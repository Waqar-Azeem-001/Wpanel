"""
External storefronts (roadmap: WordPress/external storefront integration) - a website outside this project, such as
a WordPress site running the future storefront plugin, that is allowed to read a chosen slice of the catalog
through the API in ``apps.storefronts.api``. This app owns only the connection and what is published to it; the
catalog itself (``Product``, ``Addon``, ``TldPricing``...) stays entirely in the apps that already own it (Rule 3 -
one implementation).
"""
from django.db import models

from apps.core import crypto
from apps.core.models import TimeStampedModel


class StorefrontType(models.TextChoices):
    WORDPRESS = "wordpress", "WordPress"


class StorefrontStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    DISABLED = "disabled", "Disabled"


class ExternalStorefront(TimeStampedModel):
    """
    One connected external website. ``public_id`` is what it presents alongside its secret (not itself a secret -
    safe to show in the staff UI and in logs); ``secret_hash`` is one-way (Django's own password hasher, the same
    protection as an account password or an MFA recovery code) - the raw secret is shown once, at creation or
    rotation, and is never recoverable after that, by design (a lost secret means rotating it, not looking it up).
    """

    name = models.CharField(max_length=150, help_text='e.g. "Web Host Era UK".')
    site_url = models.URLField(help_text="The storefront's own address.")
    integration_type = models.CharField(max_length=20, choices=StorefrontType.choices, default=StorefrontType.WORDPRESS)
    status = models.CharField(max_length=16, choices=StorefrontStatus.choices, default=StorefrontStatus.ACTIVE,
                              db_index=True)
    public_id = models.CharField(max_length=48, unique=True, editable=False,
                                 help_text="Sent alongside the secret; not itself sensitive.")
    secret_hash = models.CharField(max_length=128, editable=False)
    webhook_url = models.URLField(blank=True, help_text="Where to ping this storefront when a published plan changes.")
    webhook_secret_encrypted = models.TextField(blank=True, editable=False)
    notes = models.TextField(blank=True)
    last_accessed_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def is_active(self):
        return self.status == StorefrontStatus.ACTIVE

    def set_webhook_secret(self, raw):
        """Reversibly encrypted (unlike ``secret_hash`` above) because, unlike the inbound API credential, this
        one is never compared - it must be read back in full every time *we* sign an outgoing delivery. Same
        mechanism as ``PaymentProvider.set_webhook_secret`` (``apps.billing.models``)."""
        self.webhook_secret_encrypted = crypto.encrypt(raw)

    def get_webhook_secret(self):
        return crypto.decrypt(self.webhook_secret_encrypted)


class WebhookDeliveryStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    DELIVERED = "delivered", "Delivered"
    FAILED = "failed", "Failed"


class StorefrontWebhookDelivery(TimeStampedModel):
    """One "something changed, please refetch" ping - a record of what was attempted, not a payload WordPress is
    meant to trust (Rule 3: the plugin always refetches from the read API; this never carries catalog data)."""

    storefront = models.ForeignKey(ExternalStorefront, on_delete=models.CASCADE, related_name="webhook_deliveries")
    event = models.CharField(max_length=50)
    status = models.CharField(max_length=16, choices=WebhookDeliveryStatus.choices, default=WebhookDeliveryStatus.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    error = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.event} -> {self.storefront} ({self.status})"


class StorefrontProduct(models.Model):
    """
    One plan published to one storefront (Rule 3: the plan is never duplicated - this only says "show it here").
    The optional fields let a storefront present a plan slightly differently (a shorter public title, a featured
    flag, its own sort order) without touching the authoritative ``Product`` row those come from.
    """

    storefront = models.ForeignKey(ExternalStorefront, on_delete=models.CASCADE, related_name="published_products")
    product = models.ForeignKey("products.Product", on_delete=models.CASCADE, related_name="storefront_links")
    title_override = models.CharField(max_length=150, blank=True, help_text="Blank = use the plan's own name.")
    description_override = models.CharField(max_length=300, blank=True, help_text="Blank = use the plan's own description.")
    featured = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ["sort_order", "product__name"]
        constraints = [
            models.UniqueConstraint(fields=["storefront", "product"], name="unique_storefront_product")
        ]

    def __str__(self):
        return f"{self.product} on {self.storefront}"

    @property
    def display_title(self):
        return self.title_override or self.product.name

    @property
    def display_description(self):
        return self.description_override or self.product.description
