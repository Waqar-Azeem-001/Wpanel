"""
Catalog business logic. Staff API, staff web pages, the public catalog and the
admin all call these functions; none of them holds pricing or visibility rules
of its own.
"""
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Q

from apps.accounts.roles import perm
from apps.audit import services as audit
from apps.core.exceptions import ServiceError
from apps.core.utils import unique_slug

from .models import Addon, AddonPrice, CatalogStatus, Product, ProductPrice, Server

PRODUCT_FIELDS = (
    "name", "description", "type", "resource_limits", "whm_package_name", "auto_setup", "default_auto_renew",
)
ADDON_FIELDS = ("name", "description")
SERVER_FIELDS = ("name", "hostname", "ip_address", "max_accounts", "notes", "kind", "api_port", "api_username",
                 "use_ssl", "verify_ssl")


def _denied(message="You do not have permission to perform this action."):
    from rest_framework import status

    return ServiceError(message, code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)


def _require(actor, codename):
    if not actor.has_perm(perm(codename)):
        raise _denied()


def _apply(instance, data, allowed):
    changed = {}
    for field in allowed:
        if field in data and getattr(instance, field) != data[field]:
            setattr(instance, field, data[field])
            changed[field] = data[field]
    return changed


# --- Visibility & search -----------------------------------------------------------------

def visible_products_for_user(user):
    """Staff with view_products see everything; everyone else sees active items only."""
    queryset = Product.objects.prefetch_related("prices", "servers")
    if user and user.is_authenticated and user.has_perm(perm("view_products")):
        return queryset
    return queryset.filter(status=CatalogStatus.ACTIVE)


def visible_addons_for_user(user):
    queryset = Addon.objects.prefetch_related("prices")
    if user and user.is_authenticated and user.has_perm(perm("view_products")):
        return queryset
    return queryset.filter(status=CatalogStatus.ACTIVE)


def search_catalog(queryset, term):
    term = (term or "").strip()
    if not term:
        return queryset
    return queryset.filter(Q(name__icontains=term) | Q(description__icontains=term) | Q(slug__icontains=term))


# --- Pricing (authoritative, server-side) -------------------------------------------------

def get_effective_price(item, billing_cycle, custom_months=0):
    """
    The authoritative price/setup-fee row for ``item`` (a Product or Addon) at a
    billing cycle. Later phases (cart, checkout, renewals) must call this rather
    than trusting any client-submitted price or total.
    """
    try:
        return item.prices.get(billing_cycle=billing_cycle, custom_months=custom_months, is_active=True)
    except ObjectDoesNotExist:
        raise ServiceError("This billing cycle is not available for this item.", code="price_not_found")


def _price_model_and_fk(item):
    return (ProductPrice, "product") if isinstance(item, Product) else (AddonPrice, "addon")


@transaction.atomic
def set_price(actor, item, *, billing_cycle, price, custom_months=0, setup_fee=0, request=None):
    """Create or replace the price for a billing cycle (upsert, keeps history clean)."""
    _require(actor, "manage_products")
    model, fk = _price_model_and_fk(item)
    # Look up, validate, then write. (get_or_create-then-validate would INSERT unvalidated input first,
    # and PostgreSQL rejects an out-of-range value with a raw database error instead of a validation error.)
    key = {fk: item, "billing_cycle": billing_cycle, "custom_months": custom_months}
    entry = model.objects.filter(**key).first()
    created = entry is None
    if created:
        entry = model(**key)
    entry.price, entry.setup_fee = price, setup_fee
    entry.full_clean()
    entry.save()
    kind = "product" if fk == "product" else "addon"
    audit.record(f"{kind}.price_{'added' if created else 'changed'}", actor=actor, target=item,
                 metadata={"billing_cycle": billing_cycle, "custom_months": custom_months,
                          "price": str(price), "setup_fee": str(setup_fee)}, request=request)
    return entry


@transaction.atomic
def remove_price(actor, item, price_entry, *, request=None):
    _require(actor, "manage_products")
    kind = "product" if isinstance(item, Product) else "addon"
    meta = {"billing_cycle": price_entry.billing_cycle, "custom_months": price_entry.custom_months}
    price_entry.delete()
    audit.record(f"{kind}.price_removed", actor=actor, target=item, metadata=meta, request=request)


def set_price_active(actor, item, price_entry, is_active, *, request=None):
    _require(actor, "manage_products")
    if price_entry.is_active == is_active:
        return price_entry
    price_entry.is_active = is_active
    price_entry.save(update_fields=["is_active", "updated_at"])
    kind = "product" if isinstance(item, Product) else "addon"
    audit.record(f"{kind}.price_changed", actor=actor, target=item,
                 metadata={"billing_cycle": price_entry.billing_cycle, "custom_months": price_entry.custom_months,
                          "is_active": is_active}, request=request)
    return price_entry


# --- Products -----------------------------------------------------------------------------

@transaction.atomic
def create_product(actor, data, *, request=None):
    _require(actor, "manage_products")
    name = data.get("name", "")
    product = Product(name=name, slug=unique_slug(Product.objects, name))
    for field in PRODUCT_FIELDS:
        if field in data and field != "name":
            setattr(product, field, data[field])
    product.full_clean()
    product.save()
    audit.record("product.created", actor=actor, target=product, metadata={"type": product.type}, request=request)
    return product


@transaction.atomic
def update_product(actor, product, data, *, request=None):
    _require(actor, "manage_products")
    changed = _apply(product, data, PRODUCT_FIELDS)
    if not changed:
        return product
    product.full_clean()
    product.save()
    audit.record("product.updated", actor=actor, target=product, metadata={"fields": sorted(changed)}, request=request)
    return product


def set_product_status(actor, product, new_status, *, request=None):
    _require(actor, "manage_products")
    if new_status not in CatalogStatus.values:
        raise ServiceError("Unknown status.", code="invalid_status")
    previous = product.status
    if previous == new_status:
        return product
    product.status = new_status
    product.save(update_fields=["status", "updated_at"])
    audit.record("product.status_changed", actor=actor, target=product,
                 metadata={"from": previous, "to": new_status}, request=request)
    return product


def set_product_servers(actor, product, server_ids, *, request=None):
    _require(actor, "manage_products")
    servers = list(Server.objects.filter(pk__in=server_ids))
    product.servers.set(servers)
    audit.record("product.servers_changed", actor=actor, target=product,
                 metadata={"servers": [s.name for s in servers]}, request=request)
    return product


# --- Addons -------------------------------------------------------------------------------

@transaction.atomic
def create_addon(actor, data, *, request=None):
    _require(actor, "manage_products")
    name = data.get("name", "")
    addon = Addon(name=name, slug=unique_slug(Addon.objects, name), description=data.get("description", ""))
    addon.full_clean()
    addon.save()
    audit.record("addon.created", actor=actor, target=addon, request=request)
    return addon


@transaction.atomic
def update_addon(actor, addon, data, *, request=None):
    _require(actor, "manage_products")
    changed = _apply(addon, data, ADDON_FIELDS)
    if not changed:
        return addon
    addon.full_clean()
    addon.save()
    audit.record("addon.updated", actor=actor, target=addon, metadata={"fields": sorted(changed)}, request=request)
    return addon


def set_addon_status(actor, addon, new_status, *, request=None):
    _require(actor, "manage_products")
    if new_status not in CatalogStatus.values:
        raise ServiceError("Unknown status.", code="invalid_status")
    previous = addon.status
    if previous == new_status:
        return addon
    addon.status = new_status
    addon.save(update_fields=["status", "updated_at"])
    audit.record("addon.status_changed", actor=actor, target=addon,
                 metadata={"from": previous, "to": new_status}, request=request)
    return addon


# --- Servers (Phase 05 extends this same model; see apps.products.models.Server) ----------

def _require_hosting(actor, codename):
    if not actor.has_perm(perm(codename)):
        raise _denied()


@transaction.atomic
def create_server(actor, data, *, request=None):
    _require_hosting(actor, "manage_hosting")
    server = Server(**{k: v for k, v in data.items() if k in SERVER_FIELDS})
    if data.get("api_token"):
        server.set_api_token(data["api_token"])
    server.full_clean()
    server.save()
    audit.record("server.created", actor=actor, target=server,
                 metadata={"kind": server.kind, "api_token_set": bool(data.get("api_token"))}, request=request)
    return server


@transaction.atomic
def update_server(actor, server, data, *, request=None):
    _require_hosting(actor, "manage_hosting")
    changed = _apply(server, data, SERVER_FIELDS)
    token_changed = bool(data.get("api_token"))
    if token_changed:
        server.set_api_token(data["api_token"])
    if not changed and not token_changed:
        return server
    server.full_clean()
    server.save()
    audit.record("server.updated", actor=actor, target=server,
                 metadata={"fields": sorted(changed), "api_token_changed": token_changed}, request=request)
    return server


def set_server_status(actor, server, new_status, *, request=None):
    _require_hosting(actor, "manage_hosting")
    from .models import ServerStatus

    if new_status not in ServerStatus.values:
        raise ServiceError("Unknown status.", code="invalid_status")
    previous = server.status
    if previous == new_status:
        return server
    server.status = new_status
    server.save(update_fields=["status", "updated_at"])
    audit.record("server.status_changed", actor=actor, target=server,
                 metadata={"from": previous, "to": new_status}, request=request)
    return server
