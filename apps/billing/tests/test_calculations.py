"""The shared billing arithmetic: rounding, allocation and document totals."""
import random
from decimal import Decimal

import pytest

from apps.billing import calculations as calc

D = Decimal


@pytest.mark.parametrize("value,expected", [("1.005", "1.01"), ("1.004", "1.00"), (2, "2.00"), ("0.125", "0.13")])
def test_money_rounds_half_up(value, expected):
    assert calc.money(value) == D(expected)


def test_tax_amount():
    assert calc.tax_amount(D("100.00"), D("15")) == D("15.00")
    assert calc.tax_amount(D("9.99"), D("7.5")) == D("0.75")  # 0.74925 -> 0.75
    assert calc.tax_amount(D("50"), D("0")) == D("0.00")


@pytest.mark.parametrize("kind,value,subtotal,expected", [
    ("percent", "10", "99.99", "10.00"), ("percent", "100", "40", "40.00"), ("fixed", "7.5", "40", "7.50"),
    ("fixed", "70", "40", "40.00"),
])
def test_discount_amount_is_capped_at_the_subtotal(kind, value, subtotal, expected):
    assert calc.discount_amount(kind, D(value), D(subtotal)) == D(expected)


def test_unknown_discount_type_is_rejected():
    with pytest.raises(ValueError):
        calc.discount_amount("bogus", D("1"), D("10"))


def test_allocate_gives_the_remainder_to_the_largest_weight():
    assert calc.allocate(D("10.00"), [1, 1, 1]) == [D("3.34"), D("3.33"), D("3.33")]
    assert calc.allocate(D("0.01"), [5, 10]) == [D("0.00"), D("0.01")]
    assert calc.allocate(D("0"), [1, 2]) == [D("0.00"), D("0.00")]
    assert calc.allocate(D("0"), []) == []


def test_allocate_rejects_impossible_requests():
    with pytest.raises(ValueError):
        calc.allocate(D("5"), [])
    with pytest.raises(ValueError):
        calc.allocate(D("5"), [0, 0])
    with pytest.raises(ValueError):
        calc.allocate(D("5"), [1, -1])


def test_allocate_always_sums_exactly_and_never_goes_negative():
    rng = random.Random(7)
    for _ in range(500):
        weights = [D(rng.randint(0, 5000)) / 100 for _ in range(rng.randint(1, 8))]
        if not any(weights):
            continue
        total = D(rng.randint(0, 100000)) / 100
        shares = calc.allocate(total, weights)
        assert sum(shares) == calc.money(total)
        assert all(share >= 0 for share in shares)
        assert all(share == 0 for share, weight in zip(shares, weights) if weight == 0)


def test_compute_document_matches_the_line_sums():
    totals = calc.compute([D("100.00"), D("33.33"), D("0.01")], [True, True, False],
                          discount_total=D("13.33"), tax_rate=D("15"))
    assert totals.subtotal == D("133.34")
    assert totals.discount_total == D("13.33")
    assert sum(line.discount for line in totals.lines) == totals.discount_total
    assert sum(line.tax for line in totals.lines) == totals.tax_total
    assert sum(line.total for line in totals.lines) == totals.total
    assert totals.lines[2].tax == 0  # the non-taxable line carries no tax
    assert totals.tax_total == D("18.00")  # 15% of (100.00 - 10.00) + (33.33 - 3.33)
    assert totals.total == D("133.34") - D("13.33") + D("18.00")


def test_distribute_fixes_the_totals_it_is_given():
    totals = calc.distribute([D("10.00"), D("20.00")], [True, True], discount_total=D("3.00"), tax_total=D("2.71"))
    assert totals.total == D("30.00") - D("3.00") + D("2.71")
    assert sum(line.tax for line in totals.lines) == D("2.71")


def test_discount_larger_than_the_subtotal_is_rejected():
    with pytest.raises(ValueError):
        calc.compute([D("10.00")], [True], discount_total=D("10.01"))
    with pytest.raises(ValueError):
        calc.distribute([D("10.00")], [True], discount_total=D("11"), tax_total=D("0"))
