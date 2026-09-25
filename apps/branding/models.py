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
    support_email = models.EmailField(blank=True)
    footer_text = models.CharField(max_length=300, blank=True)
    date_format = models.CharField(max_length=12, choices=DATE_FORMATS, default="%d %b %Y")
    money_format = models.CharField(max_length=12, choices=MONEY_FORMATS, default="code_before")

    logo = models.BinaryField(null=True, blank=True, editable=False)
    logo_type = models.CharField(max_length=30, blank=True, editable=False)
    favicon = models.BinaryField(null=True, blank=True, editable=False)
    favicon_type = models.CharField(max_length=30, blank=True, editable=False)

    version = models.PositiveIntegerField(default=1, editable=False, help_text="Bumped on every change (cache busting).")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "brand settings"

    def __str__(self):
        return "Brand settings"

    @classmethod
    def load(cls):
        """The row, without the (possibly large) image columns."""
        row = cls.objects.defer("logo", "favicon").filter(pk=1).first()
        return row or cls.objects.create(pk=1)
