"""The referral link, the affiliate's pages, the staff pages and the REST API."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.affiliates import services
from apps.affiliates.models import Affiliate, AffiliateSettings, AffiliateStatus, Commission, Payout, Referral

from .conftest import PASSWORD, pay_invoice

pytestmark = pytest.mark.django_db

D = Decimal


def reload(obj):
    obj.refresh_from_db()
    return obj


@pytest.fixture
def earned(manager, referred, affiliate, tax):
    return Commission.objects.get(invoice=pay_invoice(manager, referred[1], "1000.00"))


@pytest.fixture
def owed(manager, admin, earned):
    """The commission approved and ready to pay, with a low enough minimum."""
    services.save_settings(admin, minimum_payout=D("10"))
    return services.approve_commission(manager, earned)


# --- The referral link ------------------------------------------------------------------------------------------------

def test_the_link_sets_the_cookie_and_counts_the_visit_once(client, affiliate):
    response = client.get(f"/r/{affiliate.code}/")
    assert response.status_code == 302 and response["Location"] == "/"
    cookie = response.cookies["wp_ref"]
    assert cookie.value == affiliate.code and cookie["httponly"] and cookie["samesite"] == "Lax"
    assert int(cookie["max-age"]) == 30 * 86400
    assert reload(affiliate).visit_count == 1
    client.get(f"/r/{affiliate.code}/")  # the same browser again
    assert reload(affiliate).visit_count == 1


def test_the_link_is_case_insensitive_and_can_continue_to_a_local_page(client, affiliate):
    response = client.get(f"/r/{affiliate.code.upper()}/?to=/products/")
    assert response["Location"] == "/products/" and response.cookies["wp_ref"].value == affiliate.code


@pytest.mark.parametrize("target", ["https://evil.example/x", "//evil.example/x", "javascript:alert(1)", "evil"])
def test_the_link_never_redirects_off_site(client, affiliate, target):
    assert client.get(f"/r/{affiliate.code}/", {"to": target})["Location"] == "/"


def test_a_dead_link_looks_like_a_live_one_but_sets_nothing(client, affiliate, manager, admin):
    unknown = client.get("/r/nosuchcode/")
    assert unknown.status_code == 302 and unknown["Location"] == "/" and "wp_ref" not in unknown.cookies
    services.suspend_affiliate(manager, affiliate)
    assert "wp_ref" not in client.get(f"/r/{affiliate.code}/").cookies
    services.reactivate_affiliate(manager, affiliate)
    services.save_settings(admin, enabled=False)
    assert "wp_ref" not in client.get(f"/r/{affiliate.code}/").cookies
    assert reload(affiliate).visit_count == 0


def test_the_last_link_followed_wins(client, affiliate, config, make_customer):
    other = services.enrol(make_customer("other@example.com")[0], accept_terms=True)
    client.get(f"/r/{affiliate.code}/")
    assert client.get(f"/r/{other.code}/").cookies["wp_ref"].value == other.code


def test_registering_with_the_cookie_credits_the_affiliate_and_clears_it(client, affiliate):
    client.cookies["wp_ref"] = affiliate.code
    response = client.post("/account/register/", {
        "first_name": "Bea", "email": "bea@example.com", "password": PASSWORD, "password_confirm": PASSWORD})
    assert response.status_code == 302
    referral = Referral.objects.get(client__email="bea@example.com")
    assert referral.affiliate == affiliate
    assert response.cookies["wp_ref"].value == "" and response.cookies["wp_ref"]["max-age"] == 0


def test_registering_with_a_stale_cookie_still_works(client, affiliate):
    client.cookies["wp_ref"] = "gone-code"
    response = client.post("/account/register/", {
        "first_name": "Bea", "email": "bea@example.com", "password": PASSWORD, "password_confirm": PASSWORD})
    assert response.status_code == 302 and not Referral.objects.exists()


def test_registering_through_the_api_with_a_code(api, affiliate):
    response = api.post("/api/v1/auth/register/", {"email": "api@example.com", "password": PASSWORD,
                                                    "referral_code": affiliate.code.upper()})
    assert response.status_code == 201
    assert Referral.objects.get(client__email="api@example.com").affiliate == affiliate
    bad = api.post("/api/v1/auth/register/", {"email": "api2@example.com", "password": PASSWORD, "referral_code": "zzz"})
    assert bad.status_code == 201 and Referral.objects.count() == 1


# --- The affiliate's pages --------------------------------------------------------------------------------------------

def test_a_customer_sees_the_offer_and_joins(client, config, make_customer, manager):
    user, _ = make_customer("new@example.com")
    client.force_login(user)
    page = client.get("/account/affiliate/").content
    assert b"Earn by referring customers" in page and b"10%" in page and b"first paid invoice" in page
    assert b"Affiliate" in client.get("/account/profile/").content  # the navigation link
    missing = client.post("/account/affiliate/", {"payout_details": "x"})
    assert missing.status_code == 200 and not Affiliate.objects.exists()
    response = client.post("/account/affiliate/", {"accept_terms": "on", "payout_details": "Meezan 001"}, follow=True)
    assert b"Welcome to the affiliate programme" in response.content and b"Your referral link" in response.content
    assert Affiliate.objects.get().payout_details == "Meezan 001"


def test_an_application_awaits_approval_then_the_dashboard_appears(client, make_customer, manager):
    user, _ = make_customer("new@example.com")
    client.force_login(user)
    page = client.post("/account/affiliate/", {"accept_terms": "on"}, follow=True).content
    assert b"Thanks for applying" in page and b"being reviewed" in page and b"Your referral link" not in page
    services.approve_affiliate(manager, Affiliate.objects.get())
    assert b"Your referral link" in client.get("/account/affiliate/").content


def test_a_closed_programme_offers_nothing(client, admin, make_customer):
    services.save_settings(admin, enabled=False)
    user, _ = make_customer("new@example.com")
    client.force_login(user)
    page = client.get("/account/affiliate/").content
    assert b"not open right now" in page and b"Apply to join" not in page
    client.post("/account/affiliate/", {"accept_terms": "on"})
    assert not Affiliate.objects.exists()


def test_the_dashboard_shows_numbers_but_no_customer_identity(client, affiliate, affiliate_user, earned):
    services.count_visit(affiliate)
    client.force_login(affiliate_user)
    page = client.get("/account/affiliate/").content
    assert affiliate.code.encode() in page and b"/r/" + affiliate.code.encode() in page
    assert b"100.00" in page and b"Customer R" in page
    for secret in (b"buyer", b"buyer@example.com", b"INV-"):
        assert secret not in page
    listing = client.get("/account/affiliate/commissions/").content
    assert b"Customer R" in listing and b"buyer" not in listing and b"INV-" not in listing
    assert b"Customer R" not in client.get("/account/affiliate/commissions/?status=paid").content


def test_payout_history_and_details(client, affiliate, affiliate_user, owed, manager):
    services.record_payout(manager, affiliate, method="Bank", reference="TXN-7")
    client.force_login(affiliate_user)
    assert b"TXN-7" in client.get("/account/affiliate/payouts/").content
    client.post("/account/affiliate/details/", {"payout_details": "New account 99"})
    assert reload(affiliate).payout_details == "New account 99"
    assert b"New account 99" in client.get("/account/affiliate/").content


def test_one_affiliate_never_sees_anothers_records(client, earned, affiliate, config, make_customer):
    stranger, _ = make_customer("stranger@example.com")
    services.enrol(stranger, accept_terms=True)
    client.force_login(stranger)
    assert b"Customer R" not in client.get("/account/affiliate/").content
    assert b"Nothing here yet" in client.get("/account/affiliate/commissions/").content


def test_customer_pages_need_login(client):
    for path in ("/account/affiliate/", "/account/affiliate/commissions/", "/account/affiliate/payouts/"):
        assert client.get(path)["Location"].startswith("/account/login/")


# --- Staff pages ------------------------------------------------------------------------------------------------------

def test_the_staff_pages_are_for_people_who_view_affiliates(client, affiliate, agent, customer, manager):
    paths = ["/staff/affiliates/", "/staff/affiliates/list/", "/staff/affiliates/commissions/",
             "/staff/affiliates/payouts/", f"/staff/affiliates/{affiliate.pk}/"]
    for user in (agent, customer):
        client.force_login(user)
        assert all(client.get(p).status_code == 403 for p in paths), user.email
    client.force_login(manager)
    assert all(client.get(p).status_code == 200 for p in paths)
    assert b"/staff/affiliates/" in client.get("/account/profile/").content
    client.logout()
    assert client.get("/staff/affiliates/")["Location"].startswith("/account/login/")


def test_the_overview_report(client, manager, earned, owed, affiliate):
    services.count_visit(affiliate)
    client.force_login(manager)
    page = client.get("/staff/affiliates/").content
    assert b"Active affiliates" in page and b"Top affiliates" in page and affiliate.user.email.encode() in page
    assert b"Approved, owed (1)" in page
    assert b"Top affiliates" in client.get("/staff/affiliates/?days=30").content


def test_an_application_is_approved_from_its_page(client, config, make_customer, manager, admin):
    services.save_settings(admin, require_approval=True)
    pending = services.enrol(make_customer("new@example.com")[0], accept_terms=True)
    client.force_login(manager)
    assert b"1 application waiting for approval" in client.get("/staff/affiliates/").content
    page = client.get(f"/staff/affiliates/{pending.pk}/").content
    assert b"Approve" in page and b"Reject" in page
    client.post(f"/staff/affiliates/{pending.pk}/approve/", {"note": "Welcome"}, follow=True)
    assert reload(pending).status == AffiliateStatus.ACTIVE


def test_rejecting_needs_a_reason_and_suspending_reactivating(client, affiliate, config, make_customer, manager,
                                                              admin):
    services.save_settings(admin, require_approval=True)
    pending = services.enrol(make_customer("new@example.com")[0], accept_terms=True)
    client.force_login(manager)
    client.post(f"/staff/affiliates/{pending.pk}/reject/", {"note": ""}, follow=True)
    assert reload(pending).status == AffiliateStatus.PENDING
    client.post(f"/staff/affiliates/{pending.pk}/reject/", {"note": "Not a fit"}, follow=True)
    assert reload(pending).status == AffiliateStatus.REJECTED
    client.post(f"/staff/affiliates/{affiliate.pk}/suspend/", {"note": "Spam"}, follow=True)
    assert reload(affiliate).status == AffiliateStatus.SUSPENDED
    client.post(f"/staff/affiliates/{affiliate.pk}/reactivate/", {}, follow=True)
    assert reload(affiliate).status == AffiliateStatus.ACTIVE


def test_an_agent_cannot_act(client, affiliate, agent):
    client.force_login(agent)
    assert client.post(f"/staff/affiliates/{affiliate.pk}/suspend/", {}).status_code == 403
    assert reload(affiliate).status == AffiliateStatus.ACTIVE


def test_the_rule_and_code_are_edited_from_the_page(client, affiliate, manager):
    client.force_login(manager)
    client.post(f"/staff/affiliates/{affiliate.pk}/rule/", {"kind": "fixed", "value": "12.50"}, follow=True)
    assert services.rule_for(reload(affiliate)) == ("fixed", D("12.50"))
    assert b"Own rule" in client.get(f"/staff/affiliates/{affiliate.pk}/").content
    client.post(f"/staff/affiliates/{affiliate.pk}/rule/", {"kind": "percentage", "value": "500"}, follow=True)
    assert services.rule_for(reload(affiliate)) == ("fixed", D("12.50"))  # refused
    client.post(f"/staff/affiliates/{affiliate.pk}/rule/", {"kind": ""}, follow=True)
    assert not reload(affiliate).has_override
    client.post(f"/staff/affiliates/{affiliate.pk}/code/", {"code": "black-friday"}, follow=True)
    assert reload(affiliate).code == "black-friday"
    assert client.get("/r/black-friday/").status_code == 302


def test_commissions_are_listed_filtered_approved_and_rejected(client, manager, earned):
    client.force_login(manager)
    assert earned.code.encode() in client.get("/staff/affiliates/commissions/").content
    assert earned.code.encode() not in client.get("/staff/affiliates/commissions/?status=paid").content
    assert earned.code.encode() in client.get(f"/staff/affiliates/commissions/?q={earned.invoice.number}").content
    client.post(f"/staff/affiliates/commissions/{earned.pk}/reject/", {"note": ""}, follow=True)
    assert reload(earned).status == "pending"
    client.post(f"/staff/affiliates/commissions/{earned.pk}/approve/", follow=True)
    assert reload(earned).status == "approved"
    client.post(f"/staff/affiliates/commissions/{earned.pk}/reject/", {"note": "Self-referral"}, follow=True)
    assert reload(earned).status == "rejected"


def test_recording_a_payout_from_the_page(client, manager, owed, affiliate):
    client.force_login(manager)
    page = client.get(f"/staff/affiliates/{affiliate.pk}/").content
    assert b"Record a payout" in page and owed.code.encode() in page
    response = client.post(f"/staff/affiliates/{affiliate.pk}/payout/", {
        "method": "JazzCash", "reference": "TXN-55", "paid_on": timezone.localdate().isoformat(),
        "commissions": [str(owed.pk)]}, follow=True)
    assert b"recorded. The affiliate was told." in response.content
    payout = Payout.objects.get()
    assert (payout.method, payout.reference, payout.amount) == ("JazzCash", "TXN-55", D("100.00"))
    assert reload(owed).status == "paid"
    assert b"TXN-55" in client.get("/staff/affiliates/payouts/").content
    assert b"TXN-55" in client.get("/staff/affiliates/payouts/?q=TXN-55").content
    assert b"TXN-55" not in client.get("/staff/affiliates/payouts/?q=zzz").content
    again = client.post(f"/staff/affiliates/{affiliate.pk}/payout/", {"method": "JazzCash", "commissions": [str(owed.pk)]},
                        follow=True)
    assert Payout.objects.count() == 1 and b"Commissions to include" in again.content  # nothing left to choose


def test_a_payout_below_the_minimum_is_refused_on_the_page(client, manager, earned, affiliate):
    services.approve_commission(manager, earned)  # 100.00 approved, minimum is 50 -> allowed; raise the bar
    AffiliateSettings.objects.filter(pk=1).update(minimum_payout=D("500"))
    client.force_login(manager)
    response = client.post(f"/staff/affiliates/{affiliate.pk}/payout/", {"method": "Bank", "commissions": [str(earned.pk)]},
                           follow=True)
    assert b"The minimum payout is 500" in response.content and not Payout.objects.exists()


def test_staff_credit_a_client_from_its_profile(client, manager, affiliate, make_customer, agent):
    _, late = make_customer("late@example.com")
    client.force_login(manager)
    page = client.get(f"/staff/clients/{late.pk}/").content
    assert b"Referred by an affiliate?" in page and b"Credit this affiliate" in page
    client.post(f"/staff/affiliates/attribute/{late.pk}/", {"code": "nosuch"}, follow=True)
    assert not Referral.objects.exists()
    client.post(f"/staff/affiliates/attribute/{late.pk}/", {"code": affiliate.code.upper()}, follow=True)
    assert Referral.objects.get().client == late
    profile = client.get(f"/staff/clients/{late.pk}/").content
    assert f"Referred by {affiliate.code}".encode() in profile and b"Credit this affiliate" not in profile
    client.force_login(agent)
    assert client.post(f"/staff/affiliates/attribute/{late.pk}/", {"code": affiliate.code}).status_code == 403


def test_the_client_profile_lists_cancellations_too(client, manager, referred):
    client.force_login(manager)
    assert b"Cancellations" in client.get(f"/staff/clients/{referred[1].pk}/").content


def test_the_settings_page(client, admin, manager):
    client.force_login(manager)
    assert client.get("/staff/affiliates/settings/").status_code == 403
    client.force_login(admin)
    assert b"Save settings" in client.get("/staff/affiliates/settings/").content
    values = {"enabled": "on", "cookie_days": 45, "commission_kind": "fixed", "commission_value": "15.00",
              "recurring_months": 6, "hold_days": 7, "minimum_payout": "25.00", "program_terms": "Be nice."}
    client.post("/staff/affiliates/settings/", values)
    row = AffiliateSettings.load()
    assert (row.cookie_days, row.commission_kind, row.commission_value, row.hold_days, row.program_terms) == (
        45, "fixed", D("15.00"), 7, "Be nice.") and not row.require_approval
    bad = client.post("/staff/affiliates/settings/", {**values, "commission_kind": "percentage", "commission_value": "150"})
    assert bad.status_code == 200 and AffiliateSettings.load().commission_kind == "fixed"


# --- API -------------------------------------------------------------------------------------------------------------

def test_api_join_and_read_the_own_record(api, config, make_customer):
    user, _ = make_customer("new@example.com")
    api.force_authenticate(user)
    assert api.get("/api/v1/affiliate/").status_code == 404
    assert api.post("/api/v1/affiliate/", {"accept_terms": False}).status_code == 400
    created = api.post("/api/v1/affiliate/", {"accept_terms": True, "payout_details": "Bank 1"})
    assert created.status_code == 201
    data = created.json()
    assert data["status"] == "active" and data["referral_url"].endswith(f"/r/{data['code']}/")
    assert data["balances"]["approved"] == "0.00" and data["stats"]["visits"] == 0
    assert api.post("/api/v1/affiliate/", {"accept_terms": True}).json()["error"]["code"] == "already_affiliate"
    assert api.patch("/api/v1/affiliate/", {"payout_details": "Bank 2"}).json()["payout_details"] == "Bank 2"
    api.force_authenticate(None)
    assert api.get("/api/v1/affiliate/").status_code == 401


def test_api_staff_review_affiliates(api, affiliate, config, make_customer, manager, agent, customer, admin):
    services.save_settings(admin, require_approval=True)
    pending = services.enrol(make_customer("new@example.com")[0], accept_terms=True)
    api.force_authenticate(customer)
    assert api.get("/api/v1/affiliates/").status_code == 403
    api.force_authenticate(agent)
    assert api.get("/api/v1/affiliates/").status_code == 403
    api.force_authenticate(manager)
    listing = api.get("/api/v1/affiliates/?status=pending").json()
    assert listing["count"] == 1 and listing["results"][0]["email"] == "new@example.com"
    assert api.get("/api/v1/affiliates/?search=aff@").json()["count"] == 1
    assert api.post(f"/api/v1/affiliates/{pending.pk}/reject/", {}).status_code == 400
    assert api.post(f"/api/v1/affiliates/{pending.pk}/approve/", {"note": "Hi"}).json()["status"] == "active"
    assert api.post(f"/api/v1/affiliates/{pending.pk}/approve/", {}).json()["error"]["code"] == "invalid_status"
    assert api.post(f"/api/v1/affiliates/{affiliate.pk}/suspend/", {}).json()["status"] == "suspended"
    assert api.post(f"/api/v1/affiliates/{affiliate.pk}/reactivate/", {}).json()["status"] == "active"
    rule = api.post(f"/api/v1/affiliates/{affiliate.pk}/rule/", {"kind": "fixed", "value": "5.00"}).json()
    assert (rule["commission_kind"], rule["commission_value"]) == ("fixed", "5.00")
    assert api.post(f"/api/v1/affiliates/{affiliate.pk}/rule/", {"kind": "percentage", "value": "200"}).status_code == 400
    assert api.post(f"/api/v1/affiliates/{affiliate.pk}/code/", {"code": "my-code"}).json()["code"] == "my-code"
    api.force_authenticate(agent)
    assert api.post(f"/api/v1/affiliates/{affiliate.pk}/suspend/", {}).status_code == 403


def test_api_commissions_scope_and_review(api, earned, affiliate, affiliate_user, referred, manager, agent, admin):
    api.force_authenticate(affiliate_user)
    mine = api.get("/api/v1/commissions/").json()
    assert mine["count"] == 1
    row = mine["results"][0]
    assert row["customer"].startswith("Customer R") and "invoice_id" not in row and "needs_review" not in row
    assert api.post(f"/api/v1/commissions/{earned.pk}/approve/").status_code == 403  # an affiliate cannot approve
    api.force_authenticate(referred[0])
    assert api.get("/api/v1/commissions/").json()["count"] == 0
    assert api.get(f"/api/v1/commissions/{earned.pk}/").status_code == 404
    api.force_authenticate(agent)
    assert api.get("/api/v1/commissions/").json()["count"] == 0
    api.force_authenticate(manager)
    staff_row = api.get(f"/api/v1/commissions/{earned.pk}/").json()
    assert staff_row["invoice_id"] == earned.invoice_id and staff_row["amount"] == "100.00"
    assert api.get("/api/v1/commissions/?status=paid").json()["count"] == 0
    assert api.post(f"/api/v1/commissions/{earned.pk}/reject/", {}).status_code == 400
    assert api.post(f"/api/v1/commissions/{earned.pk}/approve/").json()["status"] == "approved"
    assert api.post(f"/api/v1/commissions/{earned.pk}/approve/").json()["error"]["code"] == "invalid_status"
    assert api.post(f"/api/v1/commissions/{earned.pk}/reject/", {"note": "Fraud"}).json()["status"] == "rejected"


def test_api_payouts(api, owed, affiliate, affiliate_user, manager, agent, referred):
    api.force_authenticate(agent)
    assert api.post(f"/api/v1/affiliates/{affiliate.pk}/payout/", {"method": "Bank"}).status_code == 403
    api.force_authenticate(manager)
    assert api.post(f"/api/v1/affiliates/{affiliate.pk}/payout/", {}).status_code == 400  # a method is required
    bad_date = api.post(f"/api/v1/affiliates/{affiliate.pk}/payout/", {
        "method": "Bank", "paid_on": (timezone.localdate() + timedelta(days=2)).isoformat()})
    assert bad_date.json()["error"]["code"] == "payout_date_invalid"
    done = api.post(f"/api/v1/affiliates/{affiliate.pk}/payout/", {"method": "Bank", "reference": "T1",
                                                                   "commission_ids": [owed.pk]})
    assert done.status_code == 201 and done.json()["amount"] == "100.00" and done.json()["reference"] == "T1"
    assert api.post(f"/api/v1/affiliates/{affiliate.pk}/payout/", {"method": "Bank"}).json()["error"]["code"] == "nothing_to_pay"
    assert api.get("/api/v1/payouts/").json()["count"] == 1
    api.force_authenticate(affiliate_user)
    assert api.get("/api/v1/payouts/").json()["count"] == 1
    api.force_authenticate(referred[0])
    assert api.get("/api/v1/payouts/").json()["count"] == 0


def test_api_settings_and_report(api, admin, manager, customer, earned):
    api.force_authenticate(customer)
    assert api.get("/api/v1/affiliate-settings/").json()["commission_kind"] == "percentage"  # applicants can see the terms
    assert api.put("/api/v1/affiliate-settings/", {}).status_code == 403
    assert api.get("/api/v1/affiliate-report/").status_code == 403
    api.force_authenticate(manager)
    assert api.patch("/api/v1/affiliate-settings/", {"hold_days": 1}).status_code == 403
    report = api.get("/api/v1/affiliate-report/").json()
    assert report["commissions"]["pending"]["count"] == 1 and report["referrals"] == 1
    assert api.get("/api/v1/affiliate-report/?days=30").status_code == 200
    api.force_authenticate(admin)
    saved = api.patch("/api/v1/affiliate-settings/", {"hold_days": 1, "commission_value": "12.50"})
    assert saved.status_code == 200 and saved.json()["hold_days"] == 1
    assert api.patch("/api/v1/affiliate-settings/", {"commission_value": "500"}).status_code == 400
    assert AffiliateSettings.load().commission_value == D("12.50")


def test_api_requires_authentication(api):
    for path in ("/api/v1/affiliates/", "/api/v1/commissions/", "/api/v1/payouts/", "/api/v1/affiliate-report/"):
        assert api.get(path).status_code == 401


def test_the_task_command_and_schedule(settings, earned):
    from io import StringIO

    from django.core.management import call_command

    from apps.affiliates import tasks

    assert tasks.approve_commissions_task() == {"approved": 0, "errors": 0}
    assert "approve-commissions" in settings.CELERY_BEAT_SCHEDULE
    out = StringIO()
    call_command("approve_commissions", stdout=out)
    assert "Approved 0" in out.getvalue()
