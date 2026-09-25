"""Invoice and quote lifecycle: drafts, issuing, numbering, cancelling, billable items and quotes."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.billing import integrity, invoicing, payments
from apps.billing.models import (BillableItem, BillingSettings, Invoice, InvoiceStatus, NumberSequence, Quote,
                                 QuoteStatus)
from apps.clients import services as client_services
from apps.core.exceptions import ServiceError

from .conftest import lines

pytestmark = pytest.mark.django_db

D = Decimal


def make_invoice(manager, client, *specs, **kwargs):
    return invoicing.create_invoice(manager, client, lines=lines(*(specs or (("Hosting", 1, "100.00"),))), **kwargs)


# --- Drafts --------------------------------------------------------------------------------------

def test_create_draft_computes_totals_and_lines(manager, client_obj, vat):
    invoice = make_invoice(manager, client_obj, ("Hosting", 2, "50.00"), ("Setup", 1, "10.00"),
                           discount_type="fixed", discount_value="10.00")
    assert invoice.status == InvoiceStatus.DRAFT and not invoice.number
    assert (invoice.subtotal, invoice.discount_total, invoice.tax_total, invoice.total) == (
        D("110.00"), D("10.00"), D("10.00"), D("110.00"))
    assert invoice.tax_name == "Sales tax" and invoice.tax_rate == D("10.00")
    assert [i.amount for i in invoice.items.all()] == [D("100.00"), D("10.00")]
    assert integrity.verify_invoice(invoice) == []
    assert AuditEvent.objects.filter(action="invoice.created", target_id=str(invoice.pk)).exists()


def test_percentage_discount_and_non_taxable_lines(manager, client_obj, vat):
    invoice = invoicing.create_invoice(manager, client_obj, lines=[
        {"description": "Hosting", "quantity": 1, "unit_price": "100.00"},
        {"description": "Refundable deposit", "quantity": 1, "unit_price": "50.00", "taxable": False},
    ], discount_type="percent", discount_value="20")
    assert invoice.discount_total == D("30.00") and invoice.discount_label == "Discount (20%)"
    assert invoice.tax_total == D("8.00")  # 10% of (100 - 20); the deposit is not taxed
    assert invoice.total == D("128.00")
    assert integrity.verify_invoice(invoice) == []


def test_a_tax_exempt_client_pays_no_tax(manager, client_obj, vat):
    client_services.update_client(manager, client_obj, {"tax_exempt": True})
    client_obj.refresh_from_db()
    invoice = make_invoice(manager, client_obj)
    assert invoice.tax_total == 0 and invoice.tax_name == "" and invoice.total == D("100.00")


@pytest.mark.parametrize("bad", [
    [],
    [{"description": "", "quantity": 1, "unit_price": "1"}],
    [{"description": "x", "quantity": 0, "unit_price": "1"}],
    [{"description": "x", "quantity": "two", "unit_price": "1"}],
    [{"description": "x", "quantity": 1, "unit_price": "-1"}],
    [{"description": "x", "quantity": 1, "unit_price": "abc"}],
    [{"description": "x", "quantity": 1, "unit_price": "NaN"}],
    [{"description": "x" * 300, "quantity": 1, "unit_price": "1"}],
    [{"description": "x", "quantity": 100000, "unit_price": "99999999.00"}],
])
def test_invalid_lines_are_rejected_before_anything_is_written(manager, client_obj, bad):
    with pytest.raises(ValidationError):
        invoicing.create_invoice(manager, client_obj, lines=bad)
    assert not Invoice.objects.exists()


def test_invalid_discounts_are_rejected(manager, client_obj):
    for kind, value in (("fixed", "500"), ("percent", "101"), ("fixed", "-1"), ("weird", "5"), ("fixed", "abc")):
        with pytest.raises(ValidationError):
            make_invoice(manager, client_obj, discount_type=kind, discount_value=value)
    assert not Invoice.objects.exists()


def test_only_billing_managers_can_create_invoices(customer, staff, client_obj):
    for actor in (customer, staff(Role.SUPPORT_AGENT)):
        with pytest.raises(ServiceError) as exc:
            invoicing.create_invoice(actor, client_obj, lines=lines(("x", 1, "1")))
        assert exc.value.code == "permission_denied"


def test_update_replaces_lines_of_a_draft_only(manager, client_obj, vat):
    invoice = make_invoice(manager, client_obj)
    invoicing.update_invoice(manager, invoice, lines=lines(("New", 3, "20.00")), notes="Thanks")
    invoice.refresh_from_db()
    assert invoice.items.count() == 1 and invoice.total == D("66.00") and invoice.notes == "Thanks"
    invoicing.issue_invoice(manager, invoice)
    with pytest.raises(ServiceError) as exc:
        invoicing.update_invoice(manager, invoice, lines=lines(("Sneaky", 1, "1.00")))
    assert exc.value.code == "invoice_not_draft"


def test_delete_draft_releases_billable_items(manager, client_obj):
    item = invoicing.create_billable_item(manager, client_obj, description="Migration", unit_price="49.00")
    invoice = invoicing.invoice_billable_items(manager, client_obj)
    item.refresh_from_db()
    assert item.invoice_id == invoice.pk
    invoicing.delete_draft_invoice(manager, invoice)
    item.refresh_from_db()
    assert item.invoice_id is None and not Invoice.objects.exists()


def test_an_issued_invoice_cannot_be_deleted(manager, client_obj):
    invoice = invoicing.issue_invoice(manager, make_invoice(manager, client_obj))
    with pytest.raises(ServiceError):
        invoicing.delete_draft_invoice(manager, invoice)


# --- Issuing -------------------------------------------------------------------------------------

def test_issue_assigns_a_number_dates_and_snapshot(manager, client_obj, vat):
    settings_row = BillingSettings.load()
    settings_row.payment_terms_days, settings_row.invoice_prefix = 30, "WH-"
    settings_row.save()
    invoice = make_invoice(manager, client_obj)
    mail.outbox.clear()
    issued = invoicing.issue_invoice(manager, invoice)
    today = timezone.localdate()
    assert issued.number == "WH-000001" and issued.status == InvoiceStatus.UNPAID
    assert issued.issue_date == today and issued.due_date == today + timedelta(days=30)
    assert issued.billing_company == "Acme Ltd" and issued.billing_country == "US"
    assert issued.balance_due == D("110.00")
    assert len(mail.outbox) == 1 and "WH-000001" in mail.outbox[0].subject
    client_obj.refresh_from_db()
    client_services.update_client(manager, client_obj, {"company_name": "Renamed Inc"})
    issued.refresh_from_db()
    assert issued.billing_company == "Acme Ltd"  # later edits never rewrite an issued invoice


def test_numbers_are_sequential_and_gap_free(manager, client_obj):
    first = invoicing.issue_invoice(manager, make_invoice(manager, client_obj))
    make_invoice(manager, client_obj)  # a draft takes no number
    third = invoicing.issue_invoice(manager, make_invoice(manager, client_obj))
    assert (first.number, third.number) == ("INV-000001", "INV-000002")
    assert NumberSequence.objects.get(key="invoice").last_number == 2
    assert integrity.verify_all() == []


def test_a_failed_issue_does_not_burn_a_number(manager, client_obj):
    draft = make_invoice(manager, client_obj)
    draft.items.all().delete()
    with pytest.raises(ServiceError):
        invoicing.issue_invoice(manager, draft)
    assert NumberSequence.objects.get(key="invoice").last_number == 0


def test_a_zero_total_invoice_is_settled_when_issued(manager, client_obj):
    invoice = invoicing.issue_invoice(manager, make_invoice(manager, client_obj, ("Free trial", 1, "0.00")))
    assert invoice.status == InvoiceStatus.PAID and invoice.paid_at and invoice.balance_due == 0
    assert integrity.verify_invoice(invoice) == []


def test_issue_reapplies_the_clients_current_tax_status(manager, client_obj, vat):
    invoice = make_invoice(manager, client_obj)
    assert invoice.total == D("110.00")
    client_services.update_client(manager, client_obj, {"tax_exempt": True})
    client_obj.refresh_from_db()
    issued = invoicing.issue_invoice(manager, invoice)
    assert issued.tax_total == 0 and issued.total == D("100.00")
    assert integrity.verify_invoice(issued) == []


def test_issuing_twice_is_rejected(manager, client_obj):
    invoice = invoicing.issue_invoice(manager, make_invoice(manager, client_obj))
    with pytest.raises(ServiceError) as exc:
        invoicing.issue_invoice(manager, invoice)
    assert exc.value.code == "invoice_not_draft"


# --- Cancelling ----------------------------------------------------------------------------------

def test_cancel_an_unpaid_invoice(manager, client_obj):
    invoice = invoicing.issue_invoice(manager, make_invoice(manager, client_obj))
    invoicing.cancel_invoice(manager, invoice, reason="Raised in error")
    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.CANCELLED and invoice.cancel_reason == "Raised in error"
    assert invoice.balance_due == 0 and not invoice.can_be_paid
    assert AuditEvent.objects.filter(action="invoice.cancelled").exists()
    assert integrity.verify_invoice(invoice) == []


def test_a_paid_or_part_paid_invoice_cannot_be_cancelled(manager, client_obj):
    invoice = invoicing.issue_invoice(manager, make_invoice(manager, client_obj))
    payments.record_payment(manager, invoice, amount="10.00")
    with pytest.raises(ServiceError) as exc:
        invoicing.cancel_invoice(manager, invoice)
    assert exc.value.code == "invoice_not_cancellable"


def test_cancelling_releases_billable_items(manager, client_obj):
    item = invoicing.create_billable_item(manager, client_obj, description="Migration", unit_price="49.00")
    invoice = invoicing.issue_invoice(manager, invoicing.invoice_billable_items(manager, client_obj))
    invoicing.cancel_invoice(manager, invoice)
    item.refresh_from_db()
    assert item.invoice_id is None


# --- Visibility ----------------------------------------------------------------------------------

def test_customers_see_only_their_own_issued_invoices(manager, client_obj, owner, customer, staff):
    draft = make_invoice(manager, client_obj)
    issued = invoicing.issue_invoice(manager, make_invoice(manager, client_obj))
    assert list(invoicing.visible_invoices_for_user(owner)) == [issued]
    assert list(invoicing.visible_invoices_for_user(customer)) == []
    assert set(invoicing.visible_invoices_for_user(staff(Role.SUPPORT_AGENT))) == {draft, issued}
    assert list(invoicing.visible_invoices_for_user(None)) == []


# --- Billable items ------------------------------------------------------------------------------

def test_billable_items_become_a_draft_invoice_once(manager, client_obj, vat):
    invoicing.create_billable_item(manager, client_obj, description="Migration", quantity=2, unit_price="25.00")
    invoicing.create_billable_item(manager, client_obj, description="SSL setup", unit_price="15.00", taxable=False)
    invoice = invoicing.invoice_billable_items(manager, client_obj)
    assert invoice.status == InvoiceStatus.DRAFT and invoice.subtotal == D("65.00")
    assert invoice.tax_total == D("5.00")  # only the taxable 50.00 line
    assert BillableItem.objects.filter(invoice=invoice).count() == 2
    with pytest.raises(ServiceError) as exc:
        invoicing.invoice_billable_items(manager, client_obj)  # nothing left to bill
    assert exc.value.code == "billable_items_none"


def test_an_invoiced_billable_item_cannot_be_deleted(manager, client_obj):
    item = invoicing.create_billable_item(manager, client_obj, description="Migration", unit_price="49.00")
    invoicing.invoice_billable_items(manager, client_obj, [item.pk])
    with pytest.raises(ServiceError):
        invoicing.delete_billable_item(manager, item)
    other = invoicing.create_billable_item(manager, client_obj, description="Other", unit_price="1.00")
    invoicing.delete_billable_item(manager, other)
    assert not BillableItem.objects.filter(pk=other.pk).exists()


def test_billable_item_needs_a_positive_price(manager, client_obj):
    with pytest.raises(ValidationError):
        invoicing.create_billable_item(manager, client_obj, description="Free", unit_price="0")


# --- Quotes --------------------------------------------------------------------------------------

def make_quote(manager, client, **kwargs):
    return invoicing.create_quote(manager, client, lines=lines(("Custom build", 1, "200.00")), **kwargs)


def test_send_then_accept_a_quote_issues_the_invoice(manager, client_obj, owner, vat):
    draft = make_quote(manager, client_obj)
    mail.outbox.clear()
    quote = invoicing.send_quote(manager, draft)
    assert quote.number == "QUO-000001" and quote.status == QuoteStatus.SENT
    assert quote.valid_until == timezone.localdate() + timedelta(days=30)
    assert len(mail.outbox) == 1

    invoice = invoicing.accept_quote(owner, quote)
    quote.refresh_from_db()
    assert quote.status == QuoteStatus.ACCEPTED and quote.decided_at
    assert invoice.quote_id == quote.pk and invoice.status == InvoiceStatus.UNPAID
    assert invoice.total == quote.total == D("220.00")
    assert integrity.verify_all() == []
    with pytest.raises(ServiceError):
        invoicing.accept_quote(owner, quote)  # a quote is decided once


def test_declining_an_expired_or_foreign_quote(manager, client_obj, owner, customer):
    quote = invoicing.send_quote(manager, make_quote(manager, client_obj))
    with pytest.raises(ServiceError) as exc:
        invoicing.decline_quote(customer, quote)
    assert exc.value.code == "permission_denied"
    Quote.objects.filter(pk=quote.pk).update(valid_until=timezone.localdate() - timedelta(days=1))
    quote.refresh_from_db()
    assert quote.is_expired and quote.display_status_label == "Expired"
    with pytest.raises(ServiceError) as exc:
        invoicing.accept_quote(owner, quote)
    assert exc.value.code == "quote_expired"


def test_decline_and_cancel_quotes(manager, client_obj, owner):
    declined = invoicing.decline_quote(owner, invoicing.send_quote(manager, make_quote(manager, client_obj)))
    assert declined.status == QuoteStatus.DECLINED
    draft = make_quote(manager, client_obj)
    invoicing.cancel_quote(manager, draft)
    draft.refresh_from_db()
    assert draft.status == QuoteStatus.CANCELLED
    with pytest.raises(ServiceError):
        invoicing.cancel_quote(manager, declined)


def test_a_sent_quote_is_frozen(manager, client_obj):
    quote = invoicing.send_quote(manager, make_quote(manager, client_obj))
    with pytest.raises(ServiceError) as exc:
        invoicing.update_quote(manager, quote, lines=lines(("x", 1, "1")))
    assert exc.value.code == "quote_not_draft"


def test_customers_do_not_see_draft_quotes(manager, client_obj, owner):
    draft = make_quote(manager, client_obj)
    sent = invoicing.send_quote(manager, make_quote(manager, client_obj))
    assert list(invoicing.visible_quotes_for_user(owner)) == [sent]
    assert draft not in invoicing.visible_quotes_for_user(owner)
