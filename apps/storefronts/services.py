"""
Business logic for external storefronts. Web views and the storefront-facing API both call these; neither holds a
rule of its own (the same shape as every other app in this project).
"""
import logging
import secrets

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction

from apps.accounts.roles import perm
from apps.audit import services as audit
from apps.core.exceptions import ServiceError

from .models import ExternalStorefront, StorefrontProduct, StorefrontStatus

logger = logging.getLogger(__name__)

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


def rotate_webhook_secret(actor, storefront, *, request=None):
    """A fresh webhook signing secret (separate from the API credential above - this one we keep readable
    ourselves, to sign every outgoing delivery; see ``ExternalStorefront.set_webhook_secret``). Returns the raw
    value - shown once, never stored or logged anywhere else."""
    _require(actor)
    raw_secret = _generate_secret()
    storefront.set_webhook_secret(raw_secret)
    storefront.save(update_fields=["webhook_secret_encrypted", "updated_at"])
    audit.record("storefront.webhook_secret_rotated", actor=actor, target=storefront, request=request)
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


# --- Telling a storefront something changed (Step 11 - optional, deferrable; deferred no longer) -------------------

def notify_catalog_changed(storefront, event, *, request=None):
    """Queue a signed "refetch your catalog" ping (``apps/storefronts/webhooks.py``) once the surrounding
    transaction commits - a broker outage must never be why a plan's own save fails (the same pattern as order
    fulfilment's ``schedule()``). A no-op if this storefront has no webhook configured yet."""
    if not storefront.webhook_url or not storefront.webhook_secret_encrypted:
        return

    def enqueue():
        from .models import StorefrontWebhookDelivery
        from .tasks import deliver_webhook_task

        delivery = StorefrontWebhookDelivery.objects.create(storefront=storefront, event=event)
        try:
            deliver_webhook_task.delay(delivery.pk)
        except Exception:  # noqa: BLE001 - a broker hiccup must not be why the caller's own action fails
            logger.exception("Could not queue webhook delivery %s", delivery.pk)

    transaction.on_commit(enqueue)


def storefronts_for(product):
    """Every storefront a plan is currently published to - used to fan out a "please refetch" ping when the plan
    itself changes (``apps/storefronts/signals.py``), not to decide what a storefront's API response contains
    (that is ``products_for`` below, the other direction)."""
    return ExternalStorefront.objects.filter(published_products__product=product).distinct()


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
        notify_catalog_changed(storefront, "product.published" if created else "product.updated", request=request)
    return link


def unpublish_product(actor, product, storefront, *, request=None):
    _require(actor, "manage_products")
    deleted, _ = StorefrontProduct.objects.filter(storefront=storefront, product=product).delete()
    if deleted:
        audit.record("storefront.product_unpublished", actor=actor, target=product,
                     metadata={"storefront": storefront.name}, request=request)
        notify_catalog_changed(storefront, "product.unpublished", request=request)


def set_product_storefronts(actor, product, storefront_ids, *, request=None):
    """
    Replace the full set of storefronts a plan is published to (the product page's "Available on" checkboxes):
    publishes to every id in ``storefront_ids`` not already linked (with blank/default overrides - set those
    separately from each storefront's own row, once it exists), and unpublishes from every currently-linked
    storefront not in the set. Deliberately does not touch the overrides of a storefront that was already linked
    and stays linked - resaving this checkbox list must never clobber a title/description/featured/sort_order a
    staff member set from the per-storefront row below it.
    """
    _require(actor, "manage_products")
    wanted = set(storefront_ids)
    existing = set(StorefrontProduct.objects.filter(product=product).values_list("storefront_id", flat=True))
    for storefront in ExternalStorefront.objects.filter(pk__in=wanted - existing):
        publish_product(actor, product, storefront, request=request)
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
