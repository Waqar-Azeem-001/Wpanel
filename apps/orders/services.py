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
from apps.billing.models import Coupon, PaymentMethod
from apps.clients.models import Client
from apps.clients.services import contact_role
from apps.core.exceptions import ServiceError
from apps.notifications import services as notifications
from apps.products.models import BillingCycle

from . import pricing
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
    if cart.user_id != actor.pk and not actor.has_perm(perm("manage_orders")):
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


def add_hosting(actor, client, product, domain, billing_cycle, custom_months=0):
    cart = get_open_cart(actor, client)
    item = CartItem(cart=cart, kind=ItemKind.HOSTING, product=product, domain_name=domain.strip().lower(),
                    billing_cycle=billing_cycle, custom_months=custom_months or 0)
    return _add_item(actor, cart, item,
                     duplicate_check=Q(kind=ItemKind.HOSTING, product=product, domain_name=item.domain_name))


def addon_options(parent_item, addon):
    """The active prices of ``addon`` that may be billed with ``parent_item`` (same cycle, or one-time)."""
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


def add_domain_registration(actor, client, domain, years=1):
    cart = get_open_cart(actor, client)
    name = domain.strip().lower()
    item = CartItem(cart=cart, kind=ItemKind.DOMAIN_REGISTER, domain_name=name, years=years)
    return _add_item(actor, cart, item, duplicate_check=Q(
        kind__in=(ItemKind.DOMAIN_REGISTER, ItemKind.DOMAIN_TRANSFER), domain_name=name))


def add_domain_transfer(actor, client, domain, auth_code):
    cart = get_open_cart(actor, client)
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
    pricing.evaluate_coupon(coupon, cart.client, subtotal)  # raises with the reason if it can't be used
    cart.coupon = coupon
    cart.save(update_fields=["coupon", "updated_at"])
    return cart


def remove_coupon(actor, cart):
    _require_own_cart(actor, cart)
    _require_open(cart)
    cart.coupon = None
    cart.save(update_fields=["coupon", "updated_at"])
    return cart


# --- Checkout -------------------------------------------------------------------------------

def _billing_snapshot(client):
    return {
        "billing_name": client.contact_name, "billing_company": client.company_name,
        "billing_email": client.email, "billing_phone": client.phone,
        "billing_address_line1": client.address_line1, "billing_address_line2": client.address_line2,
        "billing_city": client.city, "billing_state": client.state, "billing_postcode": client.postcode,
        "billing_country": client.country, "billing_tax_id": client.tax_id,
    }


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
        cart = Cart.objects.select_for_update().select_related("client", "coupon").get(pk=cart.pk)
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
            **_billing_snapshot(client),
        )
        created = {}
        for line in priced.lines:  # cart order guarantees a hosting line precedes its add-ons
            item = line.item
            created[item.pk] = OrderItem.objects.create(
                order=order, kind=item.kind, description=line.description, product=item.product, addon=item.addon,
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
        _notify_order_placed(actor, order, method)
    return order


def _notify_order_placed(actor, order, method):
    link = reverse("orders_customer:detail", args=[order.pk])
    notifications.notify(actor, event="order.placed", title=f"Order {order.reference} placed",
                         body=f"Total {order.currency} {order.total}. Awaiting payment.", link=link)
    notifications.send_email(
        to_email=order.billing_email or order.client.email, template="order_placed",
        context={"order": order, "items": list(order.items.all()), "method": method}, user=actor,
        event="order.placed",
    )


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
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status != OrderStatus.PENDING_PAYMENT:
        raise ServiceError("Only an order that is awaiting payment can be cancelled.", code="invalid_status")
    order.status, order.cancel_reason = OrderStatus.CANCELLED, reason.strip()[:500]
    order.save(update_fields=["status", "cancel_reason", "updated_at"])
    audit.record("order.cancelled", actor=actor, target=order, metadata={"reason": order.cancel_reason},
                 request=request)
    return order
