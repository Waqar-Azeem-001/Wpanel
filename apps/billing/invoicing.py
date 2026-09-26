"""
Invoices, quotes and billable items.

Rules the code below enforces:

* Only a *draft* can be edited. Issuing assigns the next gap-free number, fixes
  the dates, snapshots the billing details and freezes the lines and totals;
  from then on an invoice can only be paid, refunded or cancelled.
* Every figure comes from ``calculations`` and is stored on the document and its
  lines, so an invoice can be re-derived from its own records (``integrity``).
* Orders never recompute prices: the invoice for an order distributes the
  order's stored discount and tax across its lines.

All authorisation lives here (staff need ``manage_billing``; customers act only
on their own client's documents), so the API, web pages and admin stay thin.
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.accounts.roles import perm
from apps.audit import services as audit
from apps.clients.models import ClientContact
from apps.clients.services import contact_role
from apps.core import currencies
from apps.core.exceptions import ServiceError
from apps.notifications import services as notifications

from . import calculations as calc
from .models import (OPEN_STATUSES, BillableItem, BillingSettings, Invoice, InvoiceItem, InvoiceStatus, NumberSequence, Quote,
                     QuoteItem, QuoteStatus)
from .services import tax_rule_for_client
from .signals import invoice_cancelled, invoice_paid

MAX_LINES = 100
MAX_AMOUNT = Decimal("9999999999.99")


def _denied(message="You do not have permission to perform this action."):
    return ServiceError(message, code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)


def _require_manage(actor):
    if not actor.has_perm(perm("manage_billing")):
        raise _denied()


def _document_currency(requested, *, default, keep=""):
    """
    The currency of a draft: what staff chose (USD, PKR or SAR), else the client's default. Amounts are not converted, so this
    only says what the typed prices are in. A value the document or client already holds may stay even if no longer offered.
    """
    if not requested:
        return keep or default
    try:
        return currencies.clean(requested, keep=keep or default)
    except ValidationError:
        raise ServiceError(f"Choose a currency: {', '.join(currencies.CODES)}.", code="invalid_currency")


def is_staff_biller(user):
    return bool(user and user.is_authenticated and user.has_perm(perm("view_billing")))


def can_act_for_client(actor, client):
    """Staff with manage_billing, or any contact of the client, may act on that client's documents."""
    return actor.has_perm(perm("manage_billing")) or contact_role(actor, client) is not None


def _require_client_access(actor, client):
    if not can_act_for_client(actor, client):
        raise _denied()


# --- Visibility -----------------------------------------------------------------------------

def _own_clients(user):
    return ClientContact.objects.filter(user=user).values("client_id")


def visible_invoices_for_user(user):
    """Staff with view_billing see every invoice; a customer sees their clients' issued invoices only."""
    queryset = Invoice.objects.select_related("client")
    if is_staff_biller(user):
        return queryset
    if user and user.is_authenticated:
        return queryset.filter(client_id__in=_own_clients(user)).exclude(status=InvoiceStatus.DRAFT)
    return queryset.none()


def visible_quotes_for_user(user):
    queryset = Quote.objects.select_related("client")
    if is_staff_biller(user):
        return queryset
    if user and user.is_authenticated:
        return queryset.filter(client_id__in=_own_clients(user)).exclude(status=QuoteStatus.DRAFT)
    return queryset.none()


# --- Building blocks ------------------------------------------------------------------------

def billing_snapshot(client):
    """The client's billing details as they are now (frozen onto orders, invoices and quotes)."""
    return {
        "billing_name": client.contact_name[:300], "billing_company": client.company_name,
        "billing_email": client.email, "billing_phone": client.phone,
        "billing_address_line1": client.address_line1, "billing_address_line2": client.address_line2,
        "billing_city": client.city, "billing_state": client.state, "billing_postcode": client.postcode,
        "billing_country": client.country, "billing_tax_id": client.tax_id,
    }


def _next_number(key, prefix):
    """Take the next number of a sequence. Must run inside the issuing transaction."""
    try:
        sequence = NumberSequence.objects.select_for_update().get(key=key)
    except NumberSequence.DoesNotExist:
        NumberSequence.objects.get_or_create(key=key)
        sequence = NumberSequence.objects.select_for_update().get(key=key)
    sequence.last_number += 1
    sequence.save(update_fields=["last_number"])
    return f"{prefix}{sequence.last_number:06d}"


def _decimal(value, label):
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise ValidationError(f"{label} must be a number.")
    if not number.is_finite():
        raise ValidationError(f"{label} must be a number.")
    return number


def _clean_line(raw, position):
    description = str(raw.get("description", "")).strip()
    if not description:
        raise ValidationError(f"Line {position}: enter a description.")
    if len(description) > 255:
        raise ValidationError(f"Line {position}: the description is too long (255 characters at most).")
    try:
        quantity = int(raw.get("quantity", 1))
    except (TypeError, ValueError):
        raise ValidationError(f"Line {position}: quantity must be a whole number.")
    if quantity < 1 or quantity > 100000:
        raise ValidationError(f"Line {position}: quantity must be at least 1.")
    unit_price = _decimal(raw.get("unit_price", ""), f"Line {position}: unit price")
    if unit_price < 0 or unit_price > MAX_AMOUNT:
        raise ValidationError(f"Line {position}: unit price must be zero or more.")
    unit_price = calc.money(unit_price)
    amount = calc.money(unit_price * quantity)
    if amount > MAX_AMOUNT:
        raise ValidationError(f"Line {position}: the amount is too large.")
    return {"description": description, "quantity": quantity, "unit_price": unit_price, "amount": amount,
            "taxable": bool(raw.get("taxable", True)), "order_item": raw.get("order_item")}


def _clean_lines(lines):
    lines = list(lines or [])
    if not lines:
        raise ValidationError("Add at least one line.")
    if len(lines) > MAX_LINES:
        raise ValidationError(f"A document can have at most {MAX_LINES} lines.")
    return [_clean_line(raw, i) for i, raw in enumerate(lines, start=1)]


def _discount(discount_type, discount_value, subtotal, label):
    """(discount_total, label) from a staff-entered discount, or zero if none was entered."""
    if not discount_type or discount_value in (None, ""):
        return calc.ZERO, ""
    if discount_type not in ("percent", "fixed"):
        raise ValidationError("Choose a valid discount type.")
    value = _decimal(discount_value, "Discount")
    if value < 0:
        raise ValidationError("The discount cannot be negative.")
    if value == 0:
        return calc.ZERO, ""
    if discount_type == "percent" and value > 100:
        raise ValidationError("A percentage discount cannot exceed 100.")
    if discount_type == "fixed" and value > subtotal:
        raise ValidationError("The discount cannot exceed the subtotal.")
    amount = calc.discount_amount(discount_type, value, subtotal)
    if not label:
        label = f"Discount ({value.normalize():f}%)" if discount_type == "percent" else "Discount"
    return amount, label.strip()[:100]


def _build_items(model, fk_name, document, lines, totals):
    items = []
    for line, result in zip(lines, totals.lines):
        items.append(model(
            **{fk_name: document}, description=line["description"], quantity=line["quantity"],
            unit_price=line["unit_price"], amount=result.amount, discount_amount=result.discount,
            taxable=line["taxable"], tax_amount=result.tax, total=result.total,
            **({"order_item": line.get("order_item")} if model is InvoiceItem else {}),
        ))
    return items


def _apply_totals(document, totals, rule, discount_label=""):
    document.subtotal, document.discount_total = totals.subtotal, totals.discount_total
    document.discount_label = discount_label if totals.discount_total else ""
    document.tax_name = rule.name if rule and rule.rate else ""
    document.tax_rate = rule.rate if rule else calc.ZERO
    document.tax_total, document.total = totals.tax_total, totals.total


def _compute(client, lines, discount_type, discount_value, discount_label):
    """Totals for a manually built document: (totals, tax rule, discount label)."""
    subtotal = sum((line["amount"] for line in lines), calc.ZERO)
    discount_total, label = _discount(discount_type, discount_value, subtotal, discount_label)
    rule = tax_rule_for_client(client)
    totals = calc.compute([line["amount"] for line in lines], [line["taxable"] for line in lines],
                          discount_total=discount_total, tax_rate=rule.rate if rule else calc.ZERO)
    if totals.total > MAX_AMOUNT:
        raise ValidationError("The total is too large.")
    return totals, rule, label


def _save_document(document, items, fk):
    """Validate everything first, then write (never insert unvalidated input)."""
    document.full_clean()
    for item in items:
        item.full_clean(exclude=[fk])
    document.save()
    for item in items:
        setattr(item, fk, document)
    type(items[0]).objects.bulk_create(items)


# --- Invoices: drafts ------------------------------------------------------------------------

@transaction.atomic
def create_invoice(actor, client, *, lines, discount_type="", discount_value=None, discount_label="", notes="",
                   currency=None, request=None):
    """Staff: a new draft invoice for ``client`` (in ``currency``, else the client's own)."""
    _require_manage(actor)
    return create_draft_invoice(actor, client, lines=lines, discount_type=discount_type,
                                discount_value=discount_value, discount_label=discount_label, notes=notes,
                                currency=currency, request=request)


def create_draft_invoice(actor, client, *, lines, discount_type="", discount_value=None, discount_label="",
                         notes="", currency=None, request=None):
    """
    A draft invoice, with no permission check: for callers that have already authorised the action
    (renewals and upgrades, which a customer may start for their own service). Run inside a transaction.
    """
    lines = _clean_lines(lines)
    totals, rule, label = _compute(client, lines, discount_type, discount_value, discount_label)
    invoice = Invoice(client=client, created_by=actor, notes=notes.strip()[:2000],
                      currency=_document_currency(currency, default=client.currency),
                      status=InvoiceStatus.DRAFT, **billing_snapshot(client))
    _apply_totals(invoice, totals, rule, label)
    _save_document(invoice, _build_items(InvoiceItem, "invoice", invoice, lines, totals), "invoice")
    audit.record("invoice.created", actor=actor, target=invoice,
                 metadata={"client_id": client.pk, "total": str(invoice.total), "lines": len(lines),
                           "currency": invoice.currency}, request=request)
    return invoice


def _require_draft(invoice):
    if invoice.status != InvoiceStatus.DRAFT:
        raise ServiceError("Only a draft invoice can be changed.", code="invoice_not_draft")


@transaction.atomic
def update_invoice(actor, invoice, *, lines, discount_type="", discount_value=None, discount_label="", notes="",
                   currency=None, request=None):
    """Staff: replace the lines/discount/notes (and the currency) of a draft invoice."""
    _require_manage(actor)
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    _require_draft(invoice)
    lines = _clean_lines(lines)
    totals, rule, label = _compute(invoice.client, lines, discount_type, discount_value, discount_label)
    previous_currency = invoice.currency
    invoice.currency = _document_currency(currency, default=invoice.client.currency, keep=invoice.currency)
    invoice.notes = notes.strip()[:2000]
    _apply_totals(invoice, totals, rule, label)
    items = _build_items(InvoiceItem, "invoice", invoice, lines, totals)
    invoice.full_clean()
    for item in items:
        item.full_clean(exclude=["invoice"])
    invoice.save()
    invoice.items.all().delete()
    InvoiceItem.objects.bulk_create(items)
    audit.record("invoice.updated", actor=actor, target=invoice,
                 metadata={"total": str(invoice.total), "lines": len(lines), "currency": invoice.currency,
                           **({"currency_from": previous_currency} if previous_currency != invoice.currency else {})},
                 request=request)
    return invoice


@transaction.atomic
def delete_draft_invoice(actor, invoice, *, request=None):
    """Staff: discard a draft that was never issued (its billable items become billable again)."""
    _require_manage(actor)
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    _require_draft(invoice)
    reference = invoice.reference
    invoice.delete()
    audit.record("invoice.draft_deleted", actor=actor, metadata={"reference": reference}, request=request)


# --- Invoices: issue / cancel -----------------------------------------------------------------

def _issue(invoice, *, actor, snapshot=None, notify=True, request=None):
    """Assign the number, set the dates, freeze the snapshot and open the invoice (caller holds the row lock)."""
    settings_row = BillingSettings.load()
    today = timezone.localdate()
    invoice.number = _next_number("invoice", settings_row.invoice_prefix)
    invoice.issue_date = today
    invoice.due_date = today + timedelta(days=settings_row.payment_terms_days)
    for field, value in (snapshot if snapshot is not None else billing_snapshot(invoice.client)).items():
        setattr(invoice, field, value)
    invoice.status = InvoiceStatus.UNPAID
    invoice.amount_paid = invoice.amount_refunded = calc.ZERO
    settled = invoice.total == 0
    if settled:  # nothing to collect
        invoice.status, invoice.paid_at = InvoiceStatus.PAID, timezone.now()
    invoice.full_clean()
    invoice.save()
    audit.record("invoice.issued", actor=actor, target=invoice,
                 metadata={"number": invoice.number, "total": str(invoice.total), "order_id": invoice.order_id},
                 request=request)
    if settled:
        invoice_paid.send(sender=Invoice, invoice=invoice, actor=actor)
    if notify:
        notify_invoice_issued(invoice)
    return invoice


def notify_invoice_issued(invoice):
    from django.urls import reverse

    link = reverse("billing_customer:invoice_detail", args=[invoice.pk])
    notifications.dispatch_client(
        "invoice.issued", invoice.client, email=invoice.billing_email or invoice.client.email,
        title=f"Invoice {invoice.number} for {invoice.currency} {invoice.total}", link=link,
        body=f"Due {invoice.due_date:%d %b %Y}." if invoice.balance_due and invoice.due_date else "",
        context={"invoice": invoice, "items": list(invoice.items.all()), "link": link})


@transaction.atomic
def issue_invoice(actor, invoice, *, notify=True, request=None):
    """Staff: turn a draft into an issued (unpaid) invoice."""
    _require_manage(actor)
    return issue_draft_invoice(actor, invoice, notify=notify, request=request)


def issue_draft_invoice(actor, invoice, *, notify=True, request=None):
    """Issue a draft with no permission check (see ``create_draft_invoice``). Run inside a transaction."""
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    _require_draft(invoice)
    items = list(invoice.items.all())
    if not items:
        raise ServiceError("Add at least one line before issuing.", code="invoice_empty")
    # Re-derive the totals now: the client's tax status or country may have changed since the draft was saved.
    lines = [{"description": i.description, "quantity": i.quantity, "unit_price": i.unit_price, "amount": i.amount,
              "taxable": i.taxable} for i in items]
    subtotal = sum((line["amount"] for line in lines), calc.ZERO)
    rule = tax_rule_for_client(invoice.client)
    totals = calc.compute([line["amount"] for line in lines], [line["taxable"] for line in lines],
                          discount_total=min(invoice.discount_total, subtotal),
                          tax_rate=rule.rate if rule else calc.ZERO)
    _apply_totals(invoice, totals, rule, invoice.discount_label)
    for item, result in zip(items, totals.lines):
        item.discount_amount, item.tax_amount, item.total = result.discount, result.tax, result.total
        item.full_clean(exclude=["invoice"])
    InvoiceItem.objects.bulk_update(items, ["discount_amount", "tax_amount", "total"])
    return _issue(invoice, actor=actor, notify=notify, request=request)


def cancel_invoice_internal(invoice, *, actor, reason="", request=None):
    """Cancel an invoice with no payments (caller holds the row lock and has authorised the action)."""
    from .payments import fail_pending_transactions

    if invoice.status == InvoiceStatus.CANCELLED:
        return invoice
    if invoice.status not in (InvoiceStatus.DRAFT, InvoiceStatus.UNPAID):
        raise ServiceError("Only an invoice with no payments can be cancelled. Refund it instead.",
                           code="invoice_not_cancellable")
    if invoice.amount_paid > 0 or invoice.transactions.filter(status="succeeded").exists():
        raise ServiceError("This invoice has payments recorded. Refund it instead.", code="invoice_not_cancellable")
    fail_pending_transactions(invoice, "The invoice was cancelled.")
    invoice.status, invoice.cancel_reason = InvoiceStatus.CANCELLED, reason.strip()[:500]
    invoice.save(update_fields=["status", "cancel_reason", "updated_at"])
    BillableItem.objects.filter(invoice=invoice).update(invoice=None)
    audit.record("invoice.cancelled", actor=actor, target=invoice, metadata={"reason": invoice.cancel_reason},
                 request=request)
    invoice_cancelled.send(sender=Invoice, invoice=invoice, actor=actor, reason=invoice.cancel_reason)
    return invoice


@transaction.atomic
def cancel_invoice(actor, invoice, *, reason="", request=None):
    """Staff: cancel an unpaid invoice."""
    _require_manage(actor)
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    return cancel_invoice_internal(invoice, actor=actor, reason=reason, request=request)


# --- Invoices from other records --------------------------------------------------------------

def create_invoice_for_order(order, *, actor=None, notify=False, request=None):
    """
    Issue the invoice for an order (called inside the checkout transaction).

    Nothing is recomputed: the order's stored discount and tax are spread over
    its lines, so the invoice total is exactly the order total.
    """
    lines = []
    for item in order.items.all().order_by("id"):
        lines.append({"description": item.description, "quantity": 1, "unit_price": item.unit_price,
                      "amount": item.unit_price, "taxable": True, "order_item": item})
        if item.setup_fee:
            lines.append({"description": f"{item.description} - setup fee"[:255], "quantity": 1,
                          "unit_price": item.setup_fee, "amount": item.setup_fee, "taxable": True,
                          "order_item": item})
    try:
        totals = calc.distribute([line["amount"] for line in lines], [line["taxable"] for line in lines],
                                 discount_total=order.discount_total, tax_total=order.tax_total)
    except ValueError as exc:
        raise ServiceError(f"The order cannot be invoiced: {exc}", code="order_not_invoiceable")
    if totals.subtotal != order.subtotal or totals.total != order.total:
        raise ServiceError("The order's stored figures do not add up, so it cannot be invoiced.",
                           code="order_not_invoiceable")
    snapshot = {f: getattr(order, f) for f in billing_snapshot(order.client)}
    invoice = Invoice(client=order.client, order=order, created_by=actor, currency=order.currency,
                      discount_label=f"Coupon {order.coupon_code}"[:100] if order.coupon_code else "",
                      tax_name=order.tax_name, tax_rate=order.tax_rate, notes="",
                      payment_method=order.payment_method, payment_method_name=order.payment_method_name,
                      status=InvoiceStatus.DRAFT, **snapshot)
    invoice.subtotal, invoice.discount_total = totals.subtotal, totals.discount_total
    invoice.tax_total, invoice.total = totals.tax_total, totals.total
    items = _build_items(InvoiceItem, "invoice", invoice, lines, totals)
    _save_document(invoice, items, "invoice")
    return _issue(invoice, actor=actor, snapshot=snapshot, notify=notify, request=request)


# --- Billable items ---------------------------------------------------------------------------

@transaction.atomic
def create_billable_item(actor, client, *, description, quantity=1, unit_price, taxable=True, request=None):
    _require_manage(actor)
    line = _clean_line({"description": description, "quantity": quantity, "unit_price": unit_price}, 1)
    if line["unit_price"] <= 0:
        raise ValidationError("The price must be greater than zero.")
    item = BillableItem(client=client, description=line["description"], quantity=line["quantity"],
                        unit_price=line["unit_price"], taxable=bool(taxable), created_by=actor)
    item.full_clean()
    item.save()
    audit.record("billable_item.created", actor=actor, target=item,
                 metadata={"client_id": client.pk, "amount": str(item.amount)}, request=request)
    return item


@transaction.atomic
def delete_billable_item(actor, item, *, request=None):
    _require_manage(actor)
    item = BillableItem.objects.select_for_update().get(pk=item.pk)
    if item.invoice_id:
        raise ServiceError("This charge is already on an invoice.", code="billable_item_invoiced")
    audit.record("billable_item.deleted", actor=actor, target=item, metadata={"client_id": item.client_id},
                 request=request)
    item.delete()


@transaction.atomic
def invoice_billable_items(actor, client, item_ids=None, *, request=None):
    """Staff: collect a client's uninvoiced charges (all, or the chosen ones) onto a new draft invoice."""
    _require_manage(actor)
    items = BillableItem.objects.select_for_update().filter(client=client, invoice__isnull=True).order_by("id")
    if item_ids is not None:
        items = items.filter(pk__in=list(item_ids))
    items = list(items)
    if not items:
        raise ServiceError("There are no uninvoiced charges to invoice.", code="billable_items_none")
    lines = [{"description": i.description, "quantity": i.quantity, "unit_price": i.unit_price,
              "taxable": i.taxable} for i in items]
    invoice = create_invoice(actor, client, lines=lines, request=request)
    BillableItem.objects.filter(pk__in=[i.pk for i in items]).update(invoice=invoice)
    return invoice


# --- Quotes -----------------------------------------------------------------------------------

@transaction.atomic
def create_quote(actor, client, *, lines, discount_type="", discount_value=None, discount_label="", notes="",
                 valid_until=None, currency=None, request=None):
    _require_manage(actor)
    lines = _clean_lines(lines)
    totals, rule, label = _compute(client, lines, discount_type, discount_value, discount_label)
    quote = Quote(client=client, created_by=actor, currency=_document_currency(currency, default=client.currency),
                  notes=notes.strip()[:2000],
                  valid_until=valid_until, status=QuoteStatus.DRAFT, **billing_snapshot(client))
    _apply_totals(quote, totals, rule, label)
    _save_document(quote, _build_items(QuoteItem, "quote", quote, lines, totals), "quote")
    audit.record("quote.created", actor=actor, target=quote,
                 metadata={"client_id": client.pk, "total": str(quote.total), "currency": quote.currency},
                 request=request)
    return quote


def _require_quote_draft(quote):
    if quote.status != QuoteStatus.DRAFT:
        raise ServiceError("Only a draft quote can be changed.", code="quote_not_draft")


@transaction.atomic
def update_quote(actor, quote, *, lines, discount_type="", discount_value=None, discount_label="", notes="",
                 valid_until=None, currency=None, request=None):
    _require_manage(actor)
    quote = Quote.objects.select_for_update().get(pk=quote.pk)
    _require_quote_draft(quote)
    lines = _clean_lines(lines)
    totals, rule, label = _compute(quote.client, lines, discount_type, discount_value, discount_label)
    quote.currency = _document_currency(currency, default=quote.client.currency, keep=quote.currency)
    quote.notes, quote.valid_until = notes.strip()[:2000], valid_until
    _apply_totals(quote, totals, rule, label)
    items = _build_items(QuoteItem, "quote", quote, lines, totals)
    quote.full_clean()
    for item in items:
        item.full_clean(exclude=["quote"])
    quote.save()
    quote.items.all().delete()
    QuoteItem.objects.bulk_create(items)
    audit.record("quote.updated", actor=actor, target=quote,
                 metadata={"total": str(quote.total), "currency": quote.currency}, request=request)
    return quote


@transaction.atomic
def send_quote(actor, quote, *, request=None):
    """Staff: number the quote, freeze it and email it to the client."""
    _require_manage(actor)
    quote = Quote.objects.select_for_update().get(pk=quote.pk)
    _require_quote_draft(quote)
    if not quote.items.exists():
        raise ServiceError("Add at least one line before sending.", code="quote_empty")
    settings_row = BillingSettings.load()
    today = timezone.localdate()
    quote.number = _next_number("quote", settings_row.quote_prefix)
    quote.issue_date = today
    if quote.valid_until is None:
        quote.valid_until = today + timedelta(days=settings_row.quote_validity_days)
    elif quote.valid_until < today:
        raise ValidationError("The quote's validity date is in the past.")
    for field, value in billing_snapshot(quote.client).items():
        setattr(quote, field, value)
    quote.status = QuoteStatus.SENT
    quote.full_clean()
    quote.save()
    audit.record("quote.sent", actor=actor, target=quote, metadata={"number": quote.number}, request=request)
    from django.urls import reverse

    notifications.dispatch(
        "quote.sent", email=quote.billing_email or quote.client.email, user=actor,
        context={"quote": quote, "items": list(quote.items.all()),
                 "link": reverse("billing_customer:quote_detail", args=[quote.pk])})
    return quote


def _lock_quote_for_decision(actor, quote):
    quote = Quote.objects.select_for_update().get(pk=quote.pk)
    _require_client_access(actor, quote.client)
    if quote.status != QuoteStatus.SENT:
        raise ServiceError("This quote is no longer open.", code="quote_not_open")
    if quote.is_expired:
        raise ServiceError("This quote has expired.", code="quote_expired")
    return quote


@transaction.atomic
def accept_quote(actor, quote, *, request=None):
    """The client (or staff on their behalf) accepts a quote; the invoice for it is issued at once."""
    quote = _lock_quote_for_decision(actor, quote)
    quote.status, quote.decided_at = QuoteStatus.ACCEPTED, timezone.now()
    quote.save(update_fields=["status", "decided_at", "updated_at"])
    lines = [{"description": i.description, "quantity": i.quantity, "unit_price": i.unit_price,
              "amount": i.amount, "taxable": i.taxable} for i in quote.items.all()]
    totals = calc.distribute([line["amount"] for line in lines], [line["taxable"] for line in lines],
                             discount_total=quote.discount_total, tax_total=quote.tax_total)
    snapshot = {f: getattr(quote, f) for f in billing_snapshot(quote.client)}
    invoice = Invoice(client=quote.client, quote=quote, created_by=actor, currency=quote.currency,
                      discount_label=quote.discount_label, tax_name=quote.tax_name, tax_rate=quote.tax_rate,
                      notes=quote.notes, status=InvoiceStatus.DRAFT, **snapshot)
    invoice.subtotal, invoice.discount_total = totals.subtotal, totals.discount_total
    invoice.tax_total, invoice.total = totals.tax_total, totals.total
    _save_document(invoice, _build_items(InvoiceItem, "invoice", invoice, lines, totals), "invoice")
    audit.record("quote.accepted", actor=actor, target=quote, metadata={"invoice_id": invoice.pk}, request=request)
    _tell_billing_team("quote.accepted", f"Quote {quote.reference} was accepted", quote)
    return _issue(invoice, actor=actor, snapshot=snapshot, notify=True, request=request)


@transaction.atomic
def decline_quote(actor, quote, *, request=None):
    quote = _lock_quote_for_decision(actor, quote)
    quote.status, quote.decided_at = QuoteStatus.DECLINED, timezone.now()
    quote.save(update_fields=["status", "decided_at", "updated_at"])
    audit.record("quote.declined", actor=actor, target=quote, request=request)
    _tell_billing_team("quote.declined", f"Quote {quote.reference} was declined", quote)
    return quote


@transaction.atomic
def delete_draft_quote(actor, quote, *, request=None):
    """Staff: discard a quote that was never sent."""
    _require_manage(actor)
    quote = Quote.objects.select_for_update().get(pk=quote.pk)
    _require_quote_draft(quote)
    reference = quote.reference
    quote.delete()
    audit.record("quote.draft_deleted", actor=actor, metadata={"reference": reference}, request=request)


@transaction.atomic
def cancel_quote(actor, quote, *, request=None):
    _require_manage(actor)
    quote = Quote.objects.select_for_update().get(pk=quote.pk)
    if quote.status not in (QuoteStatus.DRAFT, QuoteStatus.SENT):
        raise ServiceError("This quote can no longer be cancelled.", code="quote_not_open")
    quote.status = QuoteStatus.CANCELLED
    quote.save(update_fields=["status", "updated_at"])
    audit.record("quote.cancelled", actor=actor, target=quote, request=request)
    return quote



# --- Settings -------------------------------------------------------------------------------------

@transaction.atomic
def save_billing_settings(actor, data, *, request=None):
    """Staff: update the seller details, numbering prefixes and terms printed on documents."""
    _require_manage(actor)
    row = BillingSettings.load()
    fields = ("company_name", "address", "email", "phone", "tax_id", "invoice_prefix", "quote_prefix",
              "payment_terms_days", "quote_validity_days", "invoice_footer", "renewal_invoice_days",
              "send_payment_reminders")
    changed = [f for f in fields if f in data and getattr(row, f) != data[f]]
    for field in changed:
        setattr(row, field, data[field])
    row.full_clean()
    row.save()
    if changed:
        audit.record("billing_settings.updated", actor=actor, target=row, metadata={"fields": changed},
                     request=request)
    return row


def _tell_billing_team(event, title, quote):
    from django.urls import reverse

    notifications.notify_team(event, "manage_billing", title=title, body=quote.client.display_name,
                              link=reverse("billing_staff:quote_detail", args=[quote.pk]))


# --- Search ---------------------------------------------------------------------------------------

def _search_documents(queryset, term, number_pattern):
    import re

    from django.db.models import Q

    term = (term or "").strip()
    if not term:
        return queryset
    q = (Q(number__icontains=term) | Q(client__company_name__icontains=term) | Q(client__email__icontains=term)
         | Q(billing_email__icontains=term) | Q(client__first_name__icontains=term)
         | Q(client__last_name__icontains=term))
    match = re.fullmatch(number_pattern, term)
    if match:
        q |= Q(pk=int(match.group(1)))
    return queryset.filter(q)


def search_invoices(queryset, term):
    return _search_documents(queryset, term, r"(?i)draft\s*#?(\d+)")


def search_quotes(queryset, term):
    return _search_documents(queryset, term, r"(?i)draft\s*#?(\d+)")


def filter_invoices_by_status(queryset, value):
    """Filter by a stored status, or by the derived "overdue" status."""
    if not value:
        return queryset
    if value == "overdue":
        return queryset.filter(status__in=OPEN_STATUSES, due_date__lt=timezone.localdate())
    return queryset.filter(status=value)
