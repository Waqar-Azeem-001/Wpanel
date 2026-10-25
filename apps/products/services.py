"""
Catalog business logic. Staff API, staff web pages, the public catalog and the
admin all call these functions; none of them holds pricing or visibility rules
of its own.
"""
import re

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import ProtectedError, Q

from apps.accounts.roles import perm
from apps.audit import services as audit
from apps.core.exceptions import ServiceError
from apps.core.utils import unique_slug

from . import categories
from .models import (LIMIT_LABELS, Addon, AddonKind, AddonPrice, BillingCycle, CatalogStatus, Product, ProductPrice, ProductType, Server,
                     StoreCategory)

PRODUCT_FIELDS = (
    "name", "description", "type", "resource_limits", "limit_order", "whm_package_name", "auto_setup", "default_auto_renew",
    "upsell_product", "category", "publish_externally",
)
ADDON_FIELDS = ("name", "description", "kind", "category")
CATEGORY_FIELDS = ("name", "short_name", "blurb", "icon", "group", "kind", "sort_order", "is_active")
SERVER_FIELDS = ("name", "hostname", "ip_address", "region", "max_accounts", "notes", "kind", "api_port", "api_username",
                 "use_ssl", "verify_ssl", "infra_provider", "infra_client_id", "infra_api_user")


def _set_infra_secrets(server, data):
    """Store the provider's secrets when new ones are given (blank keeps what is stored). Returns what changed, by name only."""
    changed = []
    if data.get("infra_client_secret"):
        server.set_infra_client_secret(data["infra_client_secret"])
        changed.append("client_secret")
    if data.get("infra_api_password"):
        server.set_infra_api_password(data["infra_api_password"])
        changed.append("api_password")
    return changed


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
    if product.category_id is None:  # filed by its type unless staff chose otherwise, so it shows on the store
        product.category = categories.default_for_type(product.type)
    product.full_clean()
    product.save()
    if "recommended_addons" in data:
        product.recommended_addons.set(data["recommended_addons"])
    _first_price(actor, product, data, request)
    audit.record("product.created", actor=actor, target=product, metadata={"type": product.type}, request=request)
    return product


def _first_price(actor, item, data, request):
    """Creating something can include its first price (the form's price box): cycle, price, optional setup fee."""
    if data.get("price") is None or data.get("price") == "":
        return None
    return set_price(actor, item, billing_cycle=data.get("billing_cycle") or BillingCycle.MONTHLY, price=data["price"],
                     custom_months=data.get("custom_months") or 0, setup_fee=data.get("setup_fee") or 0, request=request)


@transaction.atomic
def update_product(actor, product, data, *, request=None):
    _require(actor, "manage_products")
    changed = _apply(product, data, PRODUCT_FIELDS)
    addons_changed = ("recommended_addons" in data
                      and set(a.pk for a in data["recommended_addons"]) != set(product.recommended_addons.values_list("pk", flat=True)))
    if not changed and not addons_changed:
        return product
    product.full_clean()
    product.save()
    if addons_changed:
        product.recommended_addons.set(data["recommended_addons"])
        changed["recommended_addons"] = True
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
    addon = Addon(name=name, slug=unique_slug(Addon.objects, name), description=data.get("description", ""),
                  kind=data.get("kind") or AddonKind.GENERAL, category=data.get("category"))
    if addon.category_id is None:
        addon.category = categories.default_for_kind(addon.kind)
    addon.full_clean()
    addon.save()
    _first_price(actor, addon, data, request)
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


# --- Store categories -----------------------------------------------------------------------------------------

@transaction.atomic
def save_category(actor, category, data, *, request=None):
    """Create (``category=None``) or change a store category."""
    _require(actor, "manage_products")
    created = category is None
    category = category or StoreCategory(slug=unique_slug(StoreCategory.objects, data.get("name", "")))
    changed = _apply(category, data, CATEGORY_FIELDS)
    if not created and not changed:
        return category
    category.full_clean()
    category.save()
    audit.record("store_category.created" if created else "store_category.updated", actor=actor, target=category,
                 metadata={"fields": sorted(changed)}, request=request)
    return category


@transaction.atomic
def delete_category(actor, category, *, request=None):
    """Remove an empty category. One that still holds products or add-ons is hidden instead (its things would lose their heading)."""
    _require(actor, "manage_products")
    held = category.products.count() + category.addons.count()
    if held:
        raise ServiceError(f"{held} product(s) or add-on(s) are still in this category. Move them first, or hide the category "
                           "instead of deleting it.", code="category_in_use")
    audit.record("store_category.deleted", actor=actor, metadata={"name": category.name}, request=request)
    category.delete()


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
    infra_secrets = _set_infra_secrets(server, data)
    server.full_clean()
    server.save()
    audit.record("server.created", actor=actor, target=server,
                 metadata={"kind": server.kind, "api_token_set": bool(data.get("api_token")),
                           "infra_provider": server.infra_provider, "infra_secrets_set": infra_secrets}, request=request)
    return server


@transaction.atomic
def update_server(actor, server, data, *, request=None):
    _require_hosting(actor, "manage_hosting")
    changed = _apply(server, data, SERVER_FIELDS)
    token_changed = bool(data.get("api_token"))
    if token_changed:
        server.set_api_token(data["api_token"])
    infra_secrets = _set_infra_secrets(server, data)
    if not changed and not token_changed and not infra_secrets:
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


def delete_server(actor, server, *, request=None):
    """Remove a server with no hosting accounts on it. One that is in use is refused (the accounts on it would lose
    their server), the same way a category still holding products or add-ons is refused rather than deleted."""
    _require_hosting(actor, "manage_hosting")
    name = server.name
    try:
        server.delete()
    except ProtectedError:
        raise ServiceError("This server still has hosting accounts on it. Move them to another server first.",
                           code="server_in_use")
    audit.record("server.deleted", actor=actor, metadata={"name": name}, request=request)


# --- Plans from a server -------------------------------------------------------------------------------------------------

def type_for_package(package):
    """The kind of product a server package most likely is, from its name, or "" when it is not clear (then it is not suggested). A name with
    an underscore (``owner_plan``) is a reseller's own package, never suggested."""
    lowered = package.lower()
    if "_" in lowered:
        return ""
    for prefix, kind in (("reseller", ProductType.RESELLER_HOSTING), ("wordpress", ProductType.WORDPRESS_HOSTING), ("business", ProductType.BUSINESS_HOSTING),
                         ("ecommerce", ProductType.ECOMMERCE_HOSTING), ("e-commerce", ProductType.ECOMMERCE_HOSTING), ("shared", ProductType.SHARED_HOSTING)):
        if lowered.startswith(prefix):
            return kind
    return ""


FEATURE_ORDER = ["disk_mb", "bandwidth_mb", "email_accounts", "databases", "ftp_accounts", "subdomains", "addon_domains", "parked_domains",
                 "mailing_lists", "emails_per_hour", "shell_access", "dedicated_ip"]


def features_of(details):
    """(limits, order) for a product, from what a server package includes. A missing number means unlimited (None)."""
    limits = {key: details.get(key) for key in FEATURE_ORDER if key in details}
    return limits, [key for key in FEATURE_ORDER if key in limits]


def feature_lines(limits):
    """The plan's features as short lines for its description, for example '50 GB storage' and 'Unlimited email accounts'."""
    def number(key, one, many):
        value = limits.get(key, "missing")
        if value == "missing" or value == 0:  # a plan with none of something does not advertise it
            return None
        return f"Unlimited {many}" if value is None else f"{value} {one if value == 1 else many}"

    lines = []
    for key, one, many in (("databases", "database", "databases"), ("email_accounts", "email account", "email accounts"),
                           ("ftp_accounts", "FTP account", "FTP accounts"), ("subdomains", "subdomain", "subdomains"),
                           ("addon_domains", "addon domain", "addon domains")):
        text = number(key, one, many)
        if text:
            lines.append(text)
    disk, bandwidth = limits.get("disk_mb", "missing"), limits.get("bandwidth_mb", "missing")

    def size(mb):
        return f"{mb // 1024} GB" if mb % 1024 == 0 and mb >= 1024 else f"{mb} MB"

    head = []
    if disk != "missing":
        head.append("Unlimited storage" if disk is None else f"{size(disk)} storage")
    if bandwidth != "missing":
        head.append("Unlimited bandwidth" if bandwidth is None else f"{size(bandwidth)} bandwidth")
    if limits.get("shell_access") == "Yes":
        lines.append("Shell access")
    return head + lines


FEATURE_WORDS = re.compile(r"\b(storage|web ?space|disk|bandwidth|traffic|databases?|e-?mail accounts?|mailboxes|ftp accounts?|subdomains?|addon domains?|"
                           r"shell access|dedicated ip)\b|^\W*(\d+|unlimited)\s+(parked\s+)?domains?\b", re.I)  # "10 domains" is a feature; "Free domain name" is not


def merge_description(old, limits):
    """
    The plan's description lines with the feature lines (storage, bandwidth, databases, mailboxes...) replaced by what the server says, in the
    same words the store uses, and every other line (free domain, support, anything staff wrote) kept as it was after them.
    """
    kept = [line for line in (old or "").splitlines() if line.strip() and not FEATURE_WORDS.search(line)]
    return "\n".join(feature_lines(limits) + kept)


def plans_report(server, packages, details=None):
    """
    What differs between a server's packages and the catalog: ``broken`` are products mapped to this server whose package the server does not
    have (they would fail at provisioning); ``unlinked`` are server packages no product uses yet, each with a suggested product type and the
    features it includes; ``linked`` pairs a package with the products that use it and says which of them no longer show the server's features.
    """
    details = details or {}
    products = list(Product.objects.exclude(whm_package_name="").prefetch_related("servers").order_by("name"))
    by_package = {}
    for product in products:
        by_package.setdefault(product.whm_package_name, []).append(product)
    broken = [p for p in products if server in p.servers.all() and p.whm_package_name not in packages]
    unlinked = [{"package": name, "type": type_for_package(name), "features": _shown(details.get(name))} for name in packages if name not in by_package]
    linked = []
    for name in packages:
        if name in by_package:
            wanted = features_of(details[name])[0] if name in details else None
            stale = [p for p in by_package[name] if wanted is not None
                     and ((p.resource_limits or {}) != wanted or p.description != merge_description(p.description, wanted))]
            linked.append({"package": name, "products": by_package[name], "stale": stale, "features": _shown(details.get(name))})
    return {"broken": broken, "unlinked": unlinked, "linked": linked}


def _shown(details):
    """[(title, value)] for showing a package's features on screen."""
    limits = features_of(details or {})[0]
    return [(LIMIT_LABELS.get(key, key), "Unlimited" if value is None else value) for key, value in limits.items()]


@transaction.atomic
def apply_server_plans(actor, server, packages, *, remap=None, create=None, details=None, refresh=None, request=None):
    """
    Bring the catalog in line with a server's packages. ``remap`` is {product id: package} for products pointing at a package the server does
    not have; ``create`` lists server packages to turn into new products; ``refresh`` lists product ids whose features (disk, bandwidth,
    mailboxes, databases...) are copied again from their package. New products are created HIDDEN with no price, so nothing reaches the store
    until staff price and publish them (a server does not know prices). Only packages the server really has are accepted.
    Returns (remapped, created, refreshed).
    """
    _require(actor, "manage_products")
    details = details or {}
    known = set(packages)
    remapped, created, refreshed = [], [], []
    for product_id, package in (remap or {}).items():
        if not package:
            continue
        if package not in known:
            raise ServiceError(f"The server has no package called '{package}'.", code="unknown_package")
        product = Product.objects.get(pk=product_id)
        old = product.whm_package_name
        product.whm_package_name = package
        product.save(update_fields=["whm_package_name", "updated_at"])
        product.servers.add(server)
        audit.record("product.package_remapped", actor=actor, target=product, metadata={"from": old, "to": package, "server": server.name},
                     request=request)
        remapped.append(product)
    for package in create or []:
        if package not in known:
            raise ServiceError(f"The server has no package called '{package}'.", code="unknown_package")
        if Product.objects.filter(whm_package_name=package).exists():
            continue  # something already uses it; never make a twin
        kind = type_for_package(package) or ProductType.SHARED_HOSTING
        data = {"name": package, "type": kind, "whm_package_name": package}
        if package in details:
            data["resource_limits"], data["limit_order"] = features_of(details[package])
            data["description"] = "\n".join(feature_lines(data["resource_limits"]))
        product = create_product(actor, data, request=request)
        product.servers.add(server)
        audit.record("product.created_from_server", actor=actor, target=product, metadata={"package": package, "server": server.name},
                     request=request)
        created.append(product)
    for product in Product.objects.filter(pk__in=refresh or []):
        if product.whm_package_name not in details:
            continue
        limits, order = features_of(details[product.whm_package_name])
        update_product(actor, product, {"resource_limits": limits, "limit_order": order, "description": merge_description(product.description, limits)},
                       request=request)
        audit.record("product.features_synced", actor=actor, target=product, metadata={"package": product.whm_package_name, "server": server.name},
                     request=request)
        refreshed.append(product)
    return remapped, created, refreshed


# --- Prices from WHMCS ------------------------------------------------------------------------------------------------------------

def whmcs_preview(rows):
    """
    Each WHMCS product beside the WHPanel product it most likely is (same name, ignoring case and punctuation, or the same WHM package name), with
    the prices WHPanel has now and the ones WHMCS has, and what would change. Nothing is written.
    """
    from .whmcs import normal

    products = list(Product.objects.prefetch_related("prices").order_by("name"))
    by_name = {}
    for product in products:
        by_name.setdefault(normal(product.name), product)
        if product.whm_package_name:
            by_name.setdefault(normal(product.whm_package_name), product)
    shown = []
    for row in rows:
        match = by_name.get(normal(row["name"]))
        current = {}
        if match:
            for entry in match.prices.all():
                key = f"custom:{entry.custom_months}" if entry.billing_cycle == BillingCycle.CUSTOM else entry.billing_cycle
                current[key] = str(entry.price)
        changes = [key for key, value in row["prices"].items() if current.get(key) != value["price"]]
        shown.append({**row, "match": match, "current": current, "changes": changes})
    return {"rows": shown, "products": products}


def apply_whmcs_prices(actor, choices, *, request=None):
    """Write the chosen WHMCS prices (see ``_apply_whmcs_prices``). Two clicks at once: the second waits, then writes the same prices again, which changes nothing."""
    from apps.core.locks import single_flight

    with single_flight("whmcs-price-import"):  # outside the transaction, so the second one sees the first one's committed prices
        return _apply_whmcs_prices(actor, choices, request=request)


@transaction.atomic
def _apply_whmcs_prices(actor, choices, *, request=None):
    """
    ``choices`` is ``[(product id, WHMCS prices)]``. Sets (or replaces) the price of each cycle WHMCS offers; a cycle WHMCS does not offer is left as
    it is. Returns the number of prices written. Names, features, status and everything else about a product are never touched.
    """
    _require(actor, "manage_products")
    written = 0
    for product_id, prices in choices:
        product = Product.objects.get(pk=product_id)
        for value in prices.values():
            set_price(actor, product, billing_cycle=value["cycle"], price=value["price"], custom_months=value["custom_months"],
                      setup_fee=value["setup"], request=request)
            written += 1
        audit.record("product.prices_imported", actor=actor, target=product, metadata={"source": "whmcs", "cycles": sorted(prices)}, request=request)
    return written
