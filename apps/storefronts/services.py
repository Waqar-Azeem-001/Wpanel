"""
Business logic for external storefronts. Web views and the storefront-facing API both call these; neither holds a
rule of its own (the same shape as every other app in this project).
"""
import secrets

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction

from apps.accounts.roles import perm
from apps.audit import services as audit
from apps.core.exceptions import ServiceError

from .models import ExternalStorefront, StorefrontProduct, StorefrontStatus

STOREFRONT_FIELDS = ("name", "site_url", "integration_type", "webhook_url", "notes")
PUBLIC_ID_BYTES = 12
SECRET_BYTES = 32


def _denied(message="You do not have permission to perform this action."):
    from rest_framework import status

    return ServiceError(message, code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)


def _require(actor, codename="manage_providers"):
    if not actor.has_perm(perm(codename)):
        raise _denied()


def _generate_public_id():
    return "sf_" + secrets.token_urlsafe(PUBLIC_ID_BYTES)


def _generate_secret():
    return secrets.token_urlsafe(SECRET_BYTES)


@transaction.atomic
def create_storefront(actor, data, *, request=None):
    """Returns (storefront, raw_secret) - the only time the raw secret exists outside the person's own clipboard."""
    _require(actor)
    raw_secret = _generate_secret()
    storefront = ExternalStorefront(
        public_id=_generate_public_id(), secret_hash=make_password(raw_secret),
        **{f: data[f] for f in STOREFRONT_FIELDS if f in data})
    storefront.full_clean()
    storefront.save()
    audit.record("storefront.created", actor=actor, target=storefront,
                 metadata={"name": storefront.name, "site_url": storefront.site_url}, request=request)
    return storefront, raw_secret


@transaction.atomic
def update_storefront(actor, storefront, data, *, request=None):
    _require(actor)
    changed = {}
    for field in STOREFRONT_FIELDS:
        if field in data and getattr(storefront, field) != data[field]:
            setattr(storefront, field, data[field])
            changed[field] = data[field]
    if not changed:
        return storefront
    storefront.full_clean()
    storefront.save()
    audit.record("storefront.updated", actor=actor, target=storefront, metadata={"fields": sorted(changed)},
                 request=request)
    return storefront


def set_storefront_status(actor, storefront, new_status, *, request=None):
    _require(actor)
    if new_status not in StorefrontStatus.values:
        raise ServiceError("Unknown status.", code="invalid_status")
    if storefront.status == new_status:
        return storefront
    storefront.status = new_status
    storefront.save(update_fields=["status", "updated_at"])
    audit.record("storefront.status_changed", actor=actor, target=storefront, metadata={"status": new_status},
                 request=request)
    return storefront


def rotate_secret(actor, storefront, *, request=None):
    """A fresh secret, immediately invalidating the old one. Returns the raw value - shown once, never stored."""
    _require(actor)
    raw_secret = _generate_secret()
    storefront.secret_hash = make_password(raw_secret)
    storefront.save(update_fields=["secret_hash", "updated_at"])
    audit.record("storefront.credential_rotated", actor=actor, target=storefront, request=request)
    return raw_secret


def authenticate_storefront(public_id, raw_secret):
    """None if the credential is wrong, unknown, or the storefront is disabled - deliberately the same outcome for
    all three, so a caller learns nothing about which part was wrong."""
    if not public_id or not raw_secret:
        return None
    storefront = ExternalStorefront.objects.filter(public_id=public_id, status=StorefrontStatus.ACTIVE).first()
    if storefront is None or not check_password(raw_secret, storefront.secret_hash):
        return None
    from django.utils import timezone

    ExternalStorefront.objects.filter(pk=storefront.pk).update(last_accessed_at=timezone.now())
    return storefront


# --- Which plans a storefront may show -----------------------------------------------------------------------

def publish_product(actor, product, storefront, *, request=None, **overrides):
    """Publish (or update the display overrides for) one plan on one storefront. A call that changes nothing (the
    product was already published here with the same overrides) is silent - not every re-save of a form the staff
    member did not actually change needs its own audit entry."""
    _require(actor, "manage_products")
    allowed = {k: v for k, v in overrides.items() if k in ("title_override", "description_override", "featured", "sort_order")}
    existing = StorefrontProduct.objects.filter(storefront=storefront, product=product).first()
    unchanged = existing and all(getattr(existing, k) == v for k, v in allowed.items())
    link, created = StorefrontProduct.objects.update_or_create(storefront=storefront, product=product, defaults=allowed)
    if not unchanged:
        audit.record("storefront.product_published" if created else "storefront.product_updated", actor=actor,
                     target=product, metadata={"storefront": storefront.name}, request=request)
    return link


def unpublish_product(actor, product, storefront, *, request=None):
    _require(actor, "manage_products")
    deleted, _ = StorefrontProduct.objects.filter(storefront=storefront, product=product).delete()
    if deleted:
        audit.record("storefront.product_unpublished", actor=actor, target=product,
                     metadata={"storefront": storefront.name}, request=request)


def set_product_storefronts(actor, product, storefront_ids, *, featured=False, sort_order=100, request=None):
    """
    Replace the full set of storefronts a plan is published to in one call (the product edit page's "Available on"
    checkboxes, plus one shared Featured/Sort order for this product - a per-storefront override, if ever needed, is
    still just a row in ``StorefrontProduct`` and can be set from a later, more detailed screen): publishes to every
    id in ``storefront_ids`` not already linked, updates the featured/sort_order on every id already linked, and
    unpublishes from every currently-linked storefront not in the set. Each add/update/remove is its own audited call.
    """
    _require(actor, "manage_products")
    wanted = set(storefront_ids)
    existing = set(StorefrontProduct.objects.filter(product=product).values_list("storefront_id", flat=True))
    for storefront in ExternalStorefront.objects.filter(pk__in=wanted):
        publish_product(actor, product, storefront, featured=featured, sort_order=sort_order, request=request)
    for storefront in ExternalStorefront.objects.filter(pk__in=existing - wanted):
        unpublish_product(actor, product, storefront, request=request)


def products_for(storefront):
    """Every plan this storefront may see: published to it, still allowed to publish externally at all, and still
    actually orderable (the same "active" rule the public catalog already uses - Rule 3, no second visibility rule)."""
    from apps.products.models import CatalogStatus, Product

    return (Product.objects.filter(storefront_links__storefront=storefront, publish_externally=True,
                                   status=CatalogStatus.ACTIVE)
           .select_related("category").prefetch_related("prices", "storefront_links")
           .distinct())
