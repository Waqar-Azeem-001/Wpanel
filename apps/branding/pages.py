"""
The owner's own pages (Setup > Pages): About us, Terms, a guide. Shown at /p/<slug>/ when published and listed in the footer's Company column on request.
The built-in Contact page needs no setup: it shows the contact details already entered in Setup.
"""
import re

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils.html import escape, format_html_join
from django.utils.safestring import mark_safe
from rest_framework import status

from apps.accounts.roles import perm
from apps.audit import services as audit
from apps.core.exceptions import ServiceError

from .models import PublicPage

CACHE_KEY = "branding.pages.footer.v1"
RESERVED = {"contact", "pricing", "hosting", "admin", "account", "staff", "api", "help", "domains", "products", "static", "media", "brand", "r", "e"}


def footer_links():
    """[(title, address)] of the published pages that asked to be in the footer. Never raises."""
    try:
        found = cache.get(CACHE_KEY)
        if found is None:
            found = [(p.title, f"/p/{p.slug}/") for p in PublicPage.objects.filter(is_published=True, in_footer=True).order_by("sort_order", "title")[:8]]
            cache.set(CACHE_KEY, found, 300)
        return found
    except Exception:  # noqa: BLE001
        return []


def render_body(text):
    """The page's text as safe HTML: paragraphs, lines starting with # as headings, a plain web address as a link. Nothing else is ever allowed through."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n")) if b.strip()]
    out = []
    for block in blocks:
        if block.startswith("#"):
            out.append("<h2>" + escape(block.lstrip("#").strip()) + "</h2>")
            continue
        html = escape(block).replace("\n", "<br>")
        html = re.sub(r"(https?:" + r"//[^\s<]+)", r'<a href="\1" rel="noopener noreferrer">\1</a>', html)
        out.append("<p>" + html + "</p>")
    return mark_safe("".join(out))


def save(actor, page, values, request=None):
    """Staff (``manage_settings``): create (``page`` None) or change a page. Returns the page."""
    if not actor.has_perm(perm("manage_settings")):
        raise ServiceError("You do not have permission to perform this action.", code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)
    new = page is None
    page = page or PublicPage()
    slug = (values.get("slug") or "").strip().lower()
    if slug in RESERVED:
        raise ValidationError(f"\"{slug}\" is used by the site itself; choose another address.")
    page.slug, page.title = slug, (values.get("title") or "").strip()
    page.body = (values.get("body") or "").strip()
    page.is_published, page.in_footer = bool(values.get("is_published")), bool(values.get("in_footer"))
    try:
        page.sort_order = max(0, min(32000, int(values.get("sort_order") or 100)))
    except ValueError:
        raise ValidationError("The order must be a number.") from None
    if not page.body:
        raise ValidationError("Write something in the page.")
    page.full_clean(validate_unique=False)
    if PublicPage.objects.filter(slug=page.slug).exclude(pk=page.pk).exists():
        raise ValidationError("Another page already has that address.")
    page.save()
    cache.delete(CACHE_KEY)
    audit.record("branding.page_created" if new else "branding.page_saved", actor=actor, target=page, metadata={"slug": page.slug, "published": page.is_published}, request=request)
    return page


def delete(actor, page, request=None):
    if not actor.has_perm(perm("manage_settings")):
        raise ServiceError("You do not have permission to perform this action.", code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)
    audit.record("branding.page_deleted", actor=actor, target=page, metadata={"slug": page.slug}, request=request)
    page.delete()
    cache.delete(CACHE_KEY)
