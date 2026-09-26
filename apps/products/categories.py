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


def active():
    """The categories shown on the store, in order."""
    return list(StoreCategory.objects.filter(is_active=True).order_by("sort_order", "name"))


def get(slug):
    return StoreCategory.objects.filter(slug=slug, is_active=True).first()


def items(category):
    """The active products (or add-ons) of a category, cheapest first."""
    if category.kind == CategoryKind.PLANS:
        found = list(Product.objects.filter(status=CatalogStatus.ACTIVE, category=category).prefetch_related("prices"))
    elif category.kind == CategoryKind.ADDONS:
        found = list(Addon.objects.filter(status=CatalogStatus.ACTIVE, category=category).prefetch_related("prices"))
    else:
        return []

    def lowest(entry):
        prices = [p.price for p in entry.prices.all() if p.is_active]
        return (min(prices) if prices else float("inf"), entry.name)

    return sorted(found, key=lowest)


def lowest_price(entry):
    prices = [p for p in entry.prices.all() if p.is_active]
    return min(prices, key=lambda p: p.price) if prices else None


def uncategorised():
    """Active products nobody filed under a category (or filed under a hidden one): shown as "More plans"."""
    hidden = StoreCategory.objects.filter(is_active=False).values_list("pk", flat=True)
    found = Product.objects.filter(status=CatalogStatus.ACTIVE).prefetch_related("prices")
    found = [p for p in found if p.category_id is None or p.category_id in set(hidden)]
    return sorted(found, key=lambda p: (min([x.price for x in p.prices.all() if x.is_active] or [float("inf")]), p.name))


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
