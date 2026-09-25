"""The pricing engine: the only place a price, discount or tax figure is ever computed."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.billing import services as billing
from apps.billing.models import TaxRule
from apps.clients import services as client_services
from apps.core.exceptions import ServiceError
from apps.domains.models import Domain
from apps.orders import pricing, services
from apps.orders.models import CartItem, CouponRedemption, ItemKind, OrderStatus

pytestmark = pytest.mark.django_db

D = Decimal


def _cart(owner, client_obj):
    return services.get_open_cart(owner, client_obj)


def _item(cart, **fields):
    """A cart item created directly, bypassing the service's validation (to price invalid states)."""
    item = CartItem(cart=cart, **fields)
    item.save()
    return item


# --- Hosting ---------------------------------------------------------------------------------

@pytest.mark.parametrize("cycle,months,price,setup", [
    ("monthly", 0, "10.00", "0.00"),
    ("annual", 0, "100.00", "10.00"),
    ("custom", 3, "28.00", "0.00"),
])
def test_hosting_priced_from_the_catalogue_per_cycle(owner, client_obj, shop, cycle, months, price, setup):
    services.add_hosting(owner, client_obj, shop["product"], "example.com", cycle, months)
    line = pricing.price_cart(_cart(owner, client_obj)).lines[0]
    assert (line.unit_price, line.setup_fee, line.total) == (D(price), D(setup), D(price) + D(setup))
    assert line.ok


def test_hosting_description_names_plan_cycle_and_domain(owner, client_obj, shop):
    services.add_hosting(owner, client_obj, shop["product"], "Example.COM", "annual")
    line = pricing.price_cart(_cart(owner, client_obj)).lines[0]
    assert line.description == "Starter (Annually) - example.com"


def test_hosting_unknown_or_inactive_cycle_is_rejected(owner, client_obj, shop):
    with pytest.raises(ServiceError) as exc:
        services.add_hosting(owner, client_obj, shop["product"], "example.com", "biennial")
    assert exc.value.code == "price_not_found"
    with pytest.raises(ServiceError):  # a custom term that was never configured
        services.add_hosting(owner, client_obj, shop["product"], "example.com", "custom", 7)


def test_hosting_price_row_disabled_after_adding_is_reported_on_the_line(owner, client_obj, shop, manager):
    from apps.products import services as product_services

    services.add_hosting(owner, client_obj, shop["product"], "example.com", "annual")
    row = shop["product"].prices.get(billing_cycle="annual")
    product_services.set_price_active(manager, shop["product"], row, False)
    priced = pricing.price_cart(_cart(owner, client_obj))
    assert not priced.lines[0].ok and priced.subtotal == D("0.00") and not priced.is_valid


@pytest.mark.parametrize("status", ["hidden", "retired"])
def test_non_active_product_cannot_be_ordered(owner, client_obj, shop, manager, status):
    from apps.products import services as product_services

    product_services.set_product_status(manager, shop["product"], status)
    with pytest.raises(ServiceError) as exc:
        services.add_hosting(owner, client_obj, shop["product"], "example.com", "annual")
    assert exc.value.code == "product_unavailable"


def test_product_without_whm_package_cannot_be_ordered(owner, client_obj, shop, manager):
    from apps.products import services as product_services

    product_services.update_product(manager, shop["product"], {"name": "Starter", "whm_package_name": ""})
    with pytest.raises(ServiceError) as exc:
        services.add_hosting(owner, client_obj, shop["product"], "example.com", "annual")
    assert exc.value.code == "product_unavailable"


def test_hosting_domain_must_be_a_valid_name(owner, client_obj, shop):
    with pytest.raises(ServiceError) as exc:
        services.add_hosting(owner, client_obj, shop["product"], "not a domain", "annual")
    assert exc.value.code == "invalid_domain"


# --- Add-ons ---------------------------------------------------------------------------------

def test_addon_defaults_to_the_hosting_cycle(owner, client_obj, shop):
    host = services.add_hosting(owner, client_obj, shop["product"], "example.com", "annual")
    services.add_addon(owner, host, shop["addon"])
    lines = pricing.price_cart(_cart(owner, client_obj)).lines
    assert lines[1].unit_price == D("20.00") and lines[1].item.billing_cycle == "annual"


def test_addon_one_time_price_is_allowed_on_any_hosting_cycle(owner, client_obj, shop):
    host = services.add_hosting(owner, client_obj, shop["product"], "example.com", "custom", 3)
    services.add_addon(owner, host, shop["addon"], "one_time")
    assert pricing.price_cart(_cart(owner, client_obj)).lines[1].unit_price == D("5.00")


def test_addon_must_share_the_hosting_cycle_or_be_one_time(owner, client_obj, shop):
    host = services.add_hosting(owner, client_obj, shop["product"], "example.com", "annual")
    with pytest.raises(ServiceError) as exc:
        services.add_addon(owner, host, shop["addon"], "monthly")  # addon has a monthly price, but host is annual
    assert exc.value.code == "invalid_addon"


def test_addon_with_no_price_for_the_cycle_is_not_offered(owner, client_obj, shop):
    host = services.add_hosting(owner, client_obj, shop["product"], "example.com", "custom", 3)
    options = services.addon_options(host, shop["addon"])
    assert [row.billing_cycle for row in options] == ["one_time"]


def test_addon_needs_a_hosting_parent_in_the_same_cart(owner, client_obj, shop, manager):
    other = client_services.create_client(manager, {"first_name": "B", "email": "b@x.test"})
    other_owner = other.contacts.get().user
    foreign_host = services.add_hosting(other_owner, other, shop["product"], "other.com", "annual")
    cart = _cart(owner, client_obj)
    item = _item(cart, kind=ItemKind.ADDON, addon=shop["addon"], parent=foreign_host, billing_cycle="annual")
    lines = {line.item.pk: line for line in pricing.price_cart(cart).lines}
    assert "attached to a hosting plan" in lines[item.pk].error
    orphan = _item(cart, kind=ItemKind.ADDON, addon=shop["addon"], billing_cycle="annual")
    assert not {line.item.pk: line for line in pricing.price_cart(cart).lines}[orphan.pk].ok


def test_removing_the_hosting_removes_its_addons(owner, client_obj, shop):
    host = services.add_hosting(owner, client_obj, shop["product"], "example.com", "annual")
    services.add_addon(owner, host, shop["addon"])
    services.remove_item(owner, host)
    assert not CartItem.objects.exists()


# --- Domains ---------------------------------------------------------------------------------

def test_domain_registration_is_price_times_years(owner, client_obj, shop):
    services.add_domain_registration(owner, client_obj, "example.com", 3)
    line = pricing.price_cart(_cart(owner, client_obj)).lines[0]
    assert line.unit_price == D("36.00") and line.setup_fee == D("0.00")
    assert line.description == "Domain registration: example.com (3 years)"


def test_domain_term_must_be_within_the_tld_limits(owner, client_obj, shop):
    with pytest.raises(ServiceError) as exc:
        services.add_domain_registration(owner, client_obj, "example.com", 11)
    assert exc.value.code == "invalid_term"


def test_unsupported_tld_is_rejected(owner, client_obj, shop):
    with pytest.raises(ServiceError) as exc:
        services.add_domain_registration(owner, client_obj, "example.zzz", 1)
    assert exc.value.code == "tld_not_supported"


def test_taken_domain_cannot_be_added(owner, client_obj, shop, manager):
    from apps.domains import services as domain_services

    domain_services.request_registration(manager, client_obj, "taken.com", 1)
    with pytest.raises(ServiceError) as exc:
        services.add_domain_registration(owner, client_obj, "taken.com", 1)
    assert exc.value.code == "domain_unavailable"


def test_availability_is_only_rechecked_when_asked(owner, client_obj, shop, manager):
    """Cart views must not hit the registrar on every render; checkout asks explicitly."""
    from apps.domains import services as domain_services

    services.add_domain_registration(owner, client_obj, "example.com", 1)
    domain_services.request_registration(manager, client_obj, "example.com", 1)  # snapped up meanwhile
    cart = _cart(owner, client_obj)
    assert pricing.price_cart(cart).lines[0].ok  # cheap view: not re-verified
    with pytest.raises(ServiceError):
        pricing.price_cart(cart, strict=True, verify_availability=True)


def test_domain_transfer_flat_price_and_needs_code(owner, client_obj, shop):
    services.add_domain_transfer(owner, client_obj, "moving.com", "epp-123")
    line = pricing.price_cart(_cart(owner, client_obj)).lines[0]
    assert line.unit_price == D("9.00")
    with pytest.raises(ServiceError) as exc:
        services.add_domain_transfer(owner, client_obj, "moving2.com", "")
    assert exc.value.code == "auth_code_required"


def test_transfer_of_a_domain_we_already_hold_is_rejected(owner, client_obj, shop, manager):
    from apps.domains import services as domain_services

    domain_services.request_registration(manager, client_obj, "ours.com", 1)
    with pytest.raises(ServiceError) as exc:
        services.add_domain_transfer(owner, client_obj, "ours.com", "epp")
    assert exc.value.code == "domain_exists"
    assert Domain.objects.filter(name="ours.com").count() == 1


def test_transfer_auth_code_is_stored_encrypted(owner, client_obj, shop):
    item = services.add_domain_transfer(owner, client_obj, "moving.com", "super-secret-epp")
    assert "super-secret-epp" not in item.auth_code_encrypted
    assert item.get_auth_code() == "super-secret-epp"


def test_transfer_must_be_one_year(owner, client_obj, shop):
    cart = _cart(owner, client_obj)
    item = _item(cart, kind=ItemKind.DOMAIN_TRANSFER, domain_name="moving.com", years=3, auth_code_encrypted="x")
    assert pricing.price_cart(cart).lines[0].error and item.pk


def test_same_domain_cannot_be_in_the_cart_twice(owner, client_obj, shop):
    services.add_domain_registration(owner, client_obj, "example.com", 1)
    with pytest.raises(ServiceError) as exc:
        services.add_domain_registration(owner, client_obj, "example.com", 2)
    assert exc.value.code == "duplicate_item"
    with pytest.raises(ServiceError):
        services.add_domain_transfer(owner, client_obj, "example.com", "epp")


# --- Totals: discount, tax, rounding -------------------------------------------------------------

def test_totals_without_discount_or_tax(owner, client_obj, shop):
    services.add_hosting(owner, client_obj, shop["product"], "example.com", "annual")
    services.add_domain_registration(owner, client_obj, "example.com", 1)
    priced = pricing.price_cart(_cart(owner, client_obj))
    assert priced.subtotal == D("122.00") and priced.discount_total == 0 and priced.tax_total == 0
    assert priced.total == D("122.00") and priced.currency == "USD" and priced.is_valid


def test_percentage_coupon_and_tax_are_applied_in_that_order(owner, client_obj, shop, coupon, manager):
    billing.save_tax_rule(manager, "US", name="Sales tax", rate=D("8.00"))
    services.add_hosting(owner, client_obj, shop["product"], "example.com", "annual")  # 110.00
    cart = _cart(owner, client_obj)
    services.apply_coupon(owner, cart, "save10")
    priced = pricing.price_cart(cart)
    # 110.00 - 10% = 99.00 taxable; 8% tax = 7.92
    assert (priced.subtotal, priced.discount_total, priced.tax_total, priced.total) == (
        D("110.00"), D("11.00"), D("7.92"), D("106.92"))


def test_tax_is_charged_on_the_discounted_amount_not_the_subtotal(owner, client_obj, shop, manager):
    billing.save_tax_rule(manager, "US", name="VAT", rate=D("20"))
    billing.save_coupon(manager, "HALF", {"discount_type": "percent", "value": D("50")})
    services.add_hosting(owner, client_obj, shop["product"], "example.com", "monthly")  # 10.00
    cart = _cart(owner, client_obj)
    services.apply_coupon(owner, cart, "HALF")
    priced = pricing.price_cart(cart)
    assert priced.tax_total == D("1.00") and priced.total == D("6.00")


@pytest.mark.parametrize("value,expected_discount", [
    (D("15.00"), D("15.00")),   # fixed amount
    (D("500.00"), D("110.00")),  # fixed amount larger than the order is capped at the subtotal
])
def test_fixed_coupon_is_capped_at_the_subtotal(owner, client_obj, shop, manager, value, expected_discount):
    billing.save_coupon(manager, "FIXED", {"discount_type": "fixed", "value": value})
    services.add_hosting(owner, client_obj, shop["product"], "example.com", "annual")  # 110.00
    cart = _cart(owner, client_obj)
    services.apply_coupon(owner, cart, "FIXED")
    priced = pricing.price_cart(cart)
    assert priced.discount_total == expected_discount and priced.total == D("110.00") - expected_discount


def test_rounding_is_half_up_to_two_places(owner, client_obj, shop, manager):
    from apps.products import services as product_services

    product_services.set_price(manager, shop["product"], billing_cycle="monthly", price="33.33")
    billing.save_tax_rule(manager, "US", name="Tax", rate=D("7.50"))
    billing.save_coupon(manager, "THIRD", {"discount_type": "percent", "value": D("33.33")})
    services.add_hosting(owner, client_obj, shop["product"], "example.com", "monthly")
    cart = _cart(owner, client_obj)
    services.apply_coupon(owner, cart, "THIRD")
    priced = pricing.price_cart(cart)
    # 33.33 * 33.33% = 11.1089 -> 11.11 ; taxable 22.22 ; 7.5% = 1.6665 -> 1.67 ; total 23.89
    assert (priced.discount_total, priced.tax_total, priced.total) == (D("11.11"), D("1.67"), D("23.89"))


def test_tax_rule_by_country_then_default_then_none(owner, client_obj, shop, manager):
    billing.save_tax_rule(manager, "", name="Default", rate=D("5"))
    billing.save_tax_rule(manager, "US", name="US tax", rate=D("10"))
    services.add_hosting(owner, client_obj, shop["product"], "example.com", "monthly")
    cart = _cart(owner, client_obj)
    assert pricing.price_cart(cart).tax_total == D("1.00")  # client is in US -> 10%

    client_services.update_client(manager, client_obj, {"country": "FR"})
    cart.client.refresh_from_db()
    assert pricing.price_cart(cart).tax_total == D("0.50")  # no FR rule -> default 5%

    billing.set_tax_rule_active(manager, TaxRule.objects.get(country=""), False)
    assert pricing.price_cart(cart).tax_total == D("0.00")  # default disabled -> no tax


def test_inactive_tax_rule_is_ignored(owner, client_obj, shop, manager):
    rule = billing.save_tax_rule(manager, "US", name="Tax", rate=D("10"))
    billing.set_tax_rule_active(manager, rule, False)
    services.add_hosting(owner, client_obj, shop["product"], "example.com", "monthly")
    assert pricing.price_cart(_cart(owner, client_obj)).tax_total == 0


def test_non_strict_reports_bad_lines_and_prices_the_rest(owner, client_obj, shop):
    services.add_hosting(owner, client_obj, shop["product"], "example.com", "monthly")
    cart = _cart(owner, client_obj)
    _item(cart, kind=ItemKind.HOSTING, product=shop["product"], domain_name="bad.com", billing_cycle="biennial")
    priced = pricing.price_cart(cart)
    assert priced.subtotal == D("10.00") and len(priced.errors) == 1 and not priced.is_valid


def test_strict_refuses_an_invalid_or_empty_cart(owner, client_obj, shop):
    cart = _cart(owner, client_obj)
    with pytest.raises(ServiceError) as exc:
        pricing.price_cart(cart, strict=True)
    assert exc.value.code == "cart_empty"
    _item(cart, kind=ItemKind.HOSTING, product=shop["product"], domain_name="bad.com", billing_cycle="biennial")
    with pytest.raises(ServiceError) as exc:
        pricing.price_cart(cart, strict=True)
    assert exc.value.code == "cart_invalid"


# --- Coupon rules ---------------------------------------------------------------------------------

def _priced_with(owner, client_obj, shop, code):
    services.add_hosting(owner, client_obj, shop["product"], "example.com", "annual")
    cart = _cart(owner, client_obj)
    services.apply_coupon(owner, cart, code)
    return pricing.price_cart(cart)


def test_unknown_coupon_code(owner, client_obj, shop):
    services.add_hosting(owner, client_obj, shop["product"], "example.com", "annual")
    with pytest.raises(ServiceError) as exc:
        services.apply_coupon(owner, _cart(owner, client_obj), "NOPE")
    assert exc.value.code == "coupon_invalid"


def test_coupon_code_is_case_insensitive(owner, client_obj, shop, coupon):
    assert _priced_with(owner, client_obj, shop, "  save10 ").discount_total == D("11.00")


@pytest.mark.parametrize("field,delta", [("valid_until", -1), ("valid_from", 1)])
def test_coupon_outside_its_window_is_rejected(owner, client_obj, shop, manager, field, delta):
    billing.save_coupon(manager, "WINDOW", {"discount_type": "percent", "value": D("10"),
                                            field: timezone.now() + timedelta(days=delta)})
    services.add_hosting(owner, client_obj, shop["product"], "example.com", "annual")
    with pytest.raises(ServiceError):
        services.apply_coupon(owner, _cart(owner, client_obj), "WINDOW")


def test_inactive_coupon_is_rejected(owner, client_obj, shop, manager, coupon):
    billing.set_coupon_active(manager, coupon, False)
    services.add_hosting(owner, client_obj, shop["product"], "example.com", "annual")
    with pytest.raises(ServiceError):
        services.apply_coupon(owner, _cart(owner, client_obj), "SAVE10")


def test_coupon_minimum_subtotal(owner, client_obj, shop, manager):
    billing.save_coupon(manager, "BIG", {"discount_type": "fixed", "value": D("5"), "min_subtotal": D("200")})
    services.add_hosting(owner, client_obj, shop["product"], "example.com", "annual")  # 110
    with pytest.raises(ServiceError, match="at least"):
        services.apply_coupon(owner, _cart(owner, client_obj), "BIG")


def test_coupon_that_stops_being_valid_is_flagged_not_silently_kept(owner, client_obj, shop, manager, coupon):
    priced = _priced_with(owner, client_obj, shop, "SAVE10")
    assert priced.coupon and not priced.coupon_error
    billing.set_coupon_active(manager, coupon, False)
    cart = _cart(owner, client_obj)
    cart.refresh_from_db()
    priced = pricing.price_cart(cart)
    assert priced.coupon is None and priced.discount_total == 0 and priced.coupon_error and not priced.is_valid
    with pytest.raises(ServiceError):
        pricing.price_cart(cart, strict=True)


def test_usage_limits_count_only_orders_that_are_not_cancelled(owner, client_obj, shop, manager, coupon):
    billing.save_coupon(manager, "ONCE", {"discount_type": "percent", "value": D("10"), "max_redemptions": 1})
    services.add_hosting(owner, client_obj, shop["product"], "example.com", "annual")
    cart = _cart(owner, client_obj)
    services.apply_coupon(owner, cart, "ONCE")
    order = services.checkout(owner, cart, payment_method_code=shop["bank"].code)
    assert CouponRedemption.objects.count() == 1

    services.add_hosting(owner, client_obj, shop["product"], "second.com", "annual")
    with pytest.raises(ServiceError, match="usage limit"):
        services.apply_coupon(owner, _cart(owner, client_obj), "ONCE")

    cancelled = services.cancel_order(owner, order)  # cancelling releases the use
    assert cancelled.status == OrderStatus.CANCELLED
    services.apply_coupon(owner, _cart(owner, client_obj), "ONCE")


def test_one_per_client(owner, client_obj, shop, manager):
    billing.save_coupon(manager, "WELCOME", {"discount_type": "percent", "value": D("10"), "one_per_client": True})
    services.add_hosting(owner, client_obj, shop["product"], "example.com", "annual")
    cart = _cart(owner, client_obj)
    services.apply_coupon(owner, cart, "WELCOME")
    services.checkout(owner, cart, payment_method_code=shop["bank"].code)

    services.add_hosting(owner, client_obj, shop["product"], "second.com", "annual")
    with pytest.raises(ServiceError, match="already used"):
        services.apply_coupon(owner, _cart(owner, client_obj), "WELCOME")

    # ...but another client can still use it.
    other = client_services.create_client(manager, {"first_name": "B", "email": "b@x.test"})
    other_owner = other.contacts.get().user
    services.add_hosting(other_owner, other, shop["product"], "other.com", "annual")
    services.apply_coupon(other_owner, _cart(other_owner, other), "WELCOME")


def test_coupon_model_validation(manager):
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        billing.save_coupon(manager, "BAD", {"discount_type": "percent", "value": D("101")})
    with pytest.raises(ValidationError):
        now = timezone.now()
        billing.save_coupon(manager, "BAD2", {"discount_type": "fixed", "value": D("1"),
                                              "valid_from": now, "valid_until": now - timedelta(days=1)})
