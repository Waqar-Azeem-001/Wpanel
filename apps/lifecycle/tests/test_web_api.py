"""The customer and staff pages, and the REST API, for cancellations and the lifecycle."""
from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts.roles import Role
from apps.domains.models import DomainStatus
from apps.hosting import services as hosting_services
from apps.hosting.models import HostingAccount, HostingStatus
from apps.lifecycle import services
from apps.lifecycle.models import CancellationRequest, CancellationStatus, LifecycleSettings

from .conftest import expire, paid_account_payment

pytestmark = pytest.mark.django_db

FORM = {"reason_code": "moving", "reason_text": "Going elsewhere", "timing": "end_of_term", "confirm": "on"}


def ask(actor, service, **kw):
    kw.setdefault("reason_code", "not_needed")
    kw.setdefault("timing", "end_of_term")
    return services.request_cancellation(actor, service, **kw)


def reload(obj):
    obj.refresh_from_db()
    return obj


# --- Customer pages ---------------------------------------------------------------------------------------------------

def test_the_service_pages_offer_cancellation_to_the_owner_only(client, account, domain, owner, billing_contact):
    client.force_login(owner)
    page = client.get(f"/account/hosting/{account.pk}/").content
    assert b"Request cancellation" in page and b"Stage:" in page
    assert b"Request cancellation" in client.get(f"/account/domains/{domain.pk}/").content
    client.force_login(billing_contact)
    assert b"Request cancellation" not in client.get(f"/account/hosting/{account.pk}/").content
    assert b"Request cancellation" not in client.get(f"/account/domains/{domain.pk}/").content


def test_an_owner_cancels_a_hosting_account_through_the_form(client, account, owner, manager):
    client.force_login(owner)
    url = f"/account/cancellations/new/hosting/{account.pk}/"
    page = client.get(url).content
    assert b"Why are you cancelling?" in page and b"When should it end?" in page
    missing = client.post(url, {**FORM, "confirm": ""})
    assert missing.status_code == 200 and not CancellationRequest.objects.exists()
    response = client.post(url, FORM)
    cr = CancellationRequest.objects.get()
    assert response.status_code == 302 and response["Location"] == f"/account/cancellations/{cr.pk}/"
    assert (cr.reason_code, cr.reason_text, cr.timing, cr.requested_by) == ("moving", "Going elsewhere", "end_of_term", owner)
    detail = client.get(response["Location"]).content
    assert b"Awaiting review" in detail and b"Withdraw this request" in detail
    assert b"Going elsewhere" in detail
    again = client.get(url)  # a second request is not started; the open one is shown
    assert again.status_code == 302 and again["Location"] == f"/account/cancellations/{cr.pk}/"


def test_the_domain_form_has_no_timing_and_ends_at_expiry(client, domain, owner):
    client.force_login(owner)
    url = f"/account/cancellations/new/domain/{domain.pk}/"
    page = client.get(url).content
    assert b"When should it end?" not in page and b"will not be renewed" in page
    client.post(url, {"reason_code": "not_needed", "reason_text": "", "confirm": "on"})
    assert CancellationRequest.objects.get().timing == "end_of_term"


def test_only_the_owner_can_submit_and_strangers_get_404(client, account, billing_contact, stranger):
    url = f"/account/cancellations/new/hosting/{account.pk}/"
    client.force_login(billing_contact)
    page = client.post(url, FORM)
    assert page.status_code == 200 and b"Only the owner of the account" in page.content
    assert not CancellationRequest.objects.exists()
    client.force_login(stranger)
    assert client.get(url).status_code == 404 and client.post(url, FORM).status_code == 404


def test_customers_list_view_and_withdraw_only_their_own(client, account, owner, billing_contact, stranger):
    cr = ask(owner, account)
    client.force_login(owner)
    assert cr.code.encode() in client.get("/account/cancellations/").content
    assert client.post(f"/account/cancellations/{cr.pk}/withdraw/", follow=True).status_code == 200
    assert reload(cr).status == CancellationStatus.WITHDRAWN
    cr = ask(owner, account)
    client.force_login(billing_contact)
    assert b"Withdraw this request" not in client.get(f"/account/cancellations/{cr.pk}/").content  # can look, not act
    client.post(f"/account/cancellations/{cr.pk}/withdraw/", follow=True)
    assert reload(cr).status == CancellationStatus.PENDING
    client.force_login(stranger)
    assert client.get(f"/account/cancellations/{cr.pk}/").status_code == 404
    assert client.post(f"/account/cancellations/{cr.pk}/withdraw/").status_code == 404
    assert cr.code.encode() not in client.get("/account/cancellations/").content


def test_the_customer_never_sees_the_reviewer_or_internal_errors(client, account, owner, manager):
    cr = services.approve(manager, ask(owner, account), note="All done")
    CancellationRequest.objects.filter(pk=cr.pk).update(last_error="secret internal failure")
    client.force_login(owner)
    page = client.get(f"/account/cancellations/{cr.pk}/").content
    assert b"All done" in page and manager.email.encode() not in page and b"secret internal failure" not in page


def test_customer_pages_need_login(client, account):
    for path in ("/account/cancellations/", f"/account/cancellations/new/hosting/{account.pk}/"):
        assert client.get(path)["Location"].startswith("/account/login/")


# --- Staff pages ------------------------------------------------------------------------------------------------------

def test_the_overview_is_for_staff_who_see_hosting_or_domains(client, account, owner, manager, agent, customer):
    expire(account, 4)
    for user in (manager, agent):
        client.force_login(user)
        page = client.get("/staff/lifecycle/")
        assert page.status_code == 200 and b"Grace period" in page.content and account.domain.encode() in page.content
    client.force_login(customer)
    assert client.get("/staff/lifecycle/").status_code == 403
    client.logout()
    assert client.get("/staff/lifecycle/")["Location"].startswith("/account/login/")


def test_the_navigation_links_to_the_lifecycle_for_staff_only(client, manager, owner):
    client.force_login(manager)
    assert b"/staff/lifecycle/" in client.get("/account/profile/").content
    client.force_login(owner)
    assert b"/staff/lifecycle/" not in client.get("/account/profile/").content


def test_the_queue_lists_and_filters_requests(client, account, domain, owner, manager):
    a = ask(owner, account)
    b = ask(owner, domain)
    services.reject(manager, b, note="No")
    client.force_login(manager)
    assert a.code.encode() in client.get("/staff/lifecycle/cancellations/").content
    assert b.code.encode() not in client.get("/staff/lifecycle/cancellations/").content  # open ones by default
    assert b.code.encode() in client.get("/staff/lifecycle/cancellations/?status=rejected").content
    assert b.code.encode() in client.get("/staff/lifecycle/cancellations/?status=all").content
    only_domains = client.get("/staff/lifecycle/cancellations/?status=all&kind=domain").content
    assert b.code.encode() in only_domains and a.code.encode() not in only_domains
    assert a.code.encode() in client.get(f"/staff/lifecycle/cancellations/?q={account.domain}").content
    assert a.code.encode() not in client.get("/staff/lifecycle/cancellations/?q=nomatch").content


def test_a_reviewer_approves_from_the_page(client, account, owner, manager):
    cr = ask(owner, account)
    client.force_login(manager)
    page = client.get(f"/staff/lifecycle/cancellations/{cr.pk}/").content
    assert b"Approve" in page and b"Decline" in page and b"Unused part of the paid term" in page
    response = client.post(f"/staff/lifecycle/cancellations/{cr.pk}/approve/", {"timing": "end_of_term", "note": "Bye"},
                           follow=True)
    assert b"Approved. The service ends on" in response.content
    cr = reload(cr)
    assert cr.status == CancellationStatus.APPROVED and cr.review_note == "Bye"
    assert reload(account).status == HostingStatus.ACTIVE


def test_approving_immediately_with_a_refund_from_the_page(client, account, owner, manager):
    tx = paid_account_payment(manager, account)
    cr = ask(owner, reload(account), timing="immediate")
    client.force_login(manager)
    page = client.get(f"/staff/lifecycle/cancellations/{cr.pk}/").content
    assert f'value="{tx.pk}"'.encode() in page and b"refundable" in page
    response = client.post(f"/staff/lifecycle/cancellations/{cr.pk}/approve/", {
        "timing": "immediate", "refund_amount": "40.00", "refund_payment": str(tx.pk), "note": ""}, follow=True)
    assert b"Approved and carried out. Refund of 40.00 recorded." in response.content
    assert reload(account).status == HostingStatus.TERMINATED
    assert reload(cr).refund_transaction.amount == 40


def test_a_refund_payment_that_is_not_offered_is_refused_by_the_form(client, account, owner, manager):
    cr = ask(owner, account, timing="immediate")
    client.force_login(manager)
    response = client.post(f"/staff/lifecycle/cancellations/{cr.pk}/approve/", {
        "timing": "immediate", "refund_amount": "5.00", "refund_payment": "99999", "note": ""}, follow=True)
    assert b"Refund from payment" in response.content and reload(cr).status == CancellationStatus.PENDING


def test_declining_needs_a_reason(client, account, owner, manager):
    cr = ask(owner, account)
    client.force_login(manager)
    client.post(f"/staff/lifecycle/cancellations/{cr.pk}/reject/", {"note": ""}, follow=True)
    assert reload(cr).status == CancellationStatus.PENDING
    mail.outbox.clear()
    client.post(f"/staff/lifecycle/cancellations/{cr.pk}/reject/", {"note": "Unpaid invoice"}, follow=True)
    assert reload(cr).status == CancellationStatus.REJECTED and "declined" in mail.outbox[0].subject


def test_an_agent_can_read_a_request_but_not_act_on_it(client, account, owner, agent):
    cr = ask(owner, account)
    client.force_login(agent)
    page = client.get(f"/staff/lifecycle/cancellations/{cr.pk}/").content
    assert b"You can view this request but not act on it." in page and b'action="/staff/lifecycle/cancellations/' not in page
    client.post(f"/staff/lifecycle/cancellations/{cr.pk}/approve/", {"timing": "immediate", "note": ""}, follow=True)
    assert reload(cr).status == CancellationStatus.PENDING and reload(account).status == HostingStatus.ACTIVE


def test_a_failed_request_shows_needs_attention_and_can_be_retried(client, account, owner, manager, monkeypatch):
    cr = services.approve(manager, ask(owner, account))
    CancellationRequest.objects.filter(pk=cr.pk).update(last_error="The server did not answer.")
    client.force_login(manager)
    assert b"The server did not answer." in client.get(f"/staff/lifecycle/cancellations/{cr.pk}/").content
    assert b"Needs attention" in client.get("/staff/lifecycle/cancellations/").content
    assert b"could not be carried out" in client.get("/staff/lifecycle/").content
    client.post(f"/staff/lifecycle/cancellations/{cr.pk}/retry/", follow=True)
    assert reload(cr).status == CancellationStatus.COMPLETED and reload(account).status == HostingStatus.TERMINATED


def test_staff_withdraw_a_request_from_the_page(client, account, owner, manager):
    cr = ask(owner, account)
    client.force_login(manager)
    client.post(f"/staff/lifecycle/cancellations/{cr.pk}/withdraw/", follow=True)
    assert reload(cr).status == CancellationStatus.WITHDRAWN


def test_staff_cancel_a_service_on_the_customers_behalf(client, account, domain, manager, agent):
    url = f"/staff/lifecycle/cancel/hosting/{account.pk}/"
    client.force_login(agent)
    assert client.get(url).status_code == 403
    client.force_login(manager)
    assert b"terminates the account now" in client.get(url).content
    unconfirmed = client.post(url, {**FORM, "timing": "immediate", "confirm": ""})
    assert unconfirmed.status_code == 200 and not CancellationRequest.objects.exists()
    client.post(url, {**FORM, "timing": "immediate"})
    cr = CancellationRequest.objects.get()
    assert cr.on_behalf and cr.status == CancellationStatus.COMPLETED and reload(account).status == HostingStatus.TERMINATED
    d_url = f"/staff/lifecycle/cancel/domain/{domain.pk}/"
    response = client.post(d_url, {"reason_code": "not_needed", "confirm": "on"})
    assert response.status_code == 302
    scheduled = CancellationRequest.objects.get(domain=domain)
    assert scheduled.status == CancellationStatus.APPROVED and reload(domain).auto_renew is False
    assert client.get("/staff/lifecycle/cancel/nonsense/1/")["Location"] == "/staff/lifecycle/"


def test_when_ending_fails_the_request_is_kept_for_a_second_try(client, account, manager, monkeypatch):
    from apps.core.exceptions import ServiceError

    def broken(*args, **kwargs):
        raise ServiceError("The server did not answer.", code="adapter_error")

    monkeypatch.setattr(hosting_services, "terminate_account", broken)
    client.force_login(manager)
    response = client.post(f"/staff/lifecycle/cancel/hosting/{account.pk}/", {**FORM, "timing": "immediate"}, follow=True)
    assert b"The server did not answer." in response.content and b"The request is saved" in response.content
    cr = CancellationRequest.objects.get()
    assert cr.status == CancellationStatus.APPROVED and cr.needs_attention and reload(account).status == HostingStatus.ACTIVE


def test_the_staff_service_pages_have_the_cancel_card(client, account, domain, manager, agent):
    client.force_login(manager)
    assert b"Cancel this service" in client.get(f"/staff/hosting/{account.pk}/").content
    assert b"Cancel this service" in client.get(f"/staff/domains/{domain.pk}/").content
    client.force_login(agent)
    assert b"Cancel this service" not in client.get(f"/staff/hosting/{account.pk}/").content  # cannot act on hosting


def test_the_settings_page_shows_saves_and_validates(client, admin, manager):
    client.force_login(manager)
    assert client.get("/staff/lifecycle/settings/").status_code == 403  # no view_settings
    client.force_login(admin)
    page = client.get("/staff/lifecycle/settings/").content
    assert b"Save timings" in page and b"Terminate accounts automatically" in page
    client.post("/staff/lifecycle/settings/", {"grace_after_days": 5, "suspend_after_days": 10, "terminate_after_days": 60,
                                               "auto_suspend": "on", "auto_terminate": "on"})
    row = LifecycleSettings.load()
    assert (row.grace_after_days, row.suspend_after_days, row.terminate_after_days) == (5, 10, 60)
    assert row.auto_terminate and not row.unsuspend_on_payment
    bad = client.post("/staff/lifecycle/settings/", {"grace_after_days": 9, "suspend_after_days": 5, "terminate_after_days": 60})
    assert bad.status_code == 200 and b"in order" in bad.content
    assert LifecycleSettings.load().grace_after_days == 5


# --- API -------------------------------------------------------------------------------------------------------------

def test_api_create_list_and_scope(api, account, owner, billing_contact, stranger, manager):
    body = {"service_type": "hosting", "service_id": account.pk, "reason_code": "too_expensive", "timing": "end_of_term"}
    api.force_authenticate(stranger)
    assert api.post("/api/v1/cancellations/", body).status_code == 404
    api.force_authenticate(billing_contact)
    assert api.post("/api/v1/cancellations/", body).status_code == 403
    api.force_authenticate(owner)
    created = api.post("/api/v1/cancellations/", body)
    assert created.status_code == 201
    data = created.json()
    assert (data["status"], data["service_type"], data["service_id"], data["service_name"]) == (
        "pending", "hosting", account.pk, account.domain)
    assert not {"requested_by", "reviewed_by", "last_error", "refund_error"} & set(data)  # staff-only fields
    assert api.post("/api/v1/cancellations/", body).json()["error"]["code"] == "already_requested"
    assert api.post("/api/v1/cancellations/", {**body, "reason_code": "bogus"}).status_code == 400
    assert api.get("/api/v1/cancellations/").json()["count"] == 1
    api.force_authenticate(stranger)
    assert api.get("/api/v1/cancellations/").json()["count"] == 0
    assert api.get(f"/api/v1/cancellations/{data['id']}/").status_code == 404
    api.force_authenticate(manager)
    staff_view = api.get(f"/api/v1/cancellations/{data['id']}/").json()
    assert staff_view["requested_by"] == owner.email and "last_error" in staff_view
    assert api.get("/api/v1/cancellations/?status=approved").json()["count"] == 0
    assert api.get("/api/v1/cancellations/?service_type=domain").json()["count"] == 0
    assert api.get("/api/v1/cancellations/?service_type=hosting").json()["count"] == 1


def test_api_a_domain_can_only_be_cancelled_at_expiry(api, domain, owner):
    api.force_authenticate(owner)
    response = api.post("/api/v1/cancellations/", {"service_type": "domain", "service_id": domain.pk,
                                                    "reason_code": "not_needed", "timing": "immediate"})
    assert response.status_code == 400 and response.json()["error"]["code"] == "timing_invalid"


def test_api_review_actions_and_permissions(api, account, domain, owner, manager, agent):
    hosting = ask(owner, account)
    other = ask(owner, domain)
    for user in (owner, agent):
        api.force_authenticate(user)
        assert api.post(f"/api/v1/cancellations/{hosting.pk}/approve/", {}).status_code == 403
        assert api.post(f"/api/v1/cancellations/{hosting.pk}/reject/", {"note": "No"}).status_code == 403
    api.force_authenticate(manager)
    assert api.post(f"/api/v1/cancellations/{hosting.pk}/reject/", {}).status_code == 400  # a reason is required
    approved = api.post(f"/api/v1/cancellations/{hosting.pk}/approve/", {"timing": "immediate", "note": "Done"})
    assert approved.status_code == 200 and approved.json()["status"] == "completed"
    assert reload(account).status == HostingStatus.TERMINATED
    assert api.post(f"/api/v1/cancellations/{hosting.pk}/approve/", {}).json()["error"]["code"] == "invalid_status"
    assert api.post(f"/api/v1/cancellations/{other.pk}/reject/", {"note": "Not now"}).json()["status"] == "rejected"


def test_api_withdraw_and_retry(api, account, owner, manager, billing_contact):
    cr = ask(owner, account)
    api.force_authenticate(billing_contact)
    assert api.post(f"/api/v1/cancellations/{cr.pk}/withdraw/").status_code == 403
    api.force_authenticate(owner)
    assert api.post(f"/api/v1/cancellations/{cr.pk}/withdraw/").json()["status"] == "withdrawn"
    cr = services.approve(manager, ask(owner, account))
    assert api.post(f"/api/v1/cancellations/{cr.pk}/retry/").status_code == 403
    api.force_authenticate(manager)
    assert api.post(f"/api/v1/cancellations/{cr.pk}/retry/").json()["status"] == "completed"


def test_api_refund_options_and_refund(api, account, owner, manager, agent):
    tx = paid_account_payment(manager, account)
    cr = ask(owner, reload(account), timing="immediate")
    api.force_authenticate(owner)
    assert api.get(f"/api/v1/cancellations/{cr.pk}/refund-options/").status_code == 403
    api.force_authenticate(agent)
    assert api.get(f"/api/v1/cancellations/{cr.pk}/refund-options/").status_code == 403
    api.force_authenticate(manager)
    options = api.get(f"/api/v1/cancellations/{cr.pk}/refund-options/").json()
    assert float(options["suggested"]) > 0
    assert [p["id"] for p in options["payments"]] == [tx.pk] and options["payments"][0]["refundable"] == str(tx.amount)
    bad = api.post(f"/api/v1/cancellations/{cr.pk}/approve/", {"refund_amount": "5.00", "refund_payment_id": 999999})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "refund_payment_invalid"
    ok = api.post(f"/api/v1/cancellations/{cr.pk}/approve/", {"refund_amount": "25.00", "refund_payment_id": tx.pk})
    assert ok.status_code == 200 and ok.json()["status"] == "completed" and ok.json()["refund_amount"] == "25.00"
    assert ok.json()["refund_transaction"] is not None


def test_api_lifecycle_settings(api, admin, manager, owner):
    api.force_authenticate(owner)
    assert api.get("/api/v1/lifecycle/settings/").status_code == 403
    api.force_authenticate(manager)
    assert api.get("/api/v1/lifecycle/settings/").status_code == 403
    api.force_authenticate(admin)
    assert api.get("/api/v1/lifecycle/settings/").json()["suspend_after_days"] == 7
    saved = api.patch("/api/v1/lifecycle/settings/", {"auto_terminate": True, "terminate_after_days": 45})
    assert saved.status_code == 200 and saved.json()["terminate_after_days"] == 45 and saved.json()["auto_terminate"]
    put = api.put("/api/v1/lifecycle/settings/", {"grace_after_days": 2, "suspend_after_days": 4, "terminate_after_days": 10,
                                                  "auto_suspend": True, "auto_terminate": False,
                                                  "unsuspend_on_payment": True})
    assert put.status_code == 200
    bad = api.patch("/api/v1/lifecycle/settings/", {"suspend_after_days": 1})
    assert bad.status_code == 400
    assert LifecycleSettings.load().suspend_after_days == 4


def test_api_overview_and_the_stage_on_services(api, account, domain, owner, manager, customer):
    expire(account, 5)
    api.force_authenticate(customer)
    assert api.get("/api/v1/lifecycle/overview/").status_code == 403
    api.force_authenticate(owner)
    assert api.get("/api/v1/lifecycle/overview/").status_code == 403
    assert api.get(f"/api/v1/hosting-accounts/{account.pk}/").json()["lifecycle_stage"] == "grace"
    assert api.get(f"/api/v1/domains/{domain.pk}/").json()["lifecycle_stage"] in ("active", "renewal_due")
    api.force_authenticate(manager)
    data = api.get("/api/v1/lifecycle/overview/").json()
    assert data["counts"]["grace"] == 1 and [row["id"] for row in data["services"]["grace"]] == [account.pk]
    rows = api.get("/api/v1/hosting-accounts/").json()["results"]
    assert [r["lifecycle_stage"] for r in rows] == ["grace"]


def test_api_requires_authentication(api, account):
    for path in ("/api/v1/cancellations/", "/api/v1/lifecycle/settings/", "/api/v1/lifecycle/overview/"):
        assert api.get(path).status_code == 401


def test_the_management_command_reports_and_supports_dry_run(account, capsys):
    from django.core.management import call_command

    expire(account, 10)
    call_command("run_lifecycle", "--dry-run")
    assert "Would have: cancelled 0, final notices 1, suspended 1, terminated 0" in capsys.readouterr().out
    assert reload(account).status == HostingStatus.ACTIVE
    call_command("run_lifecycle")
    assert "suspended 1" in capsys.readouterr().out and reload(account).status == HostingStatus.SUSPENDED


def test_the_celery_task_and_schedule(settings, account):
    from apps.lifecycle import tasks

    expire(account, 10)
    assert tasks.run_lifecycle_task()["suspended"] == 1
    assert "run-service-lifecycle" in settings.CELERY_BEAT_SCHEDULE
