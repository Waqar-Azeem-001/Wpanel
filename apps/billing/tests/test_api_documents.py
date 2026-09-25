"""Invoices, transactions, quotes, billable items, settings and the webhook over the API."""
import json
from decimal import Decimal

import pytest

from apps.accounts.roles import Role
from apps.billing import invoicing, payments
from apps.billing.models import Transaction

from .conftest import lines, signed_event

pytestmark = pytest.mark.django_db

D = Decimal
ITEM = {"description": "Hosting", "quantity": 1, "unit_price": "100.00"}


def issued(manager, client_obj, price="100.00"):
    return invoicing.issue_invoice(manager, invoicing.create_invoice(
        manager, client_obj, lines=lines(("Hosting", 1, price))))


@pytest.mark.parametrize("method,path", [
    ("get", "/api/v1/invoices/"), ("post", "/api/v1/invoices/"), ("get", "/api/v1/transactions/"),
    ("get", "/api/v1/quotes/"), ("get", "/api/v1/billable-items/"), ("get", "/api/v1/billing-settings/"),
])
def test_authentication_is_required(api, method, path):
    assert getattr(api, method)(path).status_code == 401


# --- Invoices -------------------------------------------------------------------------------------

def test_staff_create_edit_issue_and_delete_drafts(api, manager, client_obj, vat):
    api.force_authenticate(manager)
    created = api.post("/api/v1/invoices/", {"client": client_obj.pk, "items": [ITEM], "discount_type": "fixed",
                                             "discount_value": "10.00"})
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "draft" and body["number"] == "" and body["total"] == "99.00"
    assert body["items"][0]["amount"] == "100.00" and body["billing"]["company"] == "Acme Ltd"

    edited = api.put(f"/api/v1/invoices/{body['id']}/", {"items": [{**ITEM, "unit_price": "50.00"}]})
    assert edited.status_code == 200 and edited.json()["total"] == "55.00"

    issued_body = api.post(f"/api/v1/invoices/{body['id']}/issue/").json()
    assert issued_body["status"] == "unpaid" and issued_body["number"] == "INV-000001"
    assert issued_body["balance_due"] == "55.00"
    assert api.put(f"/api/v1/invoices/{body['id']}/", {"items": [ITEM]}).status_code == 400  # frozen
    assert api.delete(f"/api/v1/invoices/{body['id']}/").status_code == 400

    draft = api.post("/api/v1/invoices/", {"client": client_obj.pk, "items": [ITEM]}).json()
    assert api.delete(f"/api/v1/invoices/{draft['id']}/").status_code == 204


def test_create_validation(api, manager, client_obj):
    api.force_authenticate(manager)
    assert api.post("/api/v1/invoices/", {"items": [ITEM]}).json()["error"]["code"] == "client_required"
    assert api.post("/api/v1/invoices/", {"client": 99999, "items": [ITEM]}).status_code == 404
    assert api.post("/api/v1/invoices/", {"client": client_obj.pk, "items": []}).status_code == 400
    assert api.post("/api/v1/invoices/", {"client": client_obj.pk, "items": [{**ITEM, "unit_price": "-1"}]}
                    ).status_code == 400
    assert api.post("/api/v1/invoices/", {"client": client_obj.pk, "items": [ITEM], "discount_type": "fixed",
                                          "discount_value": "500"}).status_code == 400


def test_customers_see_only_their_own_issued_invoices_and_cannot_write(api, manager, owner, customer, client_obj):
    draft = invoicing.create_invoice(manager, client_obj, lines=lines(("x", 1, "5")))
    mine = issued(manager, client_obj)
    api.force_authenticate(owner)
    listing = api.get("/api/v1/invoices/").json()
    assert [row["id"] for row in listing["results"]] == [mine.pk]
    assert api.get(f"/api/v1/invoices/{draft.pk}/").status_code == 404
    assert api.post("/api/v1/invoices/", {"client": client_obj.pk, "items": [ITEM]}).status_code == 403
    assert api.post(f"/api/v1/invoices/{mine.pk}/cancel/").status_code == 403
    assert api.post(f"/api/v1/invoices/{mine.pk}/record-payment/", {"amount": "1"}).status_code == 403

    api.force_authenticate(customer)  # someone else's client
    assert api.get("/api/v1/invoices/").json()["results"] == []
    assert api.get(f"/api/v1/invoices/{mine.pk}/").status_code == 404
    assert api.get(f"/api/v1/invoices/{mine.pk}/pdf/").status_code == 404


def test_filters(api, manager, client_obj):
    a, b = issued(manager, client_obj), issued(manager, client_obj)
    payments.record_payment(manager, b, amount="100.00")
    api.force_authenticate(manager)
    assert [r["id"] for r in api.get("/api/v1/invoices/?status=paid").json()["results"]] == [b.pk]
    assert [r["id"] for r in api.get("/api/v1/invoices/?status=unpaid").json()["results"]] == [a.pk]
    assert api.get(f"/api/v1/invoices/?client={client_obj.pk}").json()["count"] == 2
    assert api.get("/api/v1/invoices/?q=INV-000002").json()["count"] == 1
    from datetime import timedelta

    from django.utils import timezone

    from apps.billing.models import Invoice

    Invoice.objects.filter(pk=a.pk).update(due_date=timezone.localdate() - timedelta(days=3))
    overdue = api.get("/api/v1/invoices/?status=overdue").json()["results"]
    assert [r["id"] for r in overdue] == [a.pk] and overdue[0]["display_status"] == "overdue"
    assert overdue[0]["status"] == "unpaid"  # overdue is derived, never stored


def test_record_payment_with_an_idempotency_header(api, manager, client_obj, bank):
    invoice = issued(manager, client_obj)
    api.force_authenticate(manager)
    url = f"/api/v1/invoices/{invoice.pk}/record-payment/"
    first = api.post(url, {"amount": "40.00", "method": "bank-transfer"}, HTTP_IDEMPOTENCY_KEY="k-1")
    again = api.post(url, {"amount": "40.00", "method": "bank-transfer"}, HTTP_IDEMPOTENCY_KEY="k-1")
    assert first.status_code == again.status_code == 201 and first.json()["id"] == again.json()["id"]
    assert Transaction.objects.count() == 1
    assert api.post(url, {"amount": "40.00"}, HTTP_IDEMPOTENCY_KEY="k-1").status_code == 201
    assert api.post(url, {"amount": "41.00"}, HTTP_IDEMPOTENCY_KEY="k-1").status_code == 409
    assert api.post(url, {"amount": "999.00"}).json()["error"]["code"] == "amount_exceeds_balance"
    assert api.post(url, {"amount": "5", "method": "nope"}).json()["error"]["code"] == "payment_method_invalid"
    assert api.get(f"/api/v1/invoices/{invoice.pk}/").json()["amount_paid"] == "40.00"
    assert len(api.get(f"/api/v1/invoices/{invoice.pk}/transactions/").json()) == 1


def test_customer_pays_online_and_offline(api, manager, owner, client_obj, bank, card, provider):
    invoice = issued(manager, client_obj)
    api.force_authenticate(owner)
    url = f"/api/v1/invoices/{invoice.pk}/pay/"
    online = api.post(url, {"method": "card"})
    assert online.status_code == 200 and online.json()["redirect_url"].startswith("/account/billing/test-gateway/")
    assert online.json()["transaction"]["status"] == "pending"
    offline = api.post(url, {"method": "bank-transfer", "reference": "TT-7"})
    assert offline.status_code == 201 and offline.json()["redirect_url"] is None
    assert api.post(url, {"method": "bank-transfer"}).json()["error"]["code"] == "payment_already_reported"
    assert api.post(url, {"method": "unknown"}).status_code == 400


def test_cancel_and_pdf(api, manager, client_obj, owner):
    invoice = issued(manager, client_obj)
    api.force_authenticate(owner)
    response = api.get(f"/api/v1/invoices/{invoice.pk}/pdf/")
    assert response.status_code == 200 and response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    api.force_authenticate(manager)
    assert api.post(f"/api/v1/invoices/{invoice.pk}/cancel/", {"reason": "oops"}).json()["status"] == "cancelled"


# --- Transactions ---------------------------------------------------------------------------------

def test_confirm_reject_and_refund(api, manager, owner, client_obj, bank):
    invoice = issued(manager, client_obj)
    tx = payments.report_payment(owner, invoice, method=bank)
    api.force_authenticate(owner)
    assert [r["id"] for r in api.get("/api/v1/transactions/").json()["results"]] == [tx.pk]
    assert api.post(f"/api/v1/transactions/{tx.pk}/confirm/").status_code == 403

    api.force_authenticate(manager)
    assert api.post(f"/api/v1/transactions/{tx.pk}/confirm/").json()["status"] == "succeeded"
    refund = api.post(f"/api/v1/transactions/{tx.pk}/refund/", {"amount": "25.00", "reason": "Goodwill"})
    assert refund.status_code == 201 and refund.json()["type"] == "refund"
    assert api.post(f"/api/v1/transactions/{tx.pk}/refund/", {"amount": "100.00"}).status_code == 400
    assert api.get(f"/api/v1/transactions/?invoice={invoice.pk}&type=refund").json()["count"] == 1


# --- Quotes ---------------------------------------------------------------------------------------

def test_quote_flow(api, manager, owner, client_obj):
    api.force_authenticate(manager)
    quote = api.post("/api/v1/quotes/", {"client": client_obj.pk, "items": [ITEM]}).json()
    assert quote["status"] == "draft"
    assert api.post(f"/api/v1/quotes/{quote['id']}/send/").json()["number"] == "QUO-000001"
    api.force_authenticate(owner)
    invoice = api.post(f"/api/v1/quotes/{quote['id']}/accept/").json()
    assert invoice["status"] == "unpaid" and invoice["quote_id"] == quote["id"]
    assert api.post(f"/api/v1/quotes/{quote['id']}/decline/").status_code == 400
    assert api.get(f"/api/v1/quotes/{quote['id']}/pdf/").content.startswith(b"%PDF")


# --- Billable items, settings -----------------------------------------------------------------------

def test_billable_items_then_invoice(api, manager, client_obj, staff):
    api.force_authenticate(manager)
    created = api.post("/api/v1/billable-items/", {"client": client_obj.pk, "description": "Migration",
                                                   "unit_price": "49.00"})
    assert created.status_code == 201
    invoice = api.post("/api/v1/billable-items/invoice/", {"client": client_obj.pk})
    assert invoice.status_code == 201 and invoice.json()["total"] == "49.00"
    assert api.delete(f"/api/v1/billable-items/{created.json()['id']}/").status_code == 400  # already invoiced
    api.force_authenticate(staff(Role.SUPPORT_AGENT))
    assert api.get("/api/v1/billable-items/").status_code == 200
    assert api.post("/api/v1/billable-items/", {"client": client_obj.pk, "description": "x",
                                                "unit_price": "1"}).status_code == 403


def test_settings_read_and_write(api, manager, staff, owner):
    api.force_authenticate(manager)
    saved = api.put("/api/v1/billing-settings/", {"company_name": "Wpanel Ltd", "payment_terms_days": 7,
                                                  "quote_validity_days": 14, "invoice_prefix": "W-",
                                                  "quote_prefix": "Q-"})
    assert saved.status_code == 200 and saved.json()["company_name"] == "Wpanel Ltd"
    assert api.get("/api/v1/billing-settings/").json()["payment_terms_days"] == 7
    api.force_authenticate(staff(Role.SUPPORT_AGENT))
    assert api.get("/api/v1/billing-settings/").status_code == 200
    assert api.put("/api/v1/billing-settings/", {"payment_terms_days": 1, "quote_validity_days": 1}).status_code == 403
    api.force_authenticate(owner)
    assert api.get("/api/v1/billing-settings/").status_code == 403


# --- Webhook --------------------------------------------------------------------------------------

def test_webhook_endpoint_needs_no_login_but_a_valid_signature(api, manager, owner, client_obj, card, provider):
    invoice = issued(manager, client_obj)
    tx, _ = payments.start_gateway_payment(owner, invoice, card)
    url = f"/api/v1/webhooks/payments/{provider.pk}/"
    body, headers = signed_event(provider, "evt_api", "payment.succeeded", tx.external_id, "100.00")

    bad = api.generic("POST", url, body, content_type="application/json", HTTP_X_TEST_SIGNATURE="t=1,v1=bad")
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_signature"
    tx.refresh_from_db()
    assert tx.status == "pending"

    ok = api.generic("POST", url, body, content_type="application/json",
                     HTTP_X_TEST_SIGNATURE=headers["X-Test-Signature"])
    assert ok.status_code == 200 and ok.json()["status"] == "processed"
    again = api.generic("POST", url, body, content_type="application/json",
                        HTTP_X_TEST_SIGNATURE=headers["X-Test-Signature"])
    assert again.status_code == 200 and again.json()["status"] == "duplicate"
    invoice.refresh_from_db()
    assert invoice.status == "paid" and invoice.amount_paid == D("100.00")


def test_webhook_for_an_unknown_or_inactive_provider_is_404(api, provider):
    assert api.post("/api/v1/webhooks/payments/9999/", {}, format="json").status_code == 404
    provider.is_active = False
    provider.save()
    assert api.post(f"/api/v1/webhooks/payments/{provider.pk}/", {}, format="json").status_code == 404


def test_webhook_ignores_a_session_login(api, owner, provider):
    """The endpoint is authenticated by signature alone: a CSRF-exempt session cookie does not matter."""
    api.force_authenticate(owner)
    body = json.dumps({"id": "x", "type": "payment.succeeded", "data": {}}).encode()
    response = api.generic("POST", f"/api/v1/webhooks/payments/{provider.pk}/", body, content_type="application/json")
    assert response.status_code == 400


def test_payment_methods_can_be_linked_to_a_gateway(api, manager, provider, bank):
    api.force_authenticate(manager)
    linked = api.put(f"/api/v1/payment-methods/{bank.code}/", {"code": bank.code, "name": bank.name,
                                                               "provider": provider.pk})
    assert linked.status_code == 200 and linked.json()["provider"] == provider.pk
    kept = api.put(f"/api/v1/payment-methods/{bank.code}/", {"code": bank.code, "name": "Renamed"})
    assert kept.json()["provider"] == provider.pk  # not sent, so not touched
    cleared = api.put(f"/api/v1/payment-methods/{bank.code}/", {"code": bank.code, "name": "Renamed",
                                                                "provider": None})
    assert cleared.json()["provider"] is None


def test_record_payment_accepts_the_time_received_and_refuses_the_future(api, manager, client_obj, bank):
    import datetime
    from datetime import timedelta

    from django.utils import timezone

    invoice = issued(manager, client_obj)
    api.force_authenticate(manager)
    url = f"/api/v1/invoices/{invoice.pk}/record-payment/"
    when = timezone.now() - timedelta(days=1)
    ok = api.post(url, {"amount": "20.00", "method": "bank-transfer", "reference": "TXN-9", "received_at": when.isoformat()})
    assert ok.status_code == 201 and ok.json()["reference"] == "TXN-9" and ok.json()["method_name"] == bank.name
    assert ok.json()["occurred_at"].startswith(when.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H"))
    future = api.post(url, {"amount": "5.00", "received_at": (timezone.now() + timedelta(days=3)).isoformat()})
    assert future.status_code == 400 and future.json()["error"]["code"] == "payment_date_invalid"
