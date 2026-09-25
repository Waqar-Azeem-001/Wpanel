"""
The one place billing arithmetic lives (roadmap rule 3: no duplicate billing
calculations). The cart pricing engine, invoices and quotes all use these
functions, so a figure can never be computed two different ways.

Money is ``Decimal`` rounded half-up to two places. Discount and tax are
*allocated* across document lines so that the lines always sum exactly to the
document totals - no invented or lost cents - which is what lets any invoice be
re-derived from its stored lines (see ``verify_billing``).

This module deliberately imports nothing from the rest of the project.
"""
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

TWO_PLACES = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value):
    return Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def tax_amount(taxable_base, rate):
    """Tax on ``taxable_base`` at ``rate`` percent."""
    return money(Decimal(taxable_base) * Decimal(rate) / Decimal(100))


def discount_amount(kind, value, subtotal):
    """
    The discount of type ``kind`` ("percent" or "fixed") worth ``value`` on
    ``subtotal``, never more than the subtotal.
    """
    subtotal = money(subtotal)
    if kind == "percent":
        amount = money(subtotal * Decimal(value) / Decimal(100))
    elif kind == "fixed":
        amount = money(value)
    else:
        raise ValueError(f"Unknown discount type: {kind!r}")
    return min(max(amount, ZERO), subtotal)


def allocate(total, weights):
    """
    Split ``total`` across ``weights`` proportionally, in whole cents, so the
    shares sum to exactly ``total``. Any rounding difference is handed out one
    cent at a time, largest weight first (ties: earliest line), so the result is
    deterministic.
    """
    total = money(total)
    weights = [Decimal(w) for w in weights]
    if any(w < 0 for w in weights):
        raise ValueError("Weights cannot be negative.")
    if not weights:
        if total != 0:
            raise ValueError("Nothing to allocate to.")
        return []
    if total == 0:
        return [ZERO for _ in weights]
    weight_sum = sum(weights)
    if weight_sum == 0:
        raise ValueError("Cannot allocate a non-zero amount across zero weights.")

    shares = [money(total * w / weight_sum) for w in weights]
    remainder = total - sum(shares)
    step = TWO_PLACES if remainder > 0 else -TWO_PLACES
    order = sorted(range(len(weights)), key=lambda i: (-weights[i], i))
    guard, i = 0, 0
    while remainder != 0:
        idx = order[i % len(order)]
        if weights[idx] > 0 and (step > 0 or shares[idx] + step >= 0):
            shares[idx] += step
            remainder -= step
        i += 1
        guard += 1
        if guard > len(order) * 1000:  # pragma: no cover - cannot happen for valid input
            raise RuntimeError("Could not allocate the rounding difference.")
    return shares


@dataclass
class LineResult:
    amount: Decimal
    discount: Decimal
    tax: Decimal

    @property
    def total(self):
        return self.amount - self.discount + self.tax


@dataclass
class DocumentTotals:
    lines: list = field(default_factory=list)
    subtotal: Decimal = ZERO
    discount_total: Decimal = ZERO
    tax_total: Decimal = ZERO

    @property
    def total(self):
        return self.subtotal - self.discount_total + self.tax_total


def distribute(amounts, taxable, *, discount_total, tax_total):
    """
    Spread already-decided ``discount_total`` and ``tax_total`` over the lines.
    Discount is shared in proportion to each line's amount; tax in proportion to
    each taxable line's amount after its discount. Used directly when the totals
    are already fixed (an order's snapshot becoming an invoice).
    """
    amounts = [money(a) for a in amounts]
    subtotal = sum(amounts, ZERO)
    discount_total = money(discount_total)
    if discount_total > subtotal:
        raise ValueError("The discount cannot exceed the subtotal.")
    discounts = allocate(discount_total, amounts)
    bases = [(a - d) if t else ZERO for a, d, t in zip(amounts, discounts, taxable)]
    taxes = allocate(tax_total, bases)
    return DocumentTotals(
        lines=[LineResult(a, d, t) for a, d, t in zip(amounts, discounts, taxes)],
        subtotal=subtotal, discount_total=discount_total, tax_total=money(tax_total),
    )


def compute(amounts, taxable, *, discount_total=ZERO, tax_rate=ZERO):
    """Work out the tax for a document from a rate, then distribute discount and tax over its lines."""
    amounts = [money(a) for a in amounts]
    discount_total = money(discount_total)
    if discount_total > sum(amounts, ZERO):
        raise ValueError("The discount cannot exceed the subtotal.")
    discounts = allocate(discount_total, amounts)
    base = sum(((a - d) for a, d, t in zip(amounts, discounts, taxable) if t), ZERO)
    return distribute(amounts, taxable, discount_total=discount_total, tax_total=tax_amount(base, tax_rate))
