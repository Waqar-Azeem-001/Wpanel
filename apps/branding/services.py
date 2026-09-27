"""
Brand settings: reading them cheaply on every request, and changing them safely.

* ``get()`` is called for every page (the context processor), so the text settings are cached and the cache is cleared
  on every change. If the cache or database misbehaves it falls back to the defaults: a page must never fail because
  the brand could not be read.
* Colours are checked for **contrast**: buttons put white text on the primary colour, so a colour that white text
  cannot be read on (WCAG AA, 4.5:1) is refused. The darker hover colour and the tint are derived from it.
* Logo and favicon are uploads, so they are validated by *content* (signature and a real image header), size-limited,
  and never SVG (an SVG can carry script). They are stored in the database and served from named routes.
"""
import logging
from dataclasses import dataclass
from io import BytesIO

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework import status

from apps.accounts.roles import perm
from apps.audit import services as audit
from apps.core.exceptions import ServiceError

from .models import BrandSettings

logger = logging.getLogger(__name__)

CACHE_KEY = "branding.settings.v1"
MENU_CACHE_KEY = "branding.menu.v1"
TEXT_FIELDS = ("site_name", "primary_color", "accent_color", "support_email", "footer_text", "date_format",
               "money_format", "theme", "custom_look")
# The ready-made looks: each has its own colours, type, corners and header (see static/css/looks.css). "custom" keeps the colours typed in
# the settings and borrows the structure of one of these.
LOOKS = {
    "signature": {"label": "Signature", "primary": "#2a6af2", "accent": "#c8fc35",
                  "tagline": "Modern and confident. Royal blue with a lime highlight, crisp corners, a bright header."},
    "heritage": {"label": "Heritage", "primary": "#0f5c46", "accent": "#c9a24a",
                 "tagline": "Classic and calm. Deep green and warm gold on ivory, serif headings, fine hairlines."},
    "aura": {"label": "Aura", "primary": "#6a2c91", "accent": "#f0a8c8",
             "tagline": "Soft and refined. Plum and rose on lavender, rounded shapes, a floating header."},
}
MIN_CONTRAST = 4.5
IMAGE_LIMITS = {"logo": (512 * 1024, 2048), "favicon": (128 * 1024, 512), "logo_dark": (512 * 1024, 2048),
                "mark_dark": (128 * 1024, 512)}  # (bytes, longest side in pixels)
IMAGE_LABELS = {"logo": "logo", "favicon": "favicon", "logo_dark": "logo for dark backgrounds", "mark_dark": "icon for dark backgrounds"}
SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png", "PNG"), (b"\xff\xd8\xff", "image/jpeg", "JPEG"),
    (b"GIF87a", "image/gif", "GIF"), (b"GIF89a", "image/gif", "GIF"), (b"\x00\x00\x01\x00", "image/x-icon", "ICO"),
)


# --- Colour maths ---------------------------------------------------------------------------------------------------

def _rgb(hex_color):
    value = hex_color.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _hex(rgb):
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def mix(hex_color, other, amount):
    """``hex_color`` moved ``amount`` (0-1) of the way towards ``other`` (a hex colour)."""
    a, b = _rgb(hex_color), _rgb(other)
    return _hex(tuple(x + (y - x) * amount for x, y in zip(a, b)))


def _luminance(hex_color):
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in _rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(first, second):
    lighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


# --- Reading ----------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Brand:
    name: str
    primary: str
    primary_dark: str
    primary_rgb: str
    primary_subtle: str
    primary_light_rgb: str
    accent_text: str
    accent: str
    support_email: str
    footer_text: str
    date_format: str
    money_format: str
    has_logo: bool
    has_favicon: bool
    version: int
    has_logo_dark: bool = False
    has_mark_dark: bool = False
    policy_links: tuple = ()
    theme: str = "signature"
    look: str = "signature"  # the structure in use: the ready-made look, or the one a custom brand borrows


def _brand_from(row, has_logo, has_favicon, has_logo_dark=False, has_mark_dark=False):
    theme = row.get("theme") or "custom"
    if theme in LOOKS:  # a ready-made look brings its own colours; "custom" keeps the ones typed in
        primary, accent, look = LOOKS[theme]["primary"], LOOKS[theme]["accent"], theme
    else:
        primary, accent, look = row["primary_color"], row["accent_color"], row.get("custom_look") or "signature"
        theme = "custom"
    return Brand(
        name=row["site_name"] or settings.SITE_NAME, primary=primary, primary_dark=mix(primary, "#000000", 0.18),
        primary_rgb=", ".join(str(c) for c in _rgb(primary)), primary_subtle=mix(primary, "#ffffff", 0.9),
        primary_light_rgb=", ".join(str(c) for c in _rgb(mix(primary, "#ffffff", 0.45))),
        accent=accent, accent_text=readable_text(accent), support_email=row["support_email"], footer_text=row["footer_text"],
        date_format=row["date_format"], money_format=row["money_format"], has_logo=has_logo,
        has_favicon=has_favicon, version=row["version"], has_logo_dark=has_logo_dark, has_mark_dark=has_mark_dark,
        policy_links=tuple((p["title"], p["url"]) for p in row.get("policy_links") or ()), theme=theme, look=look)


DEFAULTS = {"site_name": "", "primary_color": "#2459d6", "accent_color": "#1f7a4d", "support_email": "", "theme": "custom",
            "custom_look": "signature",
            "footer_text": "", "date_format": "%d %b %Y", "money_format": "code_before", "version": 1}


def get():
    """The current brand. Cheap (cached) and never raises."""
    try:
        cached = cache.get(CACHE_KEY)
        if cached is None:
            row = BrandSettings.load()
            has_logo = BrandSettings.objects.filter(pk=1, logo_type__gt="").exists()
            has_favicon = BrandSettings.objects.filter(pk=1, favicon_type__gt="").exists()
            cached = {**{f: getattr(row, f) for f in TEXT_FIELDS}, "version": row.version, "has_logo": has_logo,
                      "has_favicon": has_favicon, "has_logo_dark": bool(row.logo_dark_type), "has_mark_dark": bool(row.mark_dark_type),
                      "policy_links": list(row.policy_links or [])}
            cache.set(CACHE_KEY, cached, 300)
        return _brand_from(cached, cached["has_logo"], cached["has_favicon"], cached.get("has_logo_dark", False),
                           cached.get("has_mark_dark", False))
    except Exception:  # noqa: BLE001 - a page must not fail because the brand could not be read
        logger.exception("Could not read the brand settings; using the defaults")
        return _brand_from(DEFAULTS, False, False)


def image(kind):
    """(bytes, content type) of the stored logo or favicon, or None."""
    row = BrandSettings.objects.filter(pk=1).only(kind, f"{kind}_type").first()
    data = getattr(row, kind) if row else None
    if not data:
        return None
    return bytes(data), getattr(row, f"{kind}_type")


# --- Changing ---------------------------------------------------------------------------------------------------------

def validate_image(kind, data):
    """Returns the content type of a valid ``kind`` ("logo" or "favicon") upload, or raises ValidationError."""
    max_bytes, max_side = IMAGE_LIMITS[kind]
    label = IMAGE_LABELS[kind]
    if not data:
        raise ValidationError(f"The {label} file is empty.")
    if len(data) > max_bytes:
        raise ValidationError(f"The {label} must be at most {max_bytes // 1024} KB.")
    content_type = next((t for sig, t, _ in SIGNATURES if data.startswith(sig)), None)
    if content_type is None and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        content_type = "image/webp"
    if content_type is None or (content_type == "image/x-icon" and kind in ("logo", "logo_dark")):
        raise ValidationError(f"The {label} must be a PNG, JPEG, GIF or WebP image"
                              f"{' (or ICO)' if kind in ('favicon', 'mark_dark') else ''}. SVG is not accepted.")
    try:
        from PIL import Image

        with Image.open(BytesIO(data)) as img:
            width, height = img.size
            img.verify()
    except Exception:  # noqa: BLE001 - anything Pillow cannot read as an image is refused
        raise ValidationError(f"That {label} file is not a valid image.")
    if max(width, height) > max_side:
        raise ValidationError(f"The {label} can be at most {max_side} pixels on its longest side.")
    return content_type


MAX_POLICY_LINKS = 12


def clean_policy_links(rows):
    """[{"title", "url"}] from what was typed: empty rows are dropped, an address must be a web address, a repeat is dropped."""
    from urllib.parse import urlparse

    clean, seen = [], set()
    for row in rows or ():
        title, url = (row.get("title") or "").strip(), (row.get("url") or "").strip()
        if not title and not url:
            continue
        if not title:
            raise ValidationError("Give each policy link a title.")
        if len(title) > 60:
            raise ValidationError(f"The title \"{title[:20]}...\" is longer than 60 characters.")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or len(url) > 300 or " " in url:
            raise ValidationError(f"\"{title}\" needs a web address that starts with http or https.")
        if url in seen:
            continue
        seen.add(url)
        clean.append({"title": title, "url": url})
    if len(clean) > MAX_POLICY_LINKS:
        raise ValidationError(f"At most {MAX_POLICY_LINKS} policy links.")
    return clean


DARK_TEXT = "#14213d"


def readable_text(color):
    """The text colour to put on ``color``: white, or the dark ink when white would not be readable."""
    return "#ffffff" if contrast_ratio(color, "#ffffff") >= MIN_CONTRAST else DARK_TEXT


def _check_colors(values):
    color = values.get("primary_color")
    if color and contrast_ratio(color, "#ffffff") < MIN_CONTRAST:
        raise ValidationError({"primary_color": f"The primary colour is too light: white text on it would be hard to read "
                                                f"(contrast {contrast_ratio(color, '#ffffff'):.1f}:1, at least "
                                                f"{MIN_CONTRAST}:1 needed). Choose a darker colour."})
    accent = values.get("accent_color")  # the accent carries white text, or dark text if it is light; a mid-tone carries neither
    if accent and max(contrast_ratio(accent, "#ffffff"), contrast_ratio(accent, DARK_TEXT)) < MIN_CONTRAST:
        raise ValidationError({"accent_color": "The accent colour is a mid-tone: neither white nor dark text is easy to "
                                               f"read on it (at least {MIN_CONTRAST}:1 needed). Choose a lighter or darker colour."})


@transaction.atomic
def save_settings(actor, *, values=None, logo=None, favicon=None, remove_logo=False, remove_favicon=False,
                  logo_dark=None, mark_dark=None, remove_logo_dark=False, remove_mark_dark=False, policy_links=None, request=None):
    """Staff (``manage_settings``): change the brand. ``logo`` / ``favicon`` are the uploaded file bytes."""
    if not actor.has_perm(perm("manage_settings")):
        raise ServiceError("You do not have permission to perform this action.", code="permission_denied",
                           status_code=status.HTTP_403_FORBIDDEN)
    values = {k: v for k, v in (values or {}).items() if k in TEXT_FIELDS}
    row = BrandSettings.objects.select_for_update().get_or_create(pk=1)[0]
    before = {f: getattr(row, f) for f in TEXT_FIELDS}
    for name, value in values.items():
        setattr(row, name, (value or "").strip() if isinstance(value, str) else value)
    row.full_clean()
    _check_colors({f: getattr(row, f) for f in ("primary_color", "accent_color")})
    changed = [f for f in TEXT_FIELDS if before[f] != getattr(row, f)]
    if "theme" not in values and row.theme in LOOKS and any(c in values and getattr(row, c) != before[c] for c in ("primary_color", "accent_color")):
        row.custom_look, row.theme = row.theme, "custom"  # typing colours in (say through the API) means your own colours, on the look in use
        changed.append("theme")
    for kind, data, remove in (("logo", logo, remove_logo), ("favicon", favicon, remove_favicon),
                               ("logo_dark", logo_dark, remove_logo_dark), ("mark_dark", mark_dark, remove_mark_dark)):
        if data:
            setattr(row, kind, data)
            setattr(row, f"{kind}_type", validate_image(kind, data))
            changed.append(kind)
        elif remove and getattr(row, f"{kind}_type"):
            setattr(row, kind, None)
            setattr(row, f"{kind}_type", "")
            changed.append(f"{kind} removed")
    if policy_links is not None:
        links = clean_policy_links(policy_links)
        if links != list(row.policy_links or []):
            row.policy_links = links
            changed.append("policy links")
    if changed:
        row.version += 1
        row.save()
        cache.delete(CACHE_KEY)
        audit.record("brand.updated", actor=actor, target=row, metadata={"changed": changed}, request=request)
    return row


# --- The customer menu (Super Admin) -------------------------------------------------------------------------------------------

def menu_settings(area="client"):
    """{key: (label, is_visible, sort_order)} for the entries a Super Admin changed. Cached; cleared on every save."""
    from .models import MenuSetting

    try:
        cached = cache.get(MENU_CACHE_KEY)
        if cached is None:
            cached = {}
            for row in MenuSetting.objects.all():
                cached.setdefault(row.area, {})[row.key] = (row.label, row.is_visible, row.sort_order)
            cache.set(MENU_CACHE_KEY, cached, 300)
        return cached.get(area, {})
    except Exception:  # noqa: BLE001 - a menu must never fail because its settings could not be read
        logger.exception("Could not read the menu settings; the standard menu is used")
        return {}


def save_menu(actor, rows, *, area="client", request=None):
    """
    ``rows``: ``{key: {"label", "visible", "order"}}``. Only entries that differ from the standard are stored. The Overview entry cannot be
    hidden (a customer must always have a way home). Needs ``manage_settings``.
    """
    from django.db import transaction

    from apps.audit import services as audit

    from .models import MenuSetting

    _require_settings(actor)
    changed = 0
    with transaction.atomic():
        for key, row in rows.items():
            label = (row.get("label") or "").strip()[:60]
            visible = bool(row.get("visible")) or key == "client.home"
            order = row.get("order")
            if label or not visible or order is not None:
                MenuSetting.objects.update_or_create(area=area, key=key, defaults={"label": label, "is_visible": visible,
                                                                                     "sort_order": order})
                changed += 1
            else:
                MenuSetting.objects.filter(area=area, key=key).delete()
    cache.delete(MENU_CACHE_KEY)
    audit.record("menu.saved", actor=actor, metadata={"area": area, "customised": changed}, request=request)
    return changed


def reset_menu(actor, *, area="client", request=None):
    from apps.audit import services as audit

    from .models import MenuSetting

    _require_settings(actor)
    MenuSetting.objects.filter(area=area).delete()
    cache.delete(MENU_CACHE_KEY)
    audit.record("menu.reset", actor=actor, metadata={"area": area}, request=request)


def _require_settings(actor):
    from rest_framework import status

    from apps.accounts.roles import perm
    from apps.core.exceptions import ServiceError

    if not actor.has_perm(perm("manage_settings")):
        raise ServiceError("You do not have permission to perform this action.", code="permission_denied",
                           status_code=status.HTTP_403_FORBIDDEN)
