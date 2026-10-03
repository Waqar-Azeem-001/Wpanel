import re

from django.core.exceptions import ValidationError
from django.db import models

HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

DATE_FORMATS = (
    ("%d %b %Y", "25 Sep 2026"),
    ("%Y-%m-%d", "2026-09-25"),
    ("%d/%m/%Y", "25/09/2026"),
    ("%m/%d/%Y", "09/25/2026"),
)
MONEY_FORMATS = (
    ("code_before", "USD 1,234.50"),
    ("code_after", "1,234.50 USD"),
    ("plain", "1,234.50"),
)


def validate_hex(value):
    if not HEX_COLOR.match(value or ""):
        raise ValidationError("Use a colour like #2459d6 (a # and six letters or digits).")


class MenuSetting(models.Model):
    """
    A Super Admin's choice for one entry of the customer menu (see ``navigation``): hide it, call it something else, or move it. No row
    means the entry is shown as the code defines it. Entries are named by their registry key (``client.services``,
    ``client.services.cat.vps`` for a store category shown under Services).
    """

    area = models.CharField(max_length=10, default="client")
    key = models.CharField(max_length=80)
    label = models.CharField(max_length=60, blank=True, help_text="Blank = the standard name.")
    is_visible = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Lower numbers come first. Blank = the standard order.")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["area", "key"], name="unique_menu_setting")]

    def __str__(self):
        return f"{self.area}:{self.key}"


LOOKS = (("signature", "Signature"), ("heritage", "Heritage"), ("aura", "Aura"))
THEMES = LOOKS + (("custom", "Custom"),)


class BrandSettings(models.Model):
    """
    Single row (pk=1): how the portal presents itself. Brand is configuration, never hard-coded (roadmap Rule 6): name,
    logo, favicon, colours, support email, footer, and how dates and money are written.
    """

    site_name = models.CharField(max_length=100, blank=True, help_text="Blank = the SITE_NAME setting.")
    primary_color = models.CharField(max_length=7, default="#2459d6", validators=[validate_hex],
                                     help_text="Buttons, links and highlights. Must be dark enough for white text.")
    accent_color = models.CharField(max_length=7, default="#1f7a4d", validators=[validate_hex],
                                    help_text="Highlights and call-to-action buttons; white or dark text is chosen to suit it.")
    # The look: a ready-made brand (its own colours, type, corners and header), or "custom": your own colours on one of the looks.
    theme = models.CharField(max_length=12, choices=THEMES, default="custom")
    custom_look = models.CharField(max_length=12, choices=LOOKS, default="signature",
                                   help_text="With your own colours: the type, corners and header of this look.")
    support_email = models.EmailField(blank=True)
    footer_text = models.CharField(max_length=300, blank=True)
    date_format = models.CharField(max_length=12, choices=DATE_FORMATS, default="%d %b %Y")
    money_format = models.CharField(max_length=12, choices=MONEY_FORMATS, default="code_before")

    logo = models.BinaryField(null=True, blank=True, editable=False)
    logo_type = models.CharField(max_length=30, blank=True, editable=False)
    favicon = models.BinaryField(null=True, blank=True, editable=False)
    favicon_type = models.CharField(max_length=30, blank=True, editable=False)
    # The same brand for dark backgrounds: a light logo and a light icon, used in the dark theme and on the dark footer.
    logo_dark = models.BinaryField(null=True, blank=True, editable=False)
    logo_dark_type = models.CharField(max_length=30, blank=True, editable=False)
    mark_dark = models.BinaryField(null=True, blank=True, editable=False)
    mark_dark_type = models.CharField(max_length=30, blank=True, editable=False)
    # The policy pages the store links to (terms, refunds...): [{"title": "...", "url": "..."}], in the order shown.
    policy_links = models.JSONField(default=list, blank=True, editable=False)
    maintenance_mode = models.BooleanField(
        default=False, help_text="Show visitors and customers the \"be right back\" page while you work. "
                                 "Staff can still sign in and use the portal as usual; the API is not affected.")

    version = models.PositiveIntegerField(default=1, editable=False, help_text="Bumped on every change (cache busting).")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "brand settings"

    def __str__(self):
        return "Brand settings"

    @classmethod
    def load(cls):
        """The row, without the (possibly large) image columns."""
        row = cls.objects.defer("logo", "favicon", "logo_dark", "mark_dark").filter(pk=1).first()
        return row or cls.objects.create(pk=1)
