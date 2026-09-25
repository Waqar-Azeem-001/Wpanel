"""Server-rendered billing pages: staff invoice/quote management and the customer invoice pages."""
from decimal import Decimal

import pytest
from django.core import mail

from apps.accounts.roles import Role
from apps.billing import invoicing, payments
from apps.billing.models import BillableItem, Invoice, Quote

from .conftest import lines

pytestmark = pytest.mark.django_db

D = Decimal


def form_data(*rows, **extra):
    """POST data for the line formset: rows of (description, quantity, unit_price[, taxable])."""
    data = {"lines-TOTAL_FORMS": str(len(rows) + 1), "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "100", "discount_type": "", "notes": "", **extra}
    for i, (description, qty, price, *rest) in enumerate(rows):
        data[f"lines-{i}-description"], data[f"lines-{i}-quantity"], data[f"lines-{i}-unit_price"] = description, qty, price
        if not rest or rest[0]:
            data[f"lines-{i}-taxable"] = "on"
    data[f"lines-{len(rows)}-quantity"] = "1"  # the untouched blank row
    return data


def issued(manager, client_obj, price="100.00"):
    return invoicing.issue_invoice(manager, invoicing.create_invoice(
        manager, client_obj, lines=lines(("Hosting", 1, price))))


# --- Access -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/staff/billing/invoices/", "/staff/billing/payments/", "/staff/billing/quotes/", "/staff/billing/settings/",
    "/staff/billing/billable-items/", "/account/billing/invoices/", "/account/billing/quotes/",
])
def test_pages_require_login(client, path):
    assert client.get(path).status_code == 302


def test_customers_cannot_open_staff_billing(client, owner):
    client.force_login(owner)
    for path in ("/staff/billing/invoices/", "/staff/billing/payments/", "/staff/billing/settings/"):
        assert client.get(path).status_code == 403


def test_a_support_agent_can_look_but_not_change(client, staff, manager, client_obj):
    invoice = issued(manager, client_obj)
    client.force_login(staff(Role.SUPPORT_AGENT))
    page = client.get(f"/staff/billing/invoices/{invoice.pk}/")
    assert page.status_code == 200 and b"Record a payment" not in page.content
    assert client.get("/staff/billing/invoices/new/").status_code == 403
    assert client.post(f"/staff/billing/invoices/{invoice.pk}/cancel/").status_code == 403
    assert client.post("/staff/billing/settings/", {}).status_code == 404


# --- Staff invoices ---------------------------------------------------------------------------------

def test_create_edit_and_issue_from_the_staff_pages(client, manager, client_obj, vat):
    client.force_login(manager)
    picker = client.get("/staff/billing/invoices/new/?q=acme")
    assert client_obj.email.encode() in picker.content and b"invoices/create/?client=" in picker.content

    created = client.post(f"/staff/billing/invoices/create/?client={client_obj.pk}",
                          form_data(("Hosting", 2, "50.00"), ("Setup", 1, "10.00", False), notes="Thanks"))
    invoice = Invoice.objects.get()
    assert created.status_code == 302 and created["Location"] == f"/staff/billing/invoices/{invoice.pk}/"
    assert invoice.items.count() == 2 and invoice.subtotal == D("110.00") and invoice.tax_total == D("10.00")

    edit = client.get(f"/staff/billing/invoices/{invoice.pk}/edit/")
    assert b'value="Hosting"' in edit.content and b"lines-INITIAL_FORMS" in edit.content
    data = form_data(("Hosting", 3, "50.00"), ("Setup", 1, "10.00", False))
    data["lines-INITIAL_FORMS"] = "2"
    data["lines-TOTAL_FORMS"] = "3"
    client.post(f"/staff/billing/invoices/{invoice.pk}/edit/", data)
    invoice.refresh_from_db()
    assert invoice.subtotal == D("160.00")

    client.post(f"/staff/billing/invoices/{invoice.pk}/issue/")
    invoice.refresh_from_db()
    assert invoice.number == "INV-000001"
    assert client.get(f"/staff/billing/invoices/{invoice.pk}/edit/")["Location"].endswith(f"/invoices/{invoice.pk}/")


def test_removing_a_row_by_clearing_it(client, manager, client_obj):
    client.force_login(manager)
    invoice = invoicing.create_invoice(manager, client_obj, lines=lines(("A", 1, "10"), ("B", 1, "20")))
    data = form_data(("A", 1, "10"), ("", 1, ""))
    data["lines-INITIAL_FORMS"] = "2"
    data["lines-TOTAL_FORMS"] = "2"
    client.post(f"/staff/billing/invoices/{invoice.pk}/edit/", data)
    invoice.refresh_from_db()
    assert [i.description for i in invoice.items.all()] == ["A"]


def test_a_half_filled_row_is_reported(client, manager, client_obj):
    client.force_login(manager)
    response = client.post(f"/staff/billing/invoices/create/?client={client_obj.pk}",
                           form_data(("Hosting", 1, ""), ("", 1, "5")))
    assert response.status_code == 200 and response.content.count(b"Enter a price.") == 1
    assert b"Enter a description." in response.content
    assert not Invoice.objects.exists()


def test_an_empty_invoice_is_reported(client, manager, client_obj):
    client.force_login(manager)
    response = client.post(f"/staff/billing/invoices/create/?client={client_obj.pk}", form_data())
    assert response.status_code == 200 and b"Add at least one line." in response.content


def test_list_search_and_filters(client, manager, client_obj):
    a, b = issued(manager, client_obj), issued(manager, client_obj)
    payments.record_payment(manager, a, amount="100.00")
    client.force_login(manager)
    page = client.get("/staff/billing/invoices/?status=paid")
    assert b"INV-000001" in page.content and b"INV-000002" not in page.content
    assert b"INV-000002" in client.get("/staff/billing/invoices/?q=INV-000002").content
    assert b"No invoices match." in client.get("/staff/billing/invoices/?q=zzz").content
    assert b'value="overdue"' in client.get("/staff/billing/invoices/").content
    unpaid = client.get("/staff/billing/invoices/?status=unpaid")
    assert b.number.encode() in unpaid.content and a.number.encode() not in unpaid.content


def test_record_payment_page_and_double_submit_protection(client, manager, client_obj, bank):
    invoice = issued(manager, client_obj)
    client.force_login(manager)
    page = client.get(f"/staff/billing/invoices/{invoice.pk}/")
    assert b"Record a payment" in page.content and b'name="idempotency_key"' in page.content
    import re

    key = re.search(rb'name="idempotency_key"[^>]*value="([0-9a-f]+)"', page.content) or re.search(
        rb'value="([0-9a-f]+)"[^>]*name="idempotency_key"', page.content)
    post = {"amount": "40.00", "method": bank.pk, "reference": "TT", "note": "", "idempotency_key": key.group(1).decode()}
    client.post(f"/staff/billing/invoices/{invoice.pk}/payment/", post)
    client.post(f"/staff/billing/invoices/{invoice.pk}/payment/", post)  # the browser re-sent the form
    invoice.refresh_from_db()
    assert invoice.amount_paid == D("40.00") and invoice.transactions.count() == 1
    assert client.post(f"/staff/billing/invoices/{invoice.pk}/payment/", {"amount": "abc"}).status_code == 302


def test_cancel_delete_and_refund_from_the_pages(client, manager, client_obj):
    client.force_login(manager)
    draft = invoicing.create_invoice(manager, client_obj, lines=lines(("x", 1, "5")))
    assert client.post(f"/staff/billing/invoices/{draft.pk}/delete/").status_code == 302
    assert not Invoice.objects.filter(pk=draft.pk).exists()

    unpaid = issued(manager, client_obj)
    client.post(f"/staff/billing/invoices/{unpaid.pk}/cancel/", {"reason": "Dup"})
    unpaid.refresh_from_db()
    assert unpaid.status == "cancelled"

    paid = issued(manager, client_obj)
    tx = payments.record_payment(manager, paid, amount="100.00")
    page = client.get(f"/staff/billing/invoices/{paid.pk}/")
    assert b"Refund" in page.content
    client.post(f"/staff/billing/payments/{tx.pk}/refund/", {"amount": "100.00", "next": "invoice"})
    paid.refresh_from_db()
    assert paid.status == "refunded"


def test_reported_payments_are_confirmed_from_the_payments_list(client, manager, owner, client_obj, bank):
    invoice = issued(manager, client_obj)
    tx = payments.report_payment(owner, invoice, method=bank, reference="TT-9")
    client.force_login(manager)
    page = client.get("/staff/billing/payments/?status=pending")
    assert b"TT-9" in page.content and b"Confirm" in page.content and b"Reject" in page.content
    client.post(f"/staff/billing/payments/{tx.pk}/confirm/")
    invoice.refresh_from_db()
    assert invoice.status == "paid"
    assert b"No transactions match." in client.get("/staff/billing/payments/?status=pending").content


def test_pdf_download(client, manager, client_obj):
    invoice = issued(manager, client_obj)
    client.force_login(manager)
    response = client.get(f"/staff/billing/invoices/{invoice.pk}/pdf/")
    assert response.status_code == 200 and response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF") and "INV-000001.pdf" in response["Content-Disposition"]


# --- Quotes, billable items, settings ---------------------------------------------------------------

def test_quote_pages(client, manager, client_obj):
    client.force_login(manager)
    client.post(f"/staff/billing/quotes/create/?client={client_obj.pk}",
                form_data(("Custom build", 1, "200.00"), valid_until="2099-01-01"))
    quote = Quote.objects.get()
    assert quote.valid_until.isoformat() == "2099-01-01"
    client.post(f"/staff/billing/quotes/{quote.pk}/send/")
    quote.refresh_from_db()
    assert quote.number == "QUO-000001"
    assert b"QUO-000001" in client.get("/staff/billing/quotes/").content
    assert client.get(f"/staff/billing/quotes/{quote.pk}/pdf/").content.startswith(b"%PDF")
    client.post(f"/staff/billing/quotes/{quote.pk}/cancel/")
    quote.refresh_from_db()
    assert quote.status == "cancelled"


def test_billable_item_pages(client, manager, client_obj):
    client.force_login(manager)
    client.post("/staff/billing/billable-items/add/", {"client": client_obj.pk, "description": "Migration",
                                                       "quantity": 1, "unit_price": "49.00", "taxable": "on"})
    item = BillableItem.objects.get()
    assert b"Uninvoiced" in client.get("/staff/billing/billable-items/").content
    assert client.post("/staff/billing/billable-items/add/", {"client": 99999, "description": "x", "quantity": 1,
                                                               "unit_price": "1"}).status_code == 302
    assert BillableItem.objects.count() == 1
    response = client.post(f"/staff/billing/billable-items/invoice/{client_obj.pk}/")
    invoice = Invoice.objects.get()
    assert response["Location"] == f"/staff/billing/invoices/{invoice.pk}/"
    item.refresh_from_db()
    assert item.invoice_id == invoice.pk


def test_settings_page(client, manager):
    client.force_login(manager)
    assert b'name="invoice_prefix"' in client.get("/staff/billing/settings/").content
    data = {"company_name": "Wpanel Ltd", "address": "1 Road", "email": "b@w.test", "phone": "", "tax_id": "NTN1",
            "invoice_prefix": "W-", "quote_prefix": "Q-", "payment_terms_days": 7, "quote_validity_days": 14,
            "invoice_footer": "IBAN PK00", "renewal_invoice_days": 21}
    assert client.post("/staff/billing/settings/", data).status_code == 302
    bad = client.post("/staff/billing/settings/", {**data, "payment_terms_days": 9999})
    assert bad.status_code == 200
    from apps.billing.models import BillingSettings

    row = BillingSettings.load()
    assert (row.company_name, row.invoice_prefix, row.payment_terms_days, row.renewal_invoice_days) == ("Wpanel Ltd", "W-", 7, 21)


def test_payment_method_form_can_choose_a_gateway(client, manager, provider):
    client.force_login(manager)
    client.post("/staff/billing/payment-methods/save/", {"code": "card", "name": "Card", "sort_order": 0,
                                                         "provider": provider.pk})
    from apps.billing.models import PaymentMethod

    assert PaymentMethod.objects.get(code="card").provider == provider
    assert b"Test gateway" in client.get("/staff/billing/payment-methods/").content


# --- Customer pages ---------------------------------------------------------------------------------

def test_customer_sees_and_downloads_their_invoice(client, manager, owner, customer, client_obj):
    invoice = issued(manager, client_obj)
    client.force_login(owner)
    assert b"INV-000001" in client.get("/account/billing/invoices/").content
    page = client.get(f"/account/billing/invoices/{invoice.pk}/")
    assert page.status_code == 200 and b"Pay this invoice" in page.content
    assert client.get(f"/account/billing/invoices/{invoice.pk}/pdf/").content.startswith(b"%PDF")

    client.force_login(customer)  # not a contact of this client
    assert client.get(f"/account/billing/invoices/{invoice.pk}/").status_code == 404
    assert client.get(f"/account/billing/invoices/{invoice.pk}/pdf/").status_code == 404
    assert b"INV-000001" not in client.get("/account/billing/invoices/").content


def test_customer_reports_an_offline_payment(client, manager, owner, client_obj, bank):
    invoice = issued(manager, client_obj)
    client.force_login(owner)
    client.post(f"/account/billing/invoices/{invoice.pk}/pay/", {"method": bank.code, "amount": "100.00",
                                                                  "reference": "TT-1", "note": ""})
    tx = invoice.transactions.get()
    assert tx.status == "pending" and tx.reference == "TT-1"
    page = client.get(f"/account/billing/invoices/{invoice.pk}/")
    assert b"You have reported a payment" in page.content
    bad = client.post(f"/account/billing/invoices/{invoice.pk}/pay/", {"method": "nope"}, follow=True)
    assert b"Choose a valid payment method" in bad.content


def test_customer_pays_through_the_test_gateway(client, manager, owner, client_obj, card):
    invoice = issued(manager, client_obj)
    client.force_login(owner)
    response = client.post(f"/account/billing/invoices/{invoice.pk}/pay/", {"method": card.code})
    tx = invoice.transactions.get()
    assert response.status_code == 302 and response["Location"] == f"/account/billing/test-gateway/{tx.external_id}/"
    page = client.get(response["Location"])
    assert b"Test payment gateway" in page.content and b"Pay USD 100.00" in page.content

    done = client.post(response["Location"], {"outcome": "succeeded"}, follow=True)
    invoice.refresh_from_db()
    assert invoice.status == "paid" and b"Payment received" in done.content
    assert mail.outbox[-1].subject.startswith("Payment received")


def test_declining_on_the_test_gateway_leaves_the_invoice_unpaid(client, manager, owner, client_obj, card):
    invoice = issued(manager, client_obj)
    client.force_login(owner)
    location = client.post(f"/account/billing/invoices/{invoice.pk}/pay/", {"method": card.code})["Location"]
    done = client.post(location, {"outcome": "failed"}, follow=True)
    invoice.refresh_from_db()
    assert invoice.status == "unpaid" and b"declined" in done.content
    assert invoice.transactions.get().status == "failed"


def test_the_test_gateway_page_is_private_and_switchable(client, settings, manager, owner, customer, client_obj, card):
    invoice = issued(manager, client_obj)
    client.force_login(owner)
    location = client.post(f"/account/billing/invoices/{invoice.pk}/pay/", {"method": card.code})["Location"]
    client.force_login(customer)
    assert client.get(location).status_code == 404  # not their invoice
    client.force_login(owner)
    assert client.post(location, {"outcome": "bogus"}).status_code == 404
    settings.ALLOW_TEST_PAYMENT_GATEWAY = False
    assert client.get(location).status_code == 404  # the simulator is off outside dev/test


def test_customer_accepts_and_declines_quotes(client, manager, owner, client_obj):
    first = invoicing.send_quote(manager, invoicing.create_quote(manager, client_obj, lines=lines(("A", 1, "50"))))
    second = invoicing.send_quote(manager, invoicing.create_quote(manager, client_obj, lines=lines(("B", 1, "60"))))
    client.force_login(owner)
    assert b"Accept quote" in client.get(f"/account/billing/quotes/{first.pk}/").content
    accepted = client.post(f"/account/billing/quotes/{first.pk}/accept/", follow=True)
    assert b"Invoice INV-000001" in accepted.content
    client.post(f"/account/billing/quotes/{second.pk}/decline/")
    second.refresh_from_db()
    assert second.status == "declined"
    assert client.get(f"/account/billing/quotes/{first.pk}/pdf/").content.startswith(b"%PDF")


# --- Client profile ---------------------------------------------------------------------------------

def test_client_profile_lists_the_clients_records(client, manager, client_obj, staff):
    invoice = issued(manager, client_obj)
    payments.record_payment(manager, invoice, amount="10.00")
    client.force_login(manager)
    page = client.get(f"/staff/clients/{client_obj.pk}/")
    assert b"INV-000001" in page.content and b"Payment on INV-000001" in page.content
    assert b"New invoice" in page.content

    client.force_login(staff(Role.SUPPORT_AGENT))  # view_billing but not manage_billing
    page = client.get(f"/staff/clients/{client_obj.pk}/")
    assert b"INV-000001" in page.content and b"New invoice" not in page.content


def test_tax_exempt_is_a_staff_setting(client, manager, client_obj, owner):
    client.force_login(manager)
    edit = client.get(f"/staff/clients/{client_obj.pk}/edit/")
    assert b'name="tax_exempt"' in edit.content
    client.force_login(owner)
    assert b"tax_exempt" not in client.get("/account/client/").content


def test_staff_record_the_date_received_from_the_page(client, manager, client_obj, bank):
    from datetime import timedelta

    from django.utils import timezone

    invoice = issued(manager, client_obj)
    client.force_login(manager)
    page = client.get(f"/staff/billing/invoices/{invoice.pk}/").content
    assert b"Transaction ID / reference" in page and b"Date received" in page and b"Received via / into account" in page
    url = f"/staff/billing/invoices/{invoice.pk}/payment/"
    earlier = timezone.localdate() - timedelta(days=2)
    client.post(url, {"amount": "10.00", "method": bank.pk, "reference": "TXN-1", "received_on": earlier.isoformat()})
    tomorrow = timezone.localdate() + timedelta(days=1)
    client.post(url, {"amount": "10.00", "method": bank.pk, "reference": "TXN-2", "received_on": tomorrow.isoformat()})
    client.post(url, {"amount": "10.00", "method": bank.pk, "reference": "TXN-3", "received_on": timezone.localdate().isoformat()})
    dates = {t.reference: timezone.localtime(t.occurred_at).date() for t in invoice.transactions.all()}
    assert dates == {"TXN-1": earlier, "TXN-3": timezone.localdate()}  # the future date was refused
