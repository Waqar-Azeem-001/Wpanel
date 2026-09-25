"""Renewals and upgrades over the API and the server-rendered pages."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.roles import Role
from apps.billing.models import Invoice
from apps.hosting.models import HostingAccount
from apps.renewals import services
from apps.renewals.models import ChangeStatus, ServiceChange

from .conftest import pay

pytestmark = pytest.mark.django_db

D = Decimal


def reload(obj):
    obj.refresh_from_db()
    return obj


# --- API ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("post", "/api/v1/hosting-accounts/1/renew/"), ("get", "/api/v1/hosting-accounts/1/upgrade/"),
    ("post", "/api/v1/hosting-accounts/1/upgrade/"), ("put", "/api/v1/hosting-accounts/1/term/"),
    ("post", "/api/v1/domains/1/renewal-invoice/"), ("get", "/api/v1/service-changes/"),
])
def test_authentication_is_required(api, method, path):
    assert getattr(api, method)(path).status_code == 401


def test_renew_over_the_api(api, owner, account, tax):
    api.force_authenticate(owner)
    response = api.post(f"/api/v1/hosting-accounts/{account.pk}/renew/")
    body = response.json()
    assert response.status_code == 200 and body["kind"] == "renewal" and body["status"] == "pending"
    assert body["paid_value"] == "100.00" and body["invoice_number"].startswith("INV-")
    assert api.post(f"/api/v1/hosting-accounts/{account.pk}/renew/").json()["id"] == body["id"]  # idempotent
    invoice = api.get(f"/api/v1/invoices/{body['invoice_id']}/").json()
    assert invoice["total"] == "110.00"


def test_a_strangers_account_looks_like_it_does_not_exist(api, customer, account):
    api.force_authenticate(customer)
    assert api.post(f"/api/v1/hosting-accounts/{account.pk}/renew/").status_code == 404
    assert api.get(f"/api/v1/hosting-accounts/{account.pk}/upgrade/").status_code == 404
    assert api.get("/api/v1/service-changes/").json()["results"] == []


def test_upgrade_options_and_upgrade(api, owner, account, pro, basic, tax):
    api.force_authenticate(owner)
    options = api.get(f"/api/v1/hosting-accounts/{account.pk}/upgrade/").json()
    assert [o["product"] for o in options] == ["Pro"]  # a downgrade is not offered
    option = options[0]
    assert (option["credit"], option["net_payable"], option["remaining_days"], option["term_days"]) == (
        "72.60", "127.40", 265, 365)
    response = api.post(f"/api/v1/hosting-accounts/{account.pk}/upgrade/", {"product": pro.pk})
    assert response.status_code == 200 and response.json()["kind"] == "upgrade"
    invoice = api.get(f"/api/v1/invoices/{response.json()['invoice_id']}/").json()
    assert invoice["discount_total"] == "72.60" and invoice["total"] == "140.14"


def test_tampered_credit_price_and_expiry_are_ignored(api, owner, account, pro):
    api.force_authenticate(owner)
    response = api.post(f"/api/v1/hosting-accounts/{account.pk}/upgrade/", {
        "product": pro.pk, "credit": "199.99", "applied_credit": "199.99", "new_price": "0.01",
        "net_payable": "0.01", "term_paid": "999", "expires_at": "2099-01-01T00:00:00Z", "total": "0.01",
        "remaining_days": 9999})
    assert response.status_code == 200
    invoice = Invoice.objects.get()
    assert invoice.subtotal == D("200.00") and invoice.discount_total == D("72.60")
    change = ServiceChange.objects.get()
    assert change.calculation["credit"] == "72.60" and change.calculation["remaining_days"] == 265


def test_upgrade_errors_are_reported_cleanly(api, owner, account, starter, basic):
    api.force_authenticate(owner)
    url = f"/api/v1/hosting-accounts/{account.pk}/upgrade/"
    assert api.post(url, {"product": starter.pk}).json()["error"]["code"] == "same_plan"
    assert api.post(url, {"product": basic.pk}).json()["error"]["code"] == "downgrade_not_allowed"
    assert api.post(url, {"product": 99999}).status_code == 404
    assert api.post(url, {}).status_code == 400
    HostingAccount.objects.filter(pk=account.pk).update(billing_cycle="")
    assert api.get(url).json()["error"]["code"] == "term_not_set"


def test_domain_renewal_over_the_api(api, owner, customer, domain, tax):
    api.force_authenticate(owner)
    url = f"/api/v1/domains/{domain.pk}/renewal-invoice/"
    body = api.post(url, {"years": 2, "price": "0.01", "paid_value": "0.01"}).json()  # a price is never read
    assert body["period_months"] == 24 and body["paid_value"] == "28.00"
    assert api.post(url, {"years": 3}).json()["error"]["code"] == "change_pending"
    assert api.post(url, {"years": 99}).status_code == 400
    api.force_authenticate(customer)
    assert api.post(url, {"years": 1}).status_code == 404


def test_set_term_is_staff_only(api, manager, owner, staff, account):
    start = timezone.now() - timedelta(days=30)
    payload = {"billing_cycle": "monthly", "term_start": start.isoformat(),
               "expires_at": (start + timedelta(days=31)).isoformat(), "term_paid": "10.00"}
    url = f"/api/v1/hosting-accounts/{account.pk}/term/"
    api.force_authenticate(owner)
    assert api.put(url, payload).status_code == 403
    api.force_authenticate(staff(Role.SUPPORT_AGENT))
    assert api.put(url, payload).status_code == 403
    api.force_authenticate(manager)
    body = api.put(url, payload).json()
    assert body["billing_cycle"] == "monthly" and body["term_paid"] == "10.00"
    assert api.put(url, {**payload, "billing_cycle": "one_time"}).status_code == 400
    assert api.put(url, {**payload, "expires_at": start.isoformat()}).status_code == 400


def test_service_changes_list_scoping_and_actions(api, manager, owner, customer, account, pro, monkeypatch):
    change = services.create_upgrade(owner, account, pro)
    api.force_authenticate(owner)
    assert [c["id"] for c in api.get("/api/v1/service-changes/").json()["results"]] == [change.pk]
    assert api.post(f"/api/v1/service-changes/{change.pk}/retry/").status_code == 403
    api.force_authenticate(customer)
    assert api.get(f"/api/v1/service-changes/{change.pk}/").status_code == 404

    ServiceChange.objects.filter(pk=change.pk).update(status=ChangeStatus.FAILED, error="WHM unreachable")
    api.force_authenticate(owner)
    assert api.get(f"/api/v1/service-changes/{change.pk}/").json()["error"] == ""  # operational detail: staff only
    api.force_authenticate(manager)
    assert api.get(f"/api/v1/service-changes/{change.pk}/").json()["error"] == "WHM unreachable"
    assert api.get("/api/v1/service-changes/?status=failed&kind=upgrade").json()["count"] == 1
    pay_invoice = reload(change).invoice
    pay(manager, pay_invoice)  # nothing pending: the change stays failed until retried
    assert api.post(f"/api/v1/service-changes/{change.pk}/retry/").status_code == 200
    assert reload(account).product == pro
    assert api.post(f"/api/v1/service-changes/{change.pk}/dismiss/").status_code == 400


# --- Web ---------------------------------------------------------------------------------------------

def test_customer_hosting_page_shows_the_term_and_renews(client, manager, owner, account):
    client.force_login(owner)
    page = client.get(f"/account/hosting/{account.pk}/")
    assert b"Billing term" in page.content and b"Annual" in page.content
    assert b"Renew" in page.content and b"Upgrade" in page.content
    response = client.post(f"/account/renewals/hosting/{account.pk}/renew/")
    invoice = Invoice.objects.get()
    assert response["Location"] == f"/account/billing/invoices/{invoice.pk}/"
    page = client.get(response["Location"])
    assert b"Renewal: example.com" in page.content and b"takes effect when the invoice is paid in full" in page.content
    assert b"Awaiting payment" in client.get(f"/account/hosting/{account.pk}/").content
    pay(manager, invoice)
    assert b"Applied" in client.get(f"/account/billing/invoices/{invoice.pk}/").content


def test_the_upgrade_page_shows_the_calculation_and_creates_the_invoice(client, owner, account, pro, basic):
    client.force_login(owner)
    page = client.get(f"/account/renewals/hosting/{account.pk}/upgrade/")
    assert b"Pro" in page.content and b"72.60" in page.content and b"127.40" in page.content
    assert b"265 of 365 days" in page.content and b"Basic" not in page.content
    response = client.post(f"/account/renewals/hosting/{account.pk}/upgrade/", {"product": pro.pk})
    invoice = Invoice.objects.get()
    assert response["Location"] == f"/account/billing/invoices/{invoice.pk}/"
    detail = client.get(response["Location"]).content
    assert b"Credit for unused time" in detail and b"Days remaining" in detail and b"127.40" in detail
    again = client.post(f"/account/renewals/hosting/{account.pk}/upgrade/", {"product": basic.pk}, follow=True)
    assert b"already awaiting payment" in again.content


def test_customer_pages_reject_strangers_and_bad_input(client, customer, owner, account, pro):
    client.force_login(customer)
    assert client.post(f"/account/renewals/hosting/{account.pk}/renew/").status_code == 404
    assert client.get(f"/account/renewals/hosting/{account.pk}/upgrade/").status_code == 404
    client.force_login(owner)
    assert client.post(f"/account/renewals/hosting/{account.pk}/upgrade/", {"product": "abc"}).status_code == 302
    assert client.post(f"/account/renewals/hosting/{account.pk}/upgrade/", {"product": 99999}).status_code == 404
    assert not Invoice.objects.exists()
    assert client.get(f"/account/renewals/hosting/{account.pk}/renew/").status_code == 405


def test_upgrade_page_for_an_account_without_a_term(client, owner, account):
    HostingAccount.objects.filter(pk=account.pk).update(billing_cycle="", term_start=None, expires_at=None)
    client.force_login(owner)
    page = client.get(f"/account/renewals/hosting/{account.pk}/upgrade/")
    assert b"billing term has not been set up" in page.content
    assert b"being set up" in client.get(f"/account/hosting/{account.pk}/").content


def test_customer_domain_renewal(client, manager, owner, domain):
    client.force_login(owner)
    assert b"Renew (creates an invoice)" in client.get(f"/account/domains/{domain.pk}/renew/").content
    response = client.post(f"/account/renewals/domains/{domain.pk}/renew/", {"years": 2})
    invoice = Invoice.objects.get()
    assert response["Location"] == f"/account/billing/invoices/{invoice.pk}/"
    assert client.post(f"/account/renewals/domains/{domain.pk}/renew/", {"years": 0}, follow=True).status_code == 200
    assert Invoice.objects.count() == 1


def test_staff_set_the_term_and_create_invoices(client, manager, account, domain, staff):
    client.force_login(manager)
    page = client.get(f"/staff/renewals/hosting/{account.pk}/term/")
    assert page.status_code == 200 and b"Amount paid for this term" in page.content
    response = client.post(f"/staff/renewals/hosting/{account.pk}/term/", {
        "billing_cycle": "monthly", "custom_months": "", "term_start": "2026-09-01", "expires_at": "2026-10-01",
        "term_paid": "9.50"})
    assert response.status_code == 302
    reload(account)
    assert account.billing_cycle == "monthly" and account.term_paid == D("9.50")
    bad = client.post(f"/staff/renewals/hosting/{account.pk}/term/", {
        "billing_cycle": "monthly", "term_start": "2026-10-01", "expires_at": "2026-09-01", "term_paid": "1"})
    assert bad.status_code == 200 and b"after the term start" in bad.content

    detail = client.get(f"/staff/hosting/{account.pk}/")
    assert b"Billing term" in detail.content and b"Create renewal invoice" in detail.content
    r = client.post(f"/staff/renewals/hosting/{account.pk}/renew/")
    assert r["Location"].startswith("/staff/billing/invoices/")
    r = client.post(f"/staff/renewals/domains/{domain.pk}/renew/", {"years": 1})
    assert r["Location"].startswith("/staff/billing/invoices/")
    page = client.get(f"/staff/domains/{domain.pk}/")
    assert b"Create renewal invoice" in page.content and b"without an invoice" in page.content

    client.force_login(staff(Role.SUPPORT_AGENT))  # may look, may not set the term
    assert client.get(f"/staff/renewals/hosting/{account.pk}/term/").status_code == 403
    assert b"Set the billing term" not in client.get(f"/staff/hosting/{account.pk}/").content


def test_the_staff_renewals_page_and_generate_button(client, manager, account, domain):
    HostingAccount.objects.filter(pk=account.pk).update(expires_at=timezone.now() + timedelta(days=5))
    client.force_login(manager)
    page = client.get("/staff/renewals/")
    assert b"Due for renewal" in page.content and b"example.com" in page.content
    response = client.post("/staff/renewals/generate/", follow=True)
    assert b"Created 1 hosting" in response.content
    page = client.get("/staff/renewals/?status=pending")
    assert b"Awaiting payment" in page.content and b"Nothing is due" in page.content


def test_a_failed_change_is_visible_and_retryable_from_the_invoice(client, manager, owner, account, pro, monkeypatch):
    from apps.hosting.adapters import manual as manual_adapter
    from apps.hosting.adapters.base import HostingError

    change = services.create_upgrade(owner, account, pro)

    def broken(self, username, package):
        raise HostingError("WHM unreachable")

    original = manual_adapter.ManualAdapter.change_package
    monkeypatch.setattr(manual_adapter.ManualAdapter, "change_package", broken)
    pay(manager, change.invoice)

    client.force_login(owner)  # the customer sees reassurance, never the internal error
    body = client.get(f"/account/billing/invoices/{change.invoice_id}/").content
    assert b"Your payment has been received" in body and b"WHM unreachable" not in body

    client.force_login(manager)
    staff_page = client.get(f"/staff/billing/invoices/{change.invoice_id}/").content
    assert b"WHM unreachable" in staff_page and b"Retry applying" in staff_page
    assert b"needs attention" in client.get("/staff/renewals/").content

    fail = client.post(f"/staff/renewals/changes/{change.pk}/retry/", follow=True)
    assert b"still cannot be applied" in fail.content
    monkeypatch.setattr(manual_adapter.ManualAdapter, "change_package", original)
    ok = client.post(f"/staff/renewals/changes/{change.pk}/retry/", follow=True)
    assert b"Applied." in ok.content and reload(account).product == pro


def test_dismissing_a_failed_change(client, manager, owner, account, pro):
    change = services.create_upgrade(owner, account, pro)
    ServiceChange.objects.filter(pk=change.pk).update(status=ChangeStatus.FAILED)
    client.force_login(manager)
    response = client.post(f"/staff/renewals/changes/{change.pk}/dismiss/", {"note": "Refunded"}, follow=True)
    assert b"Marked as handled" in response.content and reload(change).status == ChangeStatus.VOID


def test_paying_for_a_domain_renewal_from_the_customer_pages(client, manager, owner, domain):
    client.force_login(owner)
    client.post(f"/account/renewals/domains/{domain.pk}/renew/", {"years": 1})
    before = domain.expires_at
    pay(manager, Invoice.objects.get())
    assert reload(domain).expires_at > before
