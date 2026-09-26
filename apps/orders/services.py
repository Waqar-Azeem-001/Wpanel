"""
Cart and order business logic. The customer web pages, the API and the staff
pages all call these functions; none of them computes a price or applies a
rule of its own (prices come only from ``apps.orders.pricing``).
"""
import re

from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from rest_framework import status

from apps.accounts.roles import perm
from apps.audit import services as audit
from apps.billing.invoicing import billing_snapshot, cancel_invoice_internal, create_invoice_for_order
from apps.billing.models import Coupon, Invoice, InvoiceStatus, PaymentMethod
from apps.clients.models import Client
from apps.clients.services import contact_role
from apps.core.exceptions import ServiceError
from apps.notifications import services as notifications
from apps.products.models import BillingCycle

from . import lifecycle, pricing
from .models import (Cart, CartItem, CartStatus, CouponRedemption, ItemKind, Order, OrderItem, OrderStatus)


def _denied(message="You do not have permission to perform this action."):
    return ServiceError(message, code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)


def can_order_for(actor, client):
    """Staff with manage_orders, or any contact of the client, may build a cart for it."""
    if not (actor and actor.is_authenticated):
        return False
    return actor.has_perm(perm("manage_orders")) or contact_role(actor, client) is not None


def _require_can_order(actor, client):
    if not can_order_for(actor, client):
        raise _denied()


def _require_own_cart(actor, cart):
    if cart.user_id is None:
        # A guest cart: only ever reached through the visitor's own session (see ``guest``), so there is no one to compare.
        return
    if actor is None or (cart.user_id != actor.pk and not actor.has_perm(perm("manage_orders"))):
        raise _denied()


def _require_open(cart):
    if cart.status != CartStatus.OPEN:
        raise ServiceError("This cart has already been checked out.", code="cart_closed")


# --- Cart -----------------------------------------------------------------------------------

def get_open_cart(actor, client):
    """The actor's open cart for ``client`` (created on first use)."""
    _require_can_order(actor, client)
    cart, _ = Cart.objects.get_or_create(user=actor, client=client, status=CartStatus.OPEN)
    return cart


def resolve_client(actor, client_id=None):
    """
    The client a cart action is for: ``client_id`` when given, else the one
    client the actor is a contact of. Anything ambiguous is an error rather than
    a guess - an order must never land on the wrong account.
    """
    if client_id is not None:
        client = Client.objects.filter(pk=client_id).first()
        # Same answer whether the client doesn't exist or the actor can't use it.
        if client is None or not can_order_for(actor, client):
            raise ServiceError("Client not found.", code="not_found", status_code=status.HTTP_404_NOT_FOUND)
        return client
    from apps.clients.services import single_contact_client

    client = single_contact_client(actor)
    if client is None:
        raise ServiceError("Specify which client this is for (client_id).", code="client_required")
    return client


def _add_item(actor, cart, item, *, duplicate_check=None):
    _require_own_cart(actor, cart)
    _require_open(cart)
    if duplicate_check is not None and cart.items.filter(duplicate_check).exists():
        raise ServiceError("This is already in your cart.", code="duplicate_item")
    pricing.price_item(item, verify_availability=True)  # raises if it can't be ordered right now
    item.save()
    return item


def add_hosting(actor, client, product, domain, billing_cycle, custom_months=0, *, cart=None):
    """Add a plan. ``cart`` is given for a guest cart; otherwise it is the actor's own cart for ``client``."""
    cart = cart or get_open_cart(actor, client)
    item = CartItem(cart=cart, kind=ItemKind.HOSTING, product=product, domain_name=domain.strip().lower(),
                    billing_cycle=billing_cycle, custom_months=custom_months or 0)
    return _add_item(actor, cart, item,
                     duplicate_check=Q(kind=ItemKind.HOSTING, product=product, domain_name=item.domain_name))


def addon_options(parent_item, addon):
    """
    The active prices of ``addon`` that may be billed with ``parent_item``: for a hosting plan the plan's own cycle or a one-time
    charge, for a domain a yearly price (charged for each year of the domain) or a one-time charge. Empty when the add-on does
    not fit this kind of line.
    """
    if parent_item.kind not in pricing.addon_parent_kinds(addon):
        return []
    if parent_item.kind in pricing.DOMAIN_KINDS:
        return [row for row in addon.prices.filter(is_active=True)
                if row.billing_cycle in (BillingCycle.ANNUAL, BillingCycle.ONE_TIME)]
    return [row for row in addon.prices.filter(is_active=True)
            if row.billing_cycle == BillingCycle.ONE_TIME
            or (row.billing_cycle, row.custom_months) == (parent_item.billing_cycle, parent_item.custom_months)]


def add_addon(actor, parent_item, addon, billing_cycle=None, custom_months=0):
    cart = parent_item.cart
    if billing_cycle is None:  # default to the parent's cycle, else a one-time price
        options = addon_options(parent_item, addon)
        if not options:
            raise ServiceError("This add-on is not available for your billing cycle.", code="invalid_addon")
        billing_cycle, custom_months = options[0].billing_cycle, options[0].custom_months
    item = CartItem(cart=cart, kind=ItemKind.ADDON, addon=addon, parent=parent_item, billing_cycle=billing_cycle,
                    custom_months=custom_months or 0)
    return _add_item(actor, cart, item, duplicate_check=Q(kind=ItemKind.ADDON, addon=addon, parent=parent_item))


def add_domain_registration(actor, client, domain, years=1, *, cart=None):
    cart = cart or get_open_cart(actor, client)
    name = domain.strip().lower()
    item = CartItem(cart=cart, kind=ItemKind.DOMAIN_REGISTER, domain_name=name, years=years)
    return _add_item(actor, cart, item, duplicate_check=Q(
        kind__in=(ItemKind.DOMAIN_REGISTER, ItemKind.DOMAIN_TRANSFER), domain_name=name))


def add_domain_transfer(actor, client, domain, auth_code, *, cart=None):
    cart = cart or get_open_cart(actor, client)
    name = domain.strip().lower()
    item = CartItem(cart=cart, kind=ItemKind.DOMAIN_TRANSFER, domain_name=name, years=1)
    item.set_auth_code(auth_code)
    return _add_item(actor, cart, item, duplicate_check=Q(
        kind__in=(ItemKind.DOMAIN_REGISTER, ItemKind.DOMAIN_TRANSFER), domain_name=name))


def remove_item(actor, item):
    """Remove an item (removing a hosting item removes its add-ons with it)."""
    _require_own_cart(actor, item.cart)
    _require_open(item.cart)
    item.delete()


def apply_coupon(actor, cart, code):
    _require_own_cart(actor, cart)
    _require_open(cart)
    coupon = Coupon.objects.filter(code=(code or "").strip().upper()).first()
    if coupon is None:
        raise ServiceError("This coupon code is not valid.", code="coupon_invalid")
    subtotal = pricing.price_cart(cart).subtotal
    pricing.evaluate_coupon(coupon, cart.client, subtotal)  # raises with the reason if it can't be used (client None: a guest)
    cart.coupon = coupon
    cart.save(update_fields=["coupon", "updated_at"])
    return cart


def remove_coupon(actor, cart):
    _require_own_cart(actor, cart)
    _require_open(cart)
    cart.coupon = None
    cart.save(update_fields=["coupon", "updated_at"])
    return cart


# --- The billing period of a plan, and add-ons as switches ---------------------------------------------------

def period_options(item):
    """
    The billing periods a hosting line can be switched to, with what each costs and how much longer terms save compared with
    paying monthly (only when a monthly price exists; nothing is invented). ``[]`` for other kinds of line.
    """
    from apps.billing.calculations import money
    from apps.products.models import STANDARD_CYCLE_MONTHS, BillingCycle

    if item.kind != ItemKind.HOSTING or item.product is None:
        return []
    rows = list(item.product.prices.filter(is_active=True))

    def months(row):
        return row.custom_months if row.billing_cycle == BillingCycle.CUSTOM else STANDARD_CYCLE_MONTHS.get(row.billing_cycle, 0)

    monthly = next((r for r in rows if r.billing_cycle == BillingCycle.MONTHLY), None)
    options = []
    for row in sorted(rows, key=months):
        n = months(row)
        if not n:
            continue
        saving = None
        if monthly is not None and n > 1 and monthly.price * n > row.price:
            saving = round(100 * (1 - row.price / (monthly.price * n)))
        options.append({"value": row.option_value, "months": n, "price": row.price, "setup_fee": row.setup_fee,
                        "per_month": money(row.price / n),
                        "saving": saving, "label": pricing.cycle_label(row.billing_cycle, row.custom_months),
                        "selected": (row.billing_cycle, row.custom_months) == (item.billing_cycle, item.custom_months)})
    return options


def change_period(actor, item, billing_cycle, custom_months=0):
    """
    Switch a hosting line to another billing period the plan is priced for. Recurring add-ons follow the plan's new period (or
    are dropped if they have no price for it); one-time ones stay.
    """
    from apps.products.models import BillingCycle
    from apps.products.services import get_effective_price

    _require_own_cart(actor, item.cart)
    _require_open(item.cart)
    if item.kind != ItemKind.HOSTING:
        raise ServiceError("Only a hosting plan has a billing period.", code="invalid_item")
    get_effective_price(item.product, billing_cycle, custom_months or 0)  # raises if the plan has no such period
    with transaction.atomic():
        item.billing_cycle, item.custom_months = billing_cycle, custom_months or 0
        item.save(update_fields=["billing_cycle", "custom_months", "updated_at"])
        for child in item.addon_items.select_related("addon"):
            if child.billing_cycle == BillingCycle.ONE_TIME:
                continue
            options = [row for row in addon_options(item, child.addon) if row.billing_cycle != BillingCycle.ONE_TIME]
            if not options:
                child.delete()
                continue
            child.billing_cycle, child.custom_months = options[0].billing_cycle, options[0].custom_months
            child.save(update_fields=["billing_cycle", "custom_months", "updated_at"])
    return item


def toggle_addon(actor, parent_item, addon, billing_cycle=None, custom_months=0):
    """An add-on as a switch: attach it if it is not on this line, take it off if it is. Returns True when it is now on."""
    _require_own_cart(actor, parent_item.cart)
    _require_open(parent_item.cart)
    existing = parent_item.addon_items.filter(addon=addon).first()
    if existing is not None:
        existing.delete()
        return False
    add_addon(actor, parent_item, addon, billing_cycle, custom_months)
    return True


# --- Selling more: upgrade and cross-sell -----------------------------------------------------------

def upgrade_options(item):
    """The upsell for a hosting line: the plan staff chose to suggest, if it is orderable in the same billing cycle."""
    from apps.products.models import CatalogStatus
    from apps.products.services import get_effective_price

    product = item.product
    target = product.upsell_product if product is not None else None
    if item.kind != ItemKind.HOSTING or target is None or target.status != CatalogStatus.ACTIVE or not target.whm_package_name:
        return None
    try:
        now = get_effective_price(product, item.billing_cycle, item.custom_months)
        then = get_effective_price(target, item.billing_cycle, item.custom_months)
    except ServiceError:
        return None
    return {"product": target, "price": then.price, "setup_fee": then.setup_fee, "extra": then.price - now.price,
            "cycle": pricing.cycle_label(item.billing_cycle, item.custom_months)}


def upgrade_item(actor, item, *, request=None):
    """Swap a hosting line for its suggested bigger plan, keeping the domain, the billing cycle and the add-ons."""
    _require_own_cart(actor, item.cart)
    _require_open(item.cart)
    offer = upgrade_options(item)
    if offer is None:
        raise ServiceError("There is no upgrade available for this plan.", code="no_upgrade")
    if item.cart.items.filter(kind=ItemKind.HOSTING, product=offer["product"], domain_name=item.domain_name).exists():
        raise ServiceError("That plan is already in your cart for this domain.", code="duplicate_item")
    previous = item.product
    item.product = offer["product"]
    pricing.price_item(item, verify_availability=False)
    item.save(update_fields=["product", "updated_at"])
    if actor is not None:
        audit.record("cart.upgraded", actor=actor, target=item.cart,
                     metadata={"from": previous.name, "to": offer["product"].name}, request=request)
    return item


def domain_offers(cart, priced):
    """Cross-sell: a plan's own domain that is not yet being registered or transferred in this cart, with its price."""
    from apps.domains import services as domain_services
    from apps.domains.models import LIVE_STATUSES, Domain, domain_tld

    covered = {i.domain_name for i in cart.items.filter(kind__in=(ItemKind.DOMAIN_REGISTER, ItemKind.DOMAIN_TRANSFER))}
    offers, seen = [], set()
    for line in priced.lines:
        name = line.item.domain_name
        if line.item.kind != ItemKind.HOSTING or not line.ok or not name or name in covered or name in seen:
            continue
        seen.add(name)
        if Domain.objects.filter(name=name, status__in=LIVE_STATUSES).exists():
            continue  # already ours: nothing to sell
        try:
            row = domain_services.get_tld_pricing(domain_tld(name))
        except Exception:  # noqa: BLE001 - an unsupported ending simply has no offer
            continue
        offers.append({"domain": name, "price": row.register_price, "years": max(row.min_years, 1)})
    return offers


def recommended_addon_ids(cart):
    """The add-ons staff marked as recommended for any plan in this cart."""
    from apps.products.models import Addon

    products = [i.product_id for i in cart.items.filter(kind=ItemKind.HOSTING)]
    return set(Addon.objects.filter(recommended_for__in=products).values_list("pk", flat=True)) if products else set()


# --- Guest carts ---------------------------------------------------------------------------------

def adopt_guest_cart(guest_cart, user, client):
    """
    Move a visitor's selections into their own open cart (after they sign in or create an account) and delete the guest cart.
    Items already present are skipped; add-ons follow their plan. Returns the account's cart.
    """
    _require_can_order(user, client)
    with transaction.atomic():
        target = get_open_cart(user, client)
        mapping = {}
        for item in guest_cart.items.select_related("product", "addon", "parent").order_by("id"):
            parent = mapping.get(item.parent_id)
            if item.kind == ItemKind.ADDON and parent is None:
                continue
            exists = target.items.filter(kind=item.kind, product=item.product, addon=item.addon, parent=parent,
                                         domain_name=item.domain_name).exists()
            if exists:
                if item.kind == ItemKind.HOSTING:
                    mapping[item.pk] = target.items.filter(kind=item.kind, product=item.product,
                                                           domain_name=item.domain_name).first()
                continue
            copy = CartItem(cart=target, kind=item.kind, product=item.product, addon=item.addon, parent=parent,
                            domain_name=item.domain_name, billing_cycle=item.billing_cycle,
                            custom_months=item.custom_months, years=item.years,
                            auth_code_encrypted=item.auth_code_encrypted)
            copy.save()
            mapping[item.pk] = copy
        if guest_cart.coupon_id and not target.coupon_id:
            target.coupon = guest_cart.coupon
            target.save(update_fields=["coupon", "updated_at"])
        guest_cart.delete()
    return target


def purge_guest_carts(*, days=30):
    """Delete guest carts nobody touched for ``days`` days (run daily). Returns how many."""
    from datetime import timedelta

    from django.utils import timezone

    stale = Cart.objects.filter(user__isnull=True, updated_at__lt=timezone.now() - timedelta(days=days))
    count = stale.count()
    stale.delete()
    return count


# --- Checkout -------------------------------------------------------------------------------

def checkout(actor, cart, *, payment_method_code, notes="", request=None):
    """
    Turn a cart into an order awaiting payment.

    Everything is recomputed from the catalogue here - the cart holds only
    selections - so the order can only carry prices that are correct *now*, and
    it is refused if any line, the coupon or a domain is no longer valid. Safe
    against a double submit: the first call closes the cart, so a repeat is
    rejected rather than creating a second order.
    """
    _require_own_cart(actor, cart)
    # Ask the database, not the (possibly stale) instance: a repeat submit must be told the
    # cart is already checked out, not fail later with a confusing "domain is reserved".
    if not Cart.objects.filter(pk=cart.pk, status=CartStatus.OPEN).exists():
        raise ServiceError("This cart has already been checked out.", code="cart_closed")
    method = PaymentMethod.objects.filter(code=payment_method_code, is_active=True).first()
    if method is None:
        raise ServiceError("Choose a valid payment method.", code="payment_method_invalid")

    # First pass, outside the transaction: fail fast, and keep any slow registrar
    # availability lookups out of the row locks taken below.
    pricing.price_cart(cart, strict=True, verify_availability=True)

    with transaction.atomic():
        # Lock only the cart row itself: PostgreSQL refuses FOR UPDATE across the nullable side of a join
        # (the cart's optional coupon), and SQLite silently ignores locking, so this can't be caught locally.
        cart = Cart.objects.select_for_update().get(pk=cart.pk)
        _require_open(cart)  # a concurrent double-submit finds it already closed
        if cart.coupon_id:  # serialise redemptions of the same code
            cart.coupon = Coupon.objects.select_for_update().get(pk=cart.coupon_id)
        # Second pass under the locks: the figures written to the order.
        priced = pricing.price_cart(cart, strict=True)
        client = cart.client

        order = Order.objects.create(
            client=client, placed_by=actor, status=OrderStatus.PENDING_PAYMENT, currency=priced.currency,
            subtotal=priced.subtotal, discount_total=priced.discount_total,
            tax_name=priced.tax_rule.name if priced.tax_rule else "", tax_rate=priced.tax_rate,
            tax_total=priced.tax_total, total=priced.total,
            coupon=priced.coupon, coupon_code=priced.coupon.code if priced.coupon else "",
            payment_method=method, payment_method_name=method.name, notes=notes.strip()[:2000],
            **billing_snapshot(client),
        )
        created = {}
        for line in priced.lines:  # cart order guarantees a hosting line precedes its add-ons
            item = line.item
            created[item.pk] = OrderItem.objects.create(
                order=order, kind=item.kind, description=line.description[:255], product=item.product,
                addon=item.addon,
                parent=created.get(item.parent_id), domain_name=item.domain_name, billing_cycle=item.billing_cycle,
                custom_months=item.custom_months, years=item.years, unit_price=line.unit_price,
                setup_fee=line.setup_fee, line_total=line.total, auth_code_encrypted=item.auth_code_encrypted,
            )
        if priced.coupon:
            CouponRedemption.objects.create(coupon=priced.coupon, order=order, client=client,
                                            discount_amount=priced.discount_total)
        cart.status = CartStatus.CONVERTED
        cart.save(update_fields=["status", "updated_at"])

        audit.record("order.placed", actor=actor, target=order,
                     metadata={"client_id": client.pk, "total": str(order.total), "items": len(priced.lines),
                               "coupon": order.coupon_code, "payment_method": method.code}, request=request)
        invoice = create_invoice_for_order(order, actor=actor)
        _notify_order_placed(actor, order, method, invoice)
    order.refresh_from_db()  # a zero-total order is settled (and marked paid) as its invoice is issued
    return order


def _notify_order_placed(actor, order, method, invoice):
    link = reverse("orders_customer:detail", args=[order.pk])
    notifications.dispatch(
        "order.placed", user=actor, email=order.billing_email or order.client.email,
        title=f"Order {order.reference} placed", body=f"Total {order.currency} {order.total}. Awaiting payment.",
        link=link, context={"order": order, "items": list(order.items.all()), "method": method, "invoice": invoice})


# --- Orders ---------------------------------------------------------------------------------

def visible_orders_for_user(user):
    queryset = Order.objects.select_related("client", "payment_method")
    if user and user.is_authenticated and user.has_perm(perm("view_orders")):
        return queryset
    if user and user.is_authenticated:
        return queryset.filter(client__contacts__user=user).distinct()
    return queryset.none()


def search_orders(queryset, term):
    term = (term or "").strip()
    if not term:
        return queryset
    q = (Q(client__company_name__icontains=term) | Q(client__email__icontains=term)
         | Q(billing_email__icontains=term) | Q(items__domain_name__icontains=term) | Q(coupon_code__iexact=term))
    match = re.fullmatch(r"[oO]?(\d+)", term)
    if match:
        q |= Q(pk=int(match.group(1)))
    return queryset.filter(q).distinct()


@transaction.atomic
def cancel_order(actor, order, *, reason="", request=None):
    """Cancel an order that hasn't been paid. Releases its coupon use and any reserved domain names."""
    if not (actor.has_perm(perm("manage_orders")) or contact_role(actor, order.client) is not None):
        raise _denied()
    # Lock order matches the payment path (invoice first, then order) so the two can never deadlock.
    open_invoices = list(Invoice.objects.select_for_update().filter(
        order=order, status__in=(InvoiceStatus.UNPAID, InvoiceStatus.PARTIALLY_PAID)))
    order = Order.objects.select_for_update().get(pk=order.pk)
    # Anyone with access may cancel an unpaid order; staff may also close one that failed or was flagged as fraud
    # (any money already taken is refunded separately, through the invoice).
    closable = (OrderStatus.PENDING_PAYMENT,) + ((OrderStatus.FRAUD, OrderStatus.FAILED)
                                                 if actor.has_perm(perm("manage_orders")) else ())
    if order.status not in closable:
        raise ServiceError("Only an order that is awaiting payment can be cancelled.", code="invalid_status")
    order = lifecycle.transition(order, OrderStatus.CANCELLED, actor=actor, action="order.cancelled", reason=reason,
                                 request=request)
    for invoice in open_invoices:
        cancel_invoice_internal(invoice, actor=actor, reason=order.cancel_reason, request=request)
    return order
