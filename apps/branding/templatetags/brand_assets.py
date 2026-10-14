"""The packaged brand pictures for pages that cannot ask the database (the 500 page, the maintenance page)."""
import base64

from django import template
from django.templatetags.static import static

from apps.branding import services

register = template.Library()


@register.simple_tag
def brand_asset_url(kind):
    """The web address of a packaged picture (``logo``, ``logo_dark``, ``mark_dark``, ``favicon``), or "" when it was not shipped."""
    found = services.packaged_file(kind)
    if found is None:
        return ""
    try:
        return static("img/brand/" + found.name)
    except Exception:  # noqa: BLE001 - a missing manifest entry must never break an error page
        return ""


@register.simple_tag
def brand_asset_data(kind):
    """The picture as a ``data:`` address, for a page that must not make any request of its own; "" when it was not shipped."""
    found = services.packaged_file(kind)
    if found is None:
        return ""
    content_type = services.PACKAGED_TYPES.get(found.suffix.lower(), "image/png")
    return f"data:{content_type};base64," + base64.b64encode(found.read_bytes()).decode()
