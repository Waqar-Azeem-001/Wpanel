"""
The proration rules, as pure functions (no database), so they can be read, tested and re-run
against stored inputs by ``verify_billing``.

The rules, stated once:

* **Days remaining** = whole calendar days from today to the paid-through date. A service that has
  expired (or expires today) has **no** remaining days and therefore **no credit**.
* **Unused value** = amount paid for the current term x days remaining / days in the term, rounded
  half-up to the cent, and **never more than the amount paid** ("valid paid value"). "Amount paid"
  excludes tax and setup fees.
* When a service is renewed before it expires, the new term is added to the current one, so the paid
  value and the term length both grow; the credit is then worth the average daily rate over the whole
  paid period. It can still never exceed what was paid.
* On an upgrade the current term is never disturbed: the new plan's own full-term price is prorated
  for the days remaining on it (the mirror of the unused-value calculation above, for the plan being
  moved *to*), the old plan's unused value is credited against that prorated amount (never below zero;
  credit beyond it is forfeited, and shown as such), and only the difference is charged. The renewal
  date does not move and does not reset - the account renews, at the new plan's price, on the date it
  already had.
"""
import calendar
from dataclasses import dataclass
from decimal import Decimal

from django.utils import timezone

from apps.billing.calculations import ZERO, money


def add_months(moment, months):
    """``moment`` moved forward ``months`` calendar months; a day that doesn't exist becomes the month's last day."""
    total = moment.month - 1 + months
    year, month = moment.year + total // 12, total % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


@dataclass(frozen=True)
class Credit:
    term_paid: Decimal
    term_days: int
    remaining_days: int
    amount: Decimal

    @property
    def formula(self):
        if self.amount == 0:
            return "No credit: nothing paid or no time remaining on the current term."
        return (f"{self.term_paid} paid for a {self.term_days}-day term x {self.remaining_days} days remaining"
                f" / {self.term_days} = {self.amount}.")


NO_CREDIT = Credit(ZERO, 0, 0, ZERO)


def unused_credit(term_paid, term_start, expires_at, *, now=None):
    """The value of the unused part of the current term (see the module docstring for the rules)."""
    term_paid = money(term_paid or 0)
    if term_paid <= 0 or term_start is None or expires_at is None:
        return NO_CREDIT
    today = timezone.localdate(now or timezone.now())
    start, end = timezone.localdate(term_start), timezone.localdate(expires_at)
    term_days = (end - start).days
    if term_days <= 0:
        return NO_CREDIT
    remaining = min((end - today).days, term_days)  # a term that has not started yet is worth all of it
    if remaining <= 0:
        return Credit(term_paid, term_days, 0, ZERO)
    amount = min(money(term_paid * remaining / term_days), term_paid)
    return Credit(term_paid, term_days, remaining, amount)


def prorated_cost(full_term_price, term_days, remaining_days):
    """What the remaining part of the current term is worth at ``full_term_price`` - the new plan's own full-term
    price at the account's billing cycle - the mirror of ``unused_credit`` above, for the plan being moved *to*
    rather than the one being moved from. A zero-length term (should not happen - see ``_require_term``) is
    charged in full rather than divided by zero."""
    if term_days <= 0:
        return money(full_term_price)
    return money(full_term_price * remaining_days / term_days)
