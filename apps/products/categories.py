"""
The storefront's categories: what a visitor can shop for, in the order and grouping they expect.

A category is a view over the catalogue, not stored data: hosting categories list the active plans of one or more product types,
the SSL and WHOIS categories list the active add-ons of that kind, and Domains leads to the domain search. Staff fill a category
by creating products or add-ons of the right type or kind; an empty one says so instead of pretending.
"""
from dataclasses import dataclass, field

from django.utils.translation import gettext_lazy as _

from .models import Addon, AddonKind, CatalogStatus, Product, ProductType


@dataclass(frozen=True)
class Category:
    key: str
    label: object
    blurb: object
    icon: str
    group: str
    product_types: tuple = ()
    addon_kind: str = ""
    url_name: str = ""  # a category that is a page of its own (Domains)
    audience: str = field(default="")


HOSTING, SERVERS, SECURITY = _("Hosting"), _("Servers"), _("Security and domains")

CATEGORIES = (
    Category("wordpress", _("WordPress hosting"), _("Tuned for WordPress, ready in minutes."), "bi-wordpress", HOSTING,
             product_types=(ProductType.WORDPRESS_HOSTING,)),
    Category("shared", _("Shared hosting"), _("Everything a first website needs, at the lowest price."), "bi-hdd-stack", HOSTING,
             product_types=(ProductType.SHARED_HOSTING,)),
    Category("business", _("Business hosting"), _("More power and headroom for a growing business site."), "bi-briefcase", HOSTING,
             product_types=(ProductType.BUSINESS_HOSTING,)),
    Category("ecommerce", _("E-commerce hosting"), _("Built for online shops: fast pages, safe checkouts."), "bi-bag-check", HOSTING,
             product_types=(ProductType.ECOMMERCE_HOSTING,)),
    Category("reseller", _("Reseller hosting"), _("Host your own clients under your brand."), "bi-people", HOSTING,
             product_types=(ProductType.RESELLER_HOSTING,)),
    Category("vps", _("VPS servers"), _("Your own virtual server with full control."), "bi-cpu", SERVERS,
             product_types=(ProductType.VPS,)),
    Category("dedicated", _("Dedicated servers"), _("A whole machine, only yours."), "bi-server", SERVERS,
             product_types=(ProductType.DEDICATED_SERVER,)),
    Category("ssl", _("SSL certificates"), _("Encrypt your site and show the padlock."), "bi-shield-lock", SECURITY,
             addon_kind=AddonKind.SSL),
    Category("whois", _("WHOIS privacy"), _("Keep your personal details out of the public domain record."), "bi-eye-slash", SECURITY,
             addon_kind=AddonKind.WHOIS_PRIVACY),
    Category("domains", _("Domains"), _("Search, register or transfer a domain name."), "bi-globe2", SECURITY,
             url_name="domains_public:search"),
)
BY_KEY = {c.key: c for c in CATEGORIES}
GROUPS = tuple(dict.fromkeys(c.group for c in CATEGORIES))


def items(category):
    """The active products (or add-ons) of a category, cheapest first."""
    if category.product_types:
        found = list(Product.objects.filter(status=CatalogStatus.ACTIVE, type__in=category.product_types).prefetch_related("prices"))
    elif category.addon_kind:
        found = list(Addon.objects.filter(status=CatalogStatus.ACTIVE, kind=category.addon_kind).prefetch_related("prices"))
    else:
        return []

    def lowest(entry):
        prices = [p.price for p in entry.prices.all() if p.is_active]
        return (min(prices) if prices else float("inf"), entry.name)

    return sorted(found, key=lowest)


def lowest_price(entry):
    prices = [p for p in entry.prices.all() if p.is_active]
    return min(prices, key=lambda p: p.price) if prices else None


def sections():
    """[(group, [(category, items)])] for the whole storefront."""
    out = {group: [] for group in GROUPS}
    for category in CATEGORIES:
        out[category.group].append((category, items(category)))
    return list(out.items())
