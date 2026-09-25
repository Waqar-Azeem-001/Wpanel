"""The proration rules as pure functions."""
from datetime import datetime, timedelta, timezone as tz
from decimal import Decimal

import pytest

from apps.renewals.proration import NO_CREDIT, add_months, unused_credit

D = Decimal
NOW = datetime(2026, 6, 15, 12, 0, tzinfo=tz.utc)


@pytest.mark.parametrize("start,months,expected", [
    ((2026, 1, 31), 1, (2026, 2, 28)),
    ((2024, 1, 31), 1, (2024, 2, 29)),
    ((2026, 3, 15), 12, (2027, 3, 15)),
    ((2026, 11, 30), 3, (2027, 2, 28)),
    ((2026, 12, 1), 1, (2027, 1, 1)),
    ((2026, 5, 31), 24, (2028, 5, 31)),
])
def test_add_months_clamps_to_the_end_of_the_month(start, months, expected):
    moved = add_months(datetime(*start, 9, 30, tzinfo=tz.utc), months)
    assert (moved.year, moved.month, moved.day) == expected and (moved.hour, moved.minute) == (9, 30)


def term(days_ago, length=365):
    start = NOW - timedelta(days=days_ago)
    return start, start + timedelta(days=length)


def test_credit_is_the_unused_share_of_what_was_paid():
    start, end = term(100)
    credit = unused_credit("100.00", start, end, now=NOW)
    assert (credit.term_days, credit.remaining_days, credit.amount) == (365, 265, D("72.60"))  # 72.5979...
    assert "265 days remaining" in credit.formula and "72.60" in credit.formula


def test_half_a_term_left_is_worth_half():
    start, end = term(30, 60)
    assert unused_credit("60.00", start, end, now=NOW).amount == D("30.00")


def test_rounding_is_half_up_to_the_cent():
    start, end = term(1, 3)  # 2 of 3 days left of 1.00 = 0.6667
    assert unused_credit("1.00", start, end, now=NOW).amount == D("0.67")
    start, end = term(1, 8)  # 7 of 8 days left of 0.10 = 0.0875 -> 0.09
    assert unused_credit("0.10", start, end, now=NOW).amount == D("0.09")


@pytest.mark.parametrize("days_ago,length", [(365, 365), (400, 365), (1000, 365)])
def test_an_expired_term_has_no_credit(days_ago, length):
    start, end = term(days_ago, length)
    credit = unused_credit("100.00", start, end, now=NOW)
    assert credit.amount == 0 and credit.remaining_days == 0


def test_a_term_that_has_not_started_is_worth_all_of_it_and_no_more():
    start, end = NOW + timedelta(days=10), NOW + timedelta(days=375)
    credit = unused_credit("100.00", start, end, now=NOW)
    assert credit.amount == D("100.00") and credit.remaining_days == 365  # never more than was paid


@pytest.mark.parametrize("paid", ["0", "0.00", None, "-5"])
def test_nothing_paid_means_no_credit(paid):
    start, end = term(10)
    assert unused_credit(paid, start, end, now=NOW).amount == 0


def test_missing_or_broken_terms_give_no_credit():
    assert unused_credit("100", None, NOW, now=NOW) is NO_CREDIT
    assert unused_credit("100", NOW, None, now=NOW) is NO_CREDIT
    assert unused_credit("100", NOW, NOW, now=NOW) is NO_CREDIT          # zero-length term
    assert unused_credit("100", NOW, NOW - timedelta(days=5), now=NOW) is NO_CREDIT  # ends before it starts


def test_credit_never_exceeds_the_amount_paid_for_any_input():
    import random

    rng = random.Random(3)
    for _ in range(2000):
        paid = D(rng.randint(1, 100000)) / 100
        length = rng.randint(1, 800)
        start = NOW - timedelta(days=rng.randint(-50, 900))
        credit = unused_credit(paid, start, start + timedelta(days=length), now=NOW)
        assert 0 <= credit.amount <= paid
        assert credit.remaining_days <= credit.term_days
