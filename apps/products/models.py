from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from apps.core import crypto
from apps.core.models import TimeStampedModel

# Resource-limit keys with a known meaning; a non-negative integer, or null for
# "unlimited". Unknown keys are allowed through unvalidated (informational only).
KNOWN_RESOURCE_LIMITS = (
    "disk_mb", "bandwidth_mb", "email_accounts", "databases", "ftp_accounts",
    "subdomains", "parked_domains", "addon_domains", "websites",
)


class ProductType(models.TextChoices):
    """
    Domain registration is deliberately excluded: TLD pricing (register/renew/
    transfer) is its own model in Phase 04 (domains), not a catalog Product row.
    """

    SHARED_HOSTING = "shared_hosting", "Shared Hosting"
    WORDPRESS_HOSTING = "wordpress_hosting", "WordPress Hosting"
    RESELLER_HOSTING = "reseller_hosting", "Reseller Hosting"
    VPS = "vps", "VPS"
    DEDICATED_SERVER = "dedicated_server", "Dedicated Server"


class CatalogStatus(models.TextChoices):
    """Shared by Product and Addon: one status vocabulary for the whole catalog."""

    ACTIVE = "active", "Active"        # visible and orderable by everyone
    HIDDEN = "hidden", "Hidden"        # not shown for new orders; existing customers keep it
    RETIRED = "retired", "Retired"     # not orderable anywhere; kept for history/reporting


class BillingCycle(models.TextChoices):
    MONTHLY = "monthly", "Monthly"
    QUARTERLY = "quarterly", "Quarterly"
    SEMI_ANNUAL = "semi_annual", "Semi-Annually"
    ANNUAL = "annual", "Annually"
    BIENNIAL = "biennial", "Biennially"
    CUSTOM = "custom", "Custom"
    # Addon-only: a single non-recurring charge (e.g. a one-off migration fee).
    ONE_TIME = "one_time", "One-time"


def encode_option(billing_cycle, custom_months=0):
    """A price row as a single form/API value: 'annual', 'one_time' or 'custom:4'."""
    return f"custom:{custom_months}" if billing_cycle == BillingCycle.CUSTOM else billing_cycle


def decode_option(value):
    """Inverse of ``encode_option``: (billing_cycle, custom_months). Raises ValueError if malformed."""
    value = (value or "").strip()
    if value.startswith("custom:"):
        months = int(value.split(":", 1)[1])
        if months < 1:
            raise ValueError("Invalid duration.")
        return BillingCycle.CUSTOM, months
    if value not in BillingCycle.values or value == BillingCycle.CUSTOM:
        raise ValueError("Invalid billing cycle.")
    return value, 0


STANDARD_CYCLE_MONTHS = {
    BillingCycle.MONTHLY: 1,
    BillingCycle.QUARTERLY: 3,
    BillingCycle.SEMI_ANNUAL: 6,
    BillingCycle.ANNUAL: 12,
    BillingCycle.BIENNIAL: 24,
}


class ServerStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    MAINTENANCE = "maintenance", "Maintenance"
    OFFLINE = "offline", "Offline"


class ServerKind(models.TextChoices):
    """Selects the provisioning adapter (apps.hosting.adapters) used for this server."""

    MANUAL = "manual", "Manual (no real provisioning - development/testing only)"
    WHM_API = "whm_api", "WHM API"


class Server(TimeStampedModel):
    """
    A hosting server a product can be provisioned on.

    Started minimal in Phase 03 (just enough for Product's "server mapping").
    Phase 05 (Hosting/WHM Provisioning) extends this *same model* - rather than
    creating a second one - with the connection details a real adapter needs.
    Credentials are encrypted at rest with ``apps.core.crypto``, the same
    pattern used by ``notifications.EmailProvider`` and
    ``domains.RegistrarProvider``.
    """

    name = models.CharField(max_length=100, unique=True)
    hostname = models.CharField(max_length=255)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=ServerStatus.choices, default=ServerStatus.ACTIVE,
                              db_index=True)
    max_accounts = models.PositiveIntegerField(null=True, blank=True, help_text="Blank means no fixed limit.")
    notes = models.TextField(blank=True)

    # --- Phase 05: provisioning connection -------------------------------------------
    kind = models.CharField(max_length=20, choices=ServerKind.choices, default=ServerKind.MANUAL)
    api_port = models.PositiveIntegerField(default=2087, help_text="WHM API port. 2087 is the standard port.")
    api_username = models.CharField(max_length=100, blank=True, help_text='WHM API username, e.g. "root".')
    api_token_encrypted = models.TextField(blank=True, editable=False)
    use_ssl = models.BooleanField(default=True, help_text="Use HTTPS for the WHM API connection.")
    verify_ssl = models.BooleanField(default=True, help_text="Disable only for a self-signed certificate you trust.")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def set_api_token(self, raw):
        self.api_token_encrypted = crypto.encrypt(raw) if raw else ""

    def get_api_token(self):
        return crypto.decrypt(self.api_token_encrypted)


class CatalogItem(TimeStampedModel):
    """Fields and behaviour shared by Product and Addon."""

    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=60, unique=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=CatalogStatus.choices, default=CatalogStatus.HIDDEN,
                              db_index=True)

    class Meta:
        abstract = True
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(CatalogItem):
    type = models.CharField(max_length=32, choices=ProductType.choices, db_index=True)
    resource_limits = models.JSONField(
        default=dict, blank=True,
        help_text="e.g. {\"disk_mb\": 5000, \"bandwidth_mb\": null} (null = unlimited).",
    )
    servers = models.ManyToManyField(Server, blank=True, related_name="products")
    whm_package_name = models.CharField(max_length=100, blank=True, help_text="WHM package this product creates.")
    auto_setup = models.BooleanField(
        default=True, help_text="Provision automatically on payment once Phase 05 is live. Off = staff provision manually.",
    )
    default_auto_renew = models.BooleanField(default=True, help_text="Default for new orders of this product.")
    # Selling more: the plan to suggest to someone who has this one in their cart (an upsell), and the add-ons that suit it
    # (a cross-sell). Both are chosen by staff; nothing is guessed.
    upsell_product = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name="upsold_from", help_text="A bigger plan to suggest in the cart.")
    recommended_addons = models.ManyToManyField("products.Addon", blank=True, related_name="recommended_for",
                                                help_text="Add-ons to highlight with this plan.")

    def clean(self):
        if self.upsell_product_id and self.pk and self.upsell_product_id == self.pk:
            raise ValidationError({"upsell_product": "A plan cannot be its own upgrade."})
        if not isinstance(self.resource_limits, dict):
            raise ValidationError({"resource_limits": "Must be a JSON object."})
        for key in KNOWN_RESOURCE_LIMITS:
            if key not in self.resource_limits or self.resource_limits[key] is None:
                continue
            value = self.resource_limits[key]
            if not (isinstance(value, int) and not isinstance(value, bool) and value >= 0):
                raise ValidationError({"resource_limits": f"'{key}' must be a non-negative integer or null."})


class Addon(CatalogItem):
    pass


class PriceEntry(TimeStampedModel):
    """
    One price for one billing cycle. Shared shape for ``ProductPrice`` and
    ``AddonPrice`` so pricing logic (``apps.products.services.get_effective_price``)
    works identically for both - the authoritative, server-side source of truth
    for what something costs. Never trust a client-submitted price or setup fee.
    """

    billing_cycle = models.CharField(max_length=16, choices=BillingCycle.choices)
    # 0 for standard/one-time cycles; the month count when billing_cycle=CUSTOM.
    custom_months = models.PositiveSmallIntegerField(default=0)
    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    setup_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True
        ordering = ["billing_cycle", "custom_months"]

    def clean(self):
        if self.billing_cycle == BillingCycle.CUSTOM and not self.custom_months:
            raise ValidationError({"custom_months": "Required (and > 0) when billing cycle is Custom."})
        if self.billing_cycle != BillingCycle.CUSTOM and self.custom_months:
            raise ValidationError({"custom_months": "Only used when billing cycle is Custom."})

    @property
    def option_value(self):
        return encode_option(self.billing_cycle, self.custom_months)

    @property
    def months(self):
        """Term length in months, or None for a one-time charge with no term."""
        if self.billing_cycle == BillingCycle.CUSTOM:
            return self.custom_months
        return STANDARD_CYCLE_MONTHS.get(self.billing_cycle)

    def __str__(self):
        label = f"{self.custom_months}mo" if self.billing_cycle == BillingCycle.CUSTOM else self.get_billing_cycle_display()
        return f"{label}: {self.price}"


class ProductPrice(PriceEntry):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="prices")

    class Meta(PriceEntry.Meta):
        constraints = [
            models.UniqueConstraint(fields=["product", "billing_cycle", "custom_months"], name="unique_product_price")
        ]

    def clean(self):
        super().clean()
        if self.billing_cycle == BillingCycle.ONE_TIME:
            raise ValidationError({"billing_cycle": "One-time pricing is for addons, not products."})


class AddonPrice(PriceEntry):
    addon = models.ForeignKey(Addon, on_delete=models.CASCADE, related_name="prices")

    class Meta(PriceEntry.Meta):
        constraints = [
            models.UniqueConstraint(fields=["addon", "billing_cycle", "custom_months"], name="unique_addon_price")
        ]
