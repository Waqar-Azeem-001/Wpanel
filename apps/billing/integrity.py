"""
Re-derive every stored financial figure from the permanent records and report
any that disagree ("financial calculations must be reproducible from permanent
database records"). Read-only; run it with ``manage.py verify_billing``.
"""
import re
from decimal import Decimal

from django.db.models import Sum

from . import calculations as calc
from .models import Invoice, InvoiceStatus, NumberSequence, Quote, TransactionStatus, TransactionType

ZERO = Decimal("0.00")

_extra_checks = []


def register_check(check):
    """Let another app add its own consistency check (``check() -> list[str]``) to ``verify_all``."""
    if check not in _extra_checks:
        _extra_checks.append(check)


def _check_document(doc, items):
    problems = []
    label = f"{type(doc).__name__} {doc.reference}"
    if sum((i.amount for i in items), ZERO) != doc.subtotal:
        problems.append(f"{label}: line amounts do not add up to the subtotal.")
    if sum((i.discount_amount for i in items), ZERO) != doc.discount_total:
        problems.append(f"{label}: line discounts do not add up to the discount total.")
    if sum((i.tax_amount for i in items), ZERO) != doc.tax_total:
        problems.append(f"{label}: line tax does not add up to the tax total.")
    if sum((i.total for i in items), ZERO) != doc.total:
        problems.append(f"{label}: line totals do not add up to the total.")
    if doc.subtotal - doc.discount_total + doc.tax_total != doc.total:
        problems.append(f"{label}: subtotal - discount + tax is not the total.")
    for item in items:
        if calc.money(item.unit_price * item.quantity) != item.amount:
            problems.append(f"{label}: '{item.description}' quantity x price is not its amount.")
        if item.amount - item.discount_amount + item.tax_amount != item.total:
            problems.append(f"{label}: '{item.description}' amount - discount + tax is not its total.")
    return problems


def verify_invoice(invoice):
    problems = _check_document(invoice, list(invoice.items.all()))
    label = f"Invoice {invoice.reference}"
    if invoice.order_id and invoice.total != invoice.order.total:
        problems.append(f"{label}: total differs from its order's total.")
    if invoice.status == InvoiceStatus.DRAFT:
        return problems
    if not invoice.number or not invoice.issue_date or not invoice.due_date:
        problems.append(f"{label}: an issued invoice must have a number and dates.")
    sums = {row["type"]: row["total"] for row in invoice.transactions.filter(
        status=TransactionStatus.SUCCEEDED).values("type").annotate(total=Sum("amount"))}
    paid, refunded = sums.get(TransactionType.PAYMENT) or ZERO, sums.get(TransactionType.REFUND) or ZERO
    if paid != invoice.amount_paid:
        problems.append(f"{label}: amount paid {invoice.amount_paid} differs from its transactions ({paid}).")
    if refunded != invoice.amount_refunded:
        problems.append(f"{label}: amount refunded {invoice.amount_refunded} differs from its transactions "
                        f"({refunded}).")
    if invoice.status != InvoiceStatus.CANCELLED:
        if paid > 0 and refunded >= paid:
            expected = InvoiceStatus.REFUNDED
        elif paid >= invoice.total:
            expected = InvoiceStatus.PAID
        elif paid > 0:
            expected = InvoiceStatus.PARTIALLY_PAID
        else:
            expected = InvoiceStatus.UNPAID
        if expected != invoice.status:
            problems.append(f"{label}: status is {invoice.status} but its transactions say {expected}.")
    return problems


def verify_quote(quote):
    return _check_document(quote, list(quote.items.all()))


def _sequence_problems(key, model):
    numbers = [n for n in model.objects.exclude(number="").values_list("number", flat=True)]
    values = []
    for number in numbers:
        match = re.search(r"(\d+)$", number)
        values.append(int(match.group(1)) if match else -1)
    problems = []
    last = NumberSequence.objects.filter(key=key).values_list("last_number", flat=True).first() or 0
    if sorted(values) != list(range(1, len(values) + 1)):
        problems.append(f"The {key} numbers are not a gap-free run from 1.")
    if last != len(values):
        problems.append(f"The {key} sequence counter ({last}) differs from the {len(values)} numbers issued.")
    return problems


def verify_all():
    """Every problem found across all invoices, quotes and number sequences."""
    problems = []
    for invoice in Invoice.objects.select_related("order").iterator():
        problems += verify_invoice(invoice)
    for quote in Quote.objects.iterator():
        problems += verify_quote(quote)
    problems += _sequence_problems("invoice", Invoice) + _sequence_problems("quote", Quote)
    for check in _extra_checks:
        problems += check()
    return problems
