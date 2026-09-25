import re

from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel

# Conservative cPanel username rules: lowercase, starts with a letter, alphanumeric only,
# capped at 16 characters. Modern cPanel allows longer usernames, but 16 stays compatible
# with older servers and OS-level Unix username limits some setups still enforce.
USERNAME_VALIDATOR = RegexValidator(
    re.compile(r"^[a-z][a-z0-9]{0,15}$"),
    "Must start with a letter and contain only lowercase letters and numbers (max 16 characters).",
)


class HostingStatus(models.TextChoices):
    PENDING = "pending", "Pending Provisioning"
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    TERMINATED = "terminated", "Terminated"
    CANCELLED = "cancelled", "Cancelled"
    FAILED = "failed", "Failed"


# Statuses that hold a live claim on the username - a second request for the same
# username is blocked while one of these is in effect (mirrors apps.domains.LIVE_STATUSES).
LIVE_STATUSES = (HostingStatus.PENDING, HostingStatus.ACTIVE, HostingStatus.SUSPENDED)


class HostingAccount(TimeStampedModel):
    """
    A provisioned hosting account (a "hosting service" in the roadmap's core
    business model) - the cPanel/WHM-side account backing a client's hosting
    product. Belongs to Client, not directly to a User, same as Domain.
    """

    client = models.ForeignKey("clients.Client", on_delete=models.PROTECT, related_name="hosting_accounts")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="hosting_accounts")
    server = models.ForeignKey("products.Server", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="hosting_accounts")
    domain = models.CharField(max_length=253, help_text="Primary domain for this hosting account.")
    username = models.CharField(max_length=16, unique=False, validators=[USERNAME_VALIDATOR],
                                help_text="cPanel account username.")
    status = models.CharField(max_length=16, choices=HostingStatus.choices, default=HostingStatus.PENDING,
                              db_index=True)
    # Snapshot of the product's WHM package at creation/last change, so a later
    # rename or reconfiguration of the product doesn't retroactively rewrite history.
    package_name = models.CharField(max_length=100, blank=True)
    suspend_reason = models.CharField(max_length=500, blank=True)
    last_error = models.TextField(blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    # The paid term (Phase 08). Blank for accounts whose term has not been recorded yet: those cannot be
    # renewed or upgraded online until staff set it. ``term_paid`` is the amount actually paid for the
    # current term, excluding tax and setup fees - the most an upgrade credit can ever be worth.
    billing_cycle = models.CharField(max_length=16, blank=True)
    custom_months = models.PositiveSmallIntegerField(default=0)
    term_start = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True, help_text="Paid through this date.")
    term_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])

    disk_used_mb = models.PositiveIntegerField(null=True, blank=True)
    disk_limit_mb = models.PositiveIntegerField(null=True, blank=True, help_text="Blank means unlimited/unknown.")
    bandwidth_used_mb = models.PositiveIntegerField(null=True, blank=True)
    bandwidth_limit_mb = models.PositiveIntegerField(null=True, blank=True,
                                                     help_text="Blank means unlimited/unknown.")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["username"], condition=models.Q(status__in=LIVE_STATUSES),
                                    name="unique_live_hosting_username")
        ]

    def __str__(self):
        return f"{self.username} ({self.domain})"

    @property
    def term_expired(self):
        return self.expires_at is not None and self.expires_at <= timezone.now()

    def save(self, *args, **kwargs):
        self.domain = self.domain.lower()
        self.username = self.username.lower()
        super().save(*args, **kwargs)
