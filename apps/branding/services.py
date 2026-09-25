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
TEXT_FIELDS = ("site_name", "primary_color", "accent_color", "support_email", "footer_text", "date_format",
               "money_format")
MIN_CONTRAST = 4.5
IMAGE_LIMITS = {"logo": (512 * 1024, 2048), "favicon": (128 * 1024, 512)}  # (bytes, longest side in pixels)
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


def _brand_from(row, has_logo, has_favicon):
    primary = row["primary_color"]
    return Brand(
        name=row["site_name"] or settings.SITE_NAME, primary=primary, primary_dark=mix(primary, "#000000", 0.18),
        primary_rgb=", ".join(str(c) for c in _rgb(primary)), primary_subtle=mix(primary, "#ffffff", 0.9),
        primary_light_rgb=", ".join(str(c) for c in _rgb(mix(primary, "#ffffff", 0.45))),
        accent=row["accent_color"], accent_text=readable_text(row["accent_color"]), support_email=row["support_email"], footer_text=row["footer_text"],
        date_format=row["date_format"], money_format=row["money_format"], has_logo=has_logo,
        has_favicon=has_favicon, version=row["version"])


DEFAULTS = {"site_name": "", "primary_color": "#2459d6", "accent_color": "#1f7a4d", "support_email": "",
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
                      "has_favicon": has_favicon}
            cache.set(CACHE_KEY, cached, 300)
        return _brand_from(cached, cached["has_logo"], cached["has_favicon"])
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
    label = "logo" if kind == "logo" else "favicon"
    if not data:
        raise ValidationError(f"The {label} file is empty.")
    if len(data) > max_bytes:
        raise ValidationError(f"The {label} must be at most {max_bytes // 1024} KB.")
    content_type = next((t for sig, t, _ in SIGNATURES if data.startswith(sig)), None)
    if content_type is None and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        content_type = "image/webp"
    if content_type is None or (content_type == "image/x-icon" and kind == "logo"):
        raise ValidationError(f"The {label} must be a PNG, JPEG, GIF or WebP image"
                              f"{' (or ICO)' if kind == 'favicon' else ''}. SVG is not accepted.")
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
                  request=None):
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
    for kind, data, remove in (("logo", logo, remove_logo), ("favicon", favicon, remove_favicon)):
        if data:
            setattr(row, kind, data)
            setattr(row, f"{kind}_type", validate_image(kind, data))
            changed.append(kind)
        elif remove and getattr(row, f"{kind}_type"):
            setattr(row, kind, None)
            setattr(row, f"{kind}_type", "")
            changed.append(f"{kind} removed")
    if changed:
        row.version += 1
        row.save()
        cache.delete(CACHE_KEY)
        audit.record("brand.updated", actor=actor, target=row, metadata={"changed": changed}, request=request)
    return row
