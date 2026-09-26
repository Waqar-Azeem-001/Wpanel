"""
The authoritative cart pricing engine.

Every price shown to a customer, and every figure stored on an order, comes
from here, computed from the catalogue at that moment. Carts hold only
selections (roadmap section 23, "server-side truth"): nothing a browser sends -
price, total, discount, tax, duration - is ever used as a number. Phase 07
invoices copy the figures snapshotted on the order rather than recomputing.

Money is ``Decimal``, rounded half-up to two places.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError

from apps.billing.calculations import ZERO, discount_amount, money, tax_amount
from apps.billing.models import Coupon, TaxRule
from apps.billing.services import tax_rule_for_client, tax_rule_for_country
from apps.core.exceptions import ServiceError
from apps.core.web import error_text
from apps.domains import services as domain_services
from apps.domains.models import DOMAIN_NAME_VALIDATOR, LIVE_STATUSES, Domain, domain_tld
from apps.products import services as product_services
from apps.products.models import BillingCycle, CatalogStatus

from .models import CartItem, CouponRedemption, ItemKind, OrderItem, OrderStatus

def cycle_label(billing_cycle, custom_months=0):
    if billing_cycle == BillingCycle.CUSTOM:
        return f"{custom_months} months"
    try:
        return BillingCycle(billing_cycle).label
    except ValueError:
        return billing_cycle or ""


@dataclass
class PricedLine:
    item: CartItem
    description: str = ""
    unit_price: Decimal = ZERO
    setup_fee: Decimal = ZERO
    error: str = ""

    @property
    def total(self):
        return self.unit_price + self.setup_fee

    @property
    def ok(self):
        return not self.error


@dataclass
class PricedCart:
    lines: list
    currency: str
    subtotal: Decimal = ZERO
    coupon: Coupon = None
    coupon_error: str = ""
    discount_total: Decimal = ZERO
    tax_rule: TaxRule = None
    tax_rate: Decimal = ZERO
    tax_total: Decimal = ZERO
    total: Decimal = ZERO
    errors: list = field(default_factory=list)
    tax_pending: bool = False  # a guest cart: the tax depends on the country, which is asked at checkout

    @property
    def is_valid(self):
        return bool(self.lines) and not self.errors and not self.coupon_error


# --- Per-item pricing ---------------------------------------------------------------------

def describe_item(item):
    """A human description built from the selection alone (used even when the item can't be priced)."""
    if item.kind == ItemKind.HOSTING:
        name = item.product.name if item.product else "Hosting"
        return f"{name} ({cycle_label(item.billing_cycle, item.custom_months)}) - {item.domain_name}"
    if item.kind == ItemKind.ADDON:
        name = item.addon.name if item.addon else "Add-on"
        label = "one-time" if item.billing_cycle == BillingCycle.ONE_TIME else cycle_label(
            item.billing_cycle, item.custom_months)
        return f"{name} ({label})"
    if item.kind == ItemKind.DOMAIN_REGISTER:
        return f"Domain registration: {item.domain_name} ({item.years} year{'s' if item.years != 1 else ''})"
    return f"Domain transfer: {item.domain_name}"


def _validate_domain_name(name):
    try:
        DOMAIN_NAME_VALIDATOR(name)
    except ValidationError:
        raise ServiceError("Enter a valid domain name, e.g. example.com.", code="invalid_domain")


def domain_reserved_by_pending_order(name):
    """True if an unpaid order already holds this name (soft reservation until it is paid or cancelled)."""
    return OrderItem.objects.filter(
        kind__in=(ItemKind.DOMAIN_REGISTER, ItemKind.DOMAIN_TRANSFER), domain_name=name,
        order__status=OrderStatus.PENDING_PAYMENT,
    ).exists()


def _price_hosting(item):
    product = item.product
    if product is None or product.status != CatalogStatus.ACTIVE:
        raise ServiceError("This plan is no longer available.", code="product_unavailable")
    if not product.whm_package_name:
        raise ServiceError("This plan is not available for ordering.", code="product_unavailable")
    _validate_domain_name(item.domain_name)
    row = product_services.get_effective_price(product, item.billing_cycle, item.custom_months)
    return row.price, row.setup_fee


DOMAIN_KINDS = (ItemKind.DOMAIN_REGISTER, ItemKind.DOMAIN_TRANSFER)


def addon_parent_kinds(addon):
    """What an add-on may be attached to: WHOIS privacy goes with a domain, everything else with a hosting plan."""
    return DOMAIN_KINDS if addon is not None and addon.applies_to == "domain" else (ItemKind.HOSTING,)


def _price_addon(item):
    addon, parent = item.addon, item.parent
    if addon is None or addon.status != CatalogStatus.ACTIVE:
        raise ServiceError("This add-on is no longer available.", code="addon_unavailable")
    if parent is None or parent.kind not in addon_parent_kinds(addon) or parent.cart_id != item.cart_id:
        what = "a domain" if addon.applies_to == "domain" else "a hosting plan"
        raise ServiceError(f"This add-on must be attached to {what} in your cart.", code="invalid_addon")
    if parent.kind in DOMAIN_KINDS:
        # A domain has years, not a billing cycle: the add-on is charged per year of the domain, or once.
        if item.billing_cycle not in (BillingCycle.ANNUAL, BillingCycle.ONE_TIME):
            raise ServiceError("This add-on is charged per year of the domain, or once.", code="invalid_addon")
        row = product_services.get_effective_price(addon, item.billing_cycle, item.custom_months)
        years = parent.years if item.billing_cycle == BillingCycle.ANNUAL else 1
        return money(row.price * years), row.setup_fee
    if item.billing_cycle != BillingCycle.ONE_TIME and (item.billing_cycle, item.custom_months) != (
            parent.billing_cycle, parent.custom_months):
        raise ServiceError("This add-on must be billed with your hosting plan or as a one-time charge.",
                           code="invalid_addon")
    row = product_services.get_effective_price(addon, item.billing_cycle, item.custom_months)
    return row.price, row.setup_fee


def _price_domain_register(item, verify_availability):
    name = item.domain_name
    _validate_domain_name(name)
    pricing = domain_services.get_tld_pricing(domain_tld(name))
    if not (pricing.min_years <= item.years <= pricing.max_years):
        raise ServiceError(f"Choose a term between {pricing.min_years} and {pricing.max_years} years.",
                           code="invalid_term")
    if domain_reserved_by_pending_order(name):
        raise ServiceError(f"{name} is already in another order awaiting payment.", code="domain_reserved")
    if verify_availability:
        available, _ = domain_services.check_availability(name)
        if not available:
            raise ServiceError(f"{name} is no longer available.", code="domain_unavailable")
    return money(pricing.register_price * item.years), ZERO


def _price_domain_transfer(item):
    name = item.domain_name
    _validate_domain_name(name)
    pricing = domain_services.get_tld_pricing(domain_tld(name))
    if item.years != 1:
        raise ServiceError("A transfer includes a one-year extension.", code="invalid_term")
    if not item.auth_code_encrypted:
        raise ServiceError("An authorization code is required for a transfer.", code="auth_code_required")
    if Domain.objects.filter(name=name, status__in=LIVE_STATUSES).exists():
        raise ServiceError(f"{name} already exists in our system.", code="domain_exists")
    if domain_reserved_by_pending_order(name):
        raise ServiceError(f"{name} is already in another order awaiting payment.", code="domain_reserved")
    return money(pricing.transfer_price), ZERO


def price_item(item, *, verify_availability=False):
    """
    (unit_price, setup_fee) for a cart item, or raise ``ServiceError``.

    ``verify_availability`` also asks the registrar whether a domain can still be
    registered - a possibly-slow external call, so it is only requested when an
    item is added and again just before checkout, not on every cart view.
    """
    if item.kind == ItemKind.HOSTING:
        price, setup = _price_hosting(item)
    elif item.kind == ItemKind.ADDON:
        price, setup = _price_addon(item)
    elif item.kind == ItemKind.DOMAIN_REGISTER:
        price, setup = _price_domain_register(item, verify_availability)
    elif item.kind == ItemKind.DOMAIN_TRANSFER:
        price, setup = _price_domain_transfer(item)
    else:  # pragma: no cover - guarded by the field's choices
        raise ServiceError("Unknown item type.", code="invalid_item")
    return money(price), money(setup)


# --- Coupons ------------------------------------------------------------------------------

def coupon_usage(coupon):
    """Redemptions that still count against the coupon (cancelled orders release theirs)."""
    return CouponRedemption.objects.filter(coupon=coupon).exclude(order__status=OrderStatus.CANCELLED)


def evaluate_coupon(coupon, client, subtotal):
    """The discount ``coupon`` gives ``client`` (None for a visitor who has no account yet) on ``subtotal``, or raise."""
    try:
        coupon.check_window()
    except ValueError as exc:
        raise ServiceError(str(exc), code="coupon_invalid")
    if subtotal < coupon.min_subtotal:
        raise ServiceError(f"This coupon needs an order of at least {coupon.min_subtotal}.", code="coupon_invalid")
    usage = coupon_usage(coupon)
    if coupon.max_redemptions is not None and usage.count() >= coupon.max_redemptions:
        raise ServiceError("This coupon has reached its usage limit.", code="coupon_invalid")
    if coupon.one_per_client and client is not None and usage.filter(client=client).exists():
        raise ServiceError("You have already used this coupon.", code="coupon_invalid")
    return money(coupon.amount_for(subtotal))


# --- The whole cart -----------------------------------------------------------------------

def price_cart(cart, *, strict=False, verify_availability=False, country=""):
    """
    Price every line, then apply the coupon and tax.

    Non-strict (cart pages/API): problems are reported per line / as a coupon
    error and the cart still prices what it can. Strict (checkout): any problem
    raises ``ServiceError`` so an order can only ever be built from a fully
    valid cart.

    A guest cart has no client yet: the coupon is checked without the once-per-client rule (checkout checks it against the new
    account) and tax is worked out from ``country`` when the visitor has chosen one, else marked pending.
    """
    items = list(cart.items.select_related("product", "addon", "parent").order_by("id"))
    if strict and not items:
        raise ServiceError("Your cart is empty.", code="cart_empty")

    lines = []
    for item in items:
        line = PricedLine(item=item, description=describe_item(item))
        try:
            line.unit_price, line.setup_fee = price_item(item, verify_availability=verify_availability)
        except (ServiceError, ValidationError) as exc:
            line.error = error_text(exc)
            if strict:
                raise ServiceError(f"{line.description}: {line.error}", code="cart_invalid")
        lines.append(line)

    priced = PricedCart(lines=lines, currency=settings.STORE_CURRENCY)
    priced.errors = [line.error for line in lines if line.error]
    priced.subtotal = money(sum((line.total for line in lines if line.ok), ZERO))

    if cart.coupon_id:
        try:
            priced.discount_total = evaluate_coupon(cart.coupon, cart.client, priced.subtotal)
            priced.coupon = cart.coupon
        except ServiceError as exc:
            if strict:
                raise
            priced.coupon_error = exc.message

    taxable = priced.subtotal - priced.discount_total
    if cart.client_id is None:
        priced.tax_rule = tax_rule_for_country(country) if country else None
        priced.tax_pending = not country
    else:
        priced.tax_rule = tax_rule_for_client(cart.client)
    if priced.tax_rule:
        priced.tax_rate = priced.tax_rule.rate
        priced.tax_total = tax_amount(taxable, priced.tax_rule.rate)
    priced.total = money(taxable + priced.tax_total)
    return priced
