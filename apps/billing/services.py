"""
Billing configuration logic: payment methods, tax rules and coupons. Staff web
pages, the staff API and the admin all call these; none holds rules of its own.
"""
from django.db import transaction
from rest_framework import status

from apps.accounts.roles import perm
from apps.audit import services as audit
from apps.core.exceptions import ServiceError

from .models import Coupon, PaymentMethod, TaxRule


def _denied(message="You do not have permission to perform this action."):
    return ServiceError(message, code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)


def _require(actor, codename):
    if not actor.has_perm(perm(codename)):
        raise _denied()


# --- Lookups used by the order/pricing code ------------------------------------------------

def active_payment_methods():
    return PaymentMethod.objects.filter(is_active=True)


def visible_payment_methods_for_user(user):
    """Staff with view_billing see every method; everyone else only the active ones."""
    if user and user.is_authenticated and user.has_perm(perm("view_billing")):
        return PaymentMethod.objects.all()
    return active_payment_methods()


def tax_rule_for_country(country):
    """The active rule for ``country``, else the active default rule, else None (no tax)."""
    country = (country or "").upper()
    rules = TaxRule.objects.filter(is_active=True)
    return (country and rules.filter(country=country).first()) or rules.filter(country="").first()


# --- Payment methods ------------------------------------------------------------------------

@transaction.atomic
def save_payment_method(actor, code, *, name, instructions="", sort_order=0, request=None):
    """Create or update a payment method (upsert by code)."""
    _require(actor, "manage_billing")
    # Look up, then validate, then write - never get_or_create-then-validate, which would INSERT
    # unvalidated input first (PostgreSQL rejects an over-long value with a raw database error).
    method = PaymentMethod.objects.filter(code=code).first()
    created = method is None
    if created:
        method = PaymentMethod(code=code)
    method.name, method.instructions, method.sort_order = name, instructions, sort_order
    method.full_clean()
    method.save()
    audit.record("payment_method.created" if created else "payment_method.updated", actor=actor, target=method,
                 metadata={"code": code}, request=request)
    return method


def set_payment_method_active(actor, method, is_active, *, request=None):
    _require(actor, "manage_billing")
    if method.is_active == is_active:
        return method
    method.is_active = is_active
    method.save(update_fields=["is_active", "updated_at"])
    audit.record("payment_method.status_changed", actor=actor, target=method, metadata={"is_active": is_active},
                 request=request)
    return method


# --- Tax rules -----------------------------------------------------------------------------

@transaction.atomic
def save_tax_rule(actor, country, *, name, rate, request=None):
    """Create or update the tax rule for a country (blank country = the default rule)."""
    _require(actor, "manage_billing")
    country = (country or "").strip().upper()
    rule = TaxRule.objects.filter(country=country).first()  # validate before writing, as above
    created = rule is None
    if created:
        rule = TaxRule(country=country)
    rule.name, rule.rate = name, rate
    rule.full_clean()
    rule.save()
    audit.record("tax_rule.created" if created else "tax_rule.updated", actor=actor, target=rule,
                 metadata={"country": country, "rate": str(rule.rate)}, request=request)
    return rule


def set_tax_rule_active(actor, rule, is_active, *, request=None):
    _require(actor, "manage_billing")
    if rule.is_active == is_active:
        return rule
    rule.is_active = is_active
    rule.save(update_fields=["is_active", "updated_at"])
    audit.record("tax_rule.status_changed", actor=actor, target=rule, metadata={"is_active": is_active},
                 request=request)
    return rule


# --- Coupons -------------------------------------------------------------------------------

COUPON_FIELDS = ("description", "discount_type", "value", "valid_from", "valid_until", "max_redemptions",
                 "one_per_client", "min_subtotal")


@transaction.atomic
def save_coupon(actor, code, data, *, request=None):
    """Create or update a coupon (upsert by code, case-insensitive)."""
    _require(actor, "manage_billing")
    code = (code or "").strip().upper()
    fields = {k: v for k, v in data.items() if k in COUPON_FIELDS}
    coupon = Coupon.objects.filter(code=code).first()
    created = coupon is None
    if created:
        coupon = Coupon(code=code, **fields)
    else:
        for key, value in fields.items():
            setattr(coupon, key, value)
    coupon.full_clean()
    coupon.save()
    audit.record("coupon.created" if created else "coupon.updated", actor=actor, target=coupon,
                 metadata={"code": code, "type": coupon.discount_type, "value": str(coupon.value)}, request=request)
    return coupon


def set_coupon_active(actor, coupon, is_active, *, request=None):
    _require(actor, "manage_billing")
    if coupon.is_active == is_active:
        return coupon
    coupon.is_active = is_active
    coupon.save(update_fields=["is_active", "updated_at"])
    audit.record("coupon.status_changed", actor=actor, target=coupon, metadata={"is_active": is_active},
                 request=request)
    return coupon
