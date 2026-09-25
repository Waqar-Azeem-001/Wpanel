"""Re-derive what a renewal/upgrade invoice claims from the figures frozen on its ServiceChange."""
from decimal import Decimal

from apps.billing import calculations as calc
from apps.billing.models import InvoiceStatus

from .models import ChangeKind, ChangeStatus, ServiceChange

ZERO = Decimal("0.00")


def verify_change(change):
    label = f"{change.get_kind_display()} #{change.pk} ({change.service_label})"
    invoice = change.invoice
    problems = []
    if change.status == ChangeStatus.APPLIED and invoice.status not in (InvoiceStatus.PAID, InvoiceStatus.REFUNDED):
        problems.append(f"{label}: applied but its invoice {invoice.reference} is not paid.")
    if change.status == ChangeStatus.PENDING and invoice.status in (InvoiceStatus.PAID, InvoiceStatus.REFUNDED):
        problems.append(f"{label}: its invoice {invoice.reference} is paid but the change was never applied.")
    if change.kind == ChangeKind.UPGRADE:
        c = change.calculation
        try:
            term_paid, credit = Decimal(c["term_paid"]), Decimal(c["credit"])
            applied, forfeited = Decimal(c["applied_credit"]), Decimal(c["forfeited"])
            new_price, net = Decimal(c["new_price"]), Decimal(c["net_payable"])
        except (KeyError, ArithmeticError):
            return problems + [f"{label}: the stored calculation is incomplete."]
        if credit > term_paid:
            problems.append(f"{label}: the credit {credit} exceeds the valid paid value {term_paid}.")
        if c["term_days"] and c["remaining_days"] > c["term_days"]:
            problems.append(f"{label}: more days remaining than the term has.")
        if c["term_days"] and c["remaining_days"] >= 0:
            expected = min(calc.money(term_paid * c["remaining_days"] / c["term_days"]), term_paid) \
                if c["remaining_days"] > 0 else ZERO
            if expected != credit:
                problems.append(f"{label}: the credit {credit} does not follow from its inputs ({expected}).")
        if applied != min(credit, new_price) or forfeited != credit - applied or net != new_price - applied:
            problems.append(f"{label}: credit, price and net payable do not add up.")
        if invoice.discount_total != applied or invoice.subtotal != new_price:
            problems.append(f"{label}: the invoice does not show this calculation.")
    return problems


def verify_all():
    problems = []
    for change in ServiceChange.objects.select_related("invoice", "hosting_account", "domain").iterator():
        problems += verify_change(change)
    return problems
