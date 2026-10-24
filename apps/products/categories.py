"""
The storefront's categories: what a visitor can shop for, in the order and grouping staff chose.

Categories are data (``StoreCategory``): staff add, rename, reorder and hide them under Products › Categories, and put each product
or add-on in one. A plans category lists the active products in it, an add-ons category (SSL, WHOIS privacy) the active add-ons in
it, and a domains category leads to the domain search. An empty one says so instead of pretending. A product with no category is
still listed, under "More plans", so nothing is ever hidden by forgetting to file it.
"""
from django.utils.translation import gettext_lazy as _

from .models import Addon, CatalogStatus, CategoryKind, Product, StoreCategory

# What a new product or add-on is filed under when staff do not choose: its type or kind, matched to the standard categories.
DEFAULT_BY_TYPE = {"wordpress_hosting": "wordpress", "shared_hosting": "shared", "business_hosting": "business",
                   "ecommerce_hosting": "ecommerce", "reseller_hosting": "reseller", "vps": "vps", "dedicated_server": "dedicated"}
DEFAULT_BY_KIND = {"ssl": "ssl", "whois_privacy": "whois"}


def default_for_type(product_type):
    return StoreCategory.objects.filter(slug=DEFAULT_BY_TYPE.get(product_type, ""), is_active=True).first()


def default_for_kind(addon_kind):
    return StoreCategory.objects.filter(slug=DEFAULT_BY_KIND.get(addon_kind, ""), is_active=True).first()


SNAPSHOT_KEY = "catalog.snapshot.v1"
SNAPSHOT_SECONDS = 60


def _snapshot():
    """
    Everything the public store pages list, read once and remembered for a minute (and dropped the moment staff change a product, price,
    add-on or category): the active categories, the active plans and add-ons with their prices, grouped, so a page makes a handful of
    queries however many categories there are. Plain model instances with their prices already loaded.
    """
    from django.core.cache import cache

    found = cache.get(SNAPSHOT_KEY)
    if found is not None:
        return found
    categories = list(StoreCategory.objects.filter(is_active=True).order_by("sort_order", "name"))
    hidden = set(StoreCategory.objects.filter(is_active=False).values_list("pk", flat=True))
    plans = list(Product.objects.filter(status=CatalogStatus.ACTIVE).select_related("category").prefetch_related("prices"))
    addons = list(Addon.objects.filter(status=CatalogStatus.ACTIVE).select_related("category").prefetch_related("prices"))

    def lowest(entry):
        prices = [p.price for p in entry.prices.all() if p.is_active]
        return (min(prices) if prices else float("inf"), entry.name)

    by_category = {}
    for entry in sorted(plans, key=lowest):
        by_category.setdefault(("plans", entry.category_id), []).append(entry)
    for entry in sorted(addons, key=lowest):
        by_category.setdefault(("addons", entry.category_id), []).append(entry)
    loose = [p for p in sorted(plans, key=lowest) if p.category_id is None or p.category_id in hidden]
    found = {"categories": categories, "items": by_category, "uncategorised": loose}
    cache.set(SNAPSHOT_KEY, found, SNAPSHOT_SECONDS)
    return found


def forget_snapshot(*args, **kwargs):
    """Called when a product, price, add-on or category is saved or deleted: the next page reads the new catalogue."""
    from django.core.cache import cache

    cache.delete(SNAPSHOT_KEY)


def active():
    """The categories shown on the store, in order."""
    return list(_snapshot()["categories"])


def get(slug):
    return next((c for c in _snapshot()["categories"] if c.slug == slug), None)


def items(category):
    """The active products (or add-ons) of a category, cheapest first."""
    if category.kind == CategoryKind.PLANS:
        return list(_snapshot()["items"].get(("plans", category.pk), []))
    if category.kind == CategoryKind.ADDONS:
        return list(_snapshot()["items"].get(("addons", category.pk), []))
    return []


def lowest_price(entry):
    prices = [p for p in entry.prices.all() if p.is_active]
    return min(prices, key=lambda p: p.price) if prices else None


def uncategorised():
    """Active products nobody filed under a category (or filed under a hidden one): shown as "More plans"."""
    return list(_snapshot()["uncategorised"])


def sections():
    """[(group, [(category, items)])] for the whole store, groups in the order of their first category."""
    out = {}
    for category in active():
        out.setdefault(category.group, []).append((category, items(category)))
    extra = uncategorised()
    if extra:
        more = StoreCategory(name=str(_("More plans")), slug="more", short_name="", blurb="", icon="bi-box-seam",
                             group=str(_("More")), kind=CategoryKind.PLANS)
        out.setdefault(more.group, []).append((more, extra))
    return list(out.items())
