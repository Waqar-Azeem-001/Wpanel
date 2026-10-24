"""
What the public top bar's menus list. Every entry is a page that really exists and is built from what we actually sell and publish: the store categories (grouped under the
heading staff gave them), the domain pages, and the knowledgebase categories that have a published article. Nothing is a placeholder: an empty menu is
left out, so there is never a dead link or an empty promise.
"""
from collections import OrderedDict

from django.urls import reverse
from django.utils.translation import gettext as _

from . import categories
from .models import CategoryKind


def _category_url(category):
    if category.kind == CategoryKind.DOMAINS:
        return reverse("domains_public:search")
    return reverse("catalog:product_list") + f"?category={category.slug}"


def products():
    """[{"title", "items": [{"label", "about", "icon", "url"}]}]: the store categories under their headings, then a Domains column."""
    groups = OrderedDict()
    for category in categories.active():
        entry = {"label": category.name, "about": category.blurb, "icon": category.icon, "url": _category_url(category)}
        groups.setdefault(category.group or _("More"), []).append(entry)
    columns = [{"title": title, "items": items} for title, items in groups.items()]
    has_domains = any(item["url"] == reverse("domains_public:search") for column in columns for item in column["items"])
    domains = [{"label": _("Domain prices"), "about": _("Every extension with its price."), "icon": "bi-tags", "url": reverse("pricing") + "#domains"}]
    if not has_domains:
        domains.insert(0, {"label": _("Register a domain"), "about": _("Search for the name you want."), "icon": "bi-globe2", "url": reverse("domains_public:search")})
    domains.append({"label": _("Add-ons"), "about": _("SSL, privacy and other extras."), "icon": "bi-plus-square", "url": reverse("catalog:addons")})
    columns.append({"title": _("Domains and more"), "items": domains})
    return columns


def resources():
    """The help centre and its categories that have at least one published article."""
    try:
        from apps.support import kb

        found = [{"label": c.name, "about": c.description, "url": reverse("help:category", args=[c.slug])} for c in kb.public_category_list()[:6]]
    except Exception:  # noqa: BLE001 - a menu must never break a page
        found = []
    return [{"label": _("Help centre"), "about": _("Guides and answers."), "url": reverse("help:index")}] + found


def build():
    return {"products": products(), "resources": resources(), "pricing_url": reverse("pricing")}
