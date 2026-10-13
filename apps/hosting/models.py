import re
from urllib.parse import urlunsplit

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
    limit_alerts = models.JSONField(default=dict, blank=True, help_text="Which usage warnings have already been sent this month, so each is sent once (native hosting).")
    mail_server = models.ForeignKey("products.Server", null=True, blank=True, on_delete=models.SET_NULL, related_name="mail_accounts",
                                    help_text="Native hosting only: the separate mail node that holds this account's mailboxes (chosen when mail is first used).")
    suspend_reason = models.CharField(max_length=500, blank=True)
    suspended_for_nonpayment = models.BooleanField(
        default=False, help_text="Suspended automatically because the renewal was not paid; lifted when it is.")
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

    backup_requested_at = models.DateTimeField(null=True, blank=True,
                                               help_text="Last time a backup was requested from the server.")
    backup_ref = models.CharField(max_length=100, blank=True, help_text="The adapter's own reference for that backup.")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["username"], condition=models.Q(status__in=LIVE_STATUSES),
                                    name="unique_live_hosting_username")
        ]

    @property
    def site_url(self):
        """The public address of the website itself (for a "Visit site" link)."""
        return urlunsplit(("https", self.domain, "", "", ""))

    def __str__(self):
        return f"{self.username} ({self.domain})"

    @property
    def term_expired(self):
        return self.expires_at is not None and self.expires_at <= timezone.now()

    def save(self, *args, **kwargs):
        self.domain = self.domain.lower()
        self.username = self.username.lower()
        super().save(*args, **kwargs)


class NativeBackup(models.Model):
    """
    One backup of a native hosting account (N8). WHPanel keeps this register itself, so what exists is known even if a node is lost. A customer's backup is a *group*: the web node's
    part, and the mail node's part when mail is used.
    """

    account = models.ForeignKey(HostingAccount, on_delete=models.CASCADE, related_name="native_backups")
    node = models.ForeignKey("agent.AgentNode", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    group = models.CharField(max_length=12, db_index=True)
    part = models.CharField(max_length=4, choices=[("web", "Web node"), ("mail", "Mail node")])
    kind = models.CharField(max_length=10, help_text="What this part holds: files, database, email or full.")
    requested_kind = models.CharField(max_length=10, help_text="What the customer asked for.")
    source = models.CharField(max_length=20, default="manual", help_text="manual, scheduled or before restore")
    snapshot_id = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=10, default="running", db_index=True, help_text="running, complete, failed, deleting or deleted")
    size_mb = models.FloatField(null=True, blank=True)
    error = models.CharField(max_length=300, blank=True)
    verified = models.CharField(max_length=6, blank=True)  # "", "ok" or "failed"
    verified_at = models.DateTimeField(null=True, blank=True)
    restore_tested_at = models.DateTimeField(null=True, blank=True)
    restore_test_ok = models.BooleanField(null=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class NativeBackupSettings(models.Model):
    account = models.OneToOneField(HostingAccount, on_delete=models.CASCADE, related_name="native_backup_settings")
    schedule = models.CharField(max_length=8, default="off")  # off, daily or weekly
    last_scheduled_at = models.DateTimeField(null=True, blank=True)


class AppState(models.TextChoices):
    INSTALLING = "installing", "Installing"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"


class WebsiteApp(TimeStampedModel):
    """
    What the website was set up as (the "Create website" step): WordPress, or a custom website the customer fills with their own files.
    One per hosting account for now. The administrator's password is never stored here; only what is needed to show the screen and the link to sign in.
    """

    KIND_CHOICES = [("wordpress", "WordPress"), ("custom", "Custom website")]

    account = models.OneToOneField(HostingAccount, on_delete=models.CASCADE, related_name="site_app")
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    state = models.CharField(max_length=12, choices=AppState.choices, default=AppState.READY)
    title = models.CharField(max_length=100, blank=True)
    admin_user = models.CharField(max_length=30, blank=True)
    admin_email = models.EmailField(blank=True)
    admin_url = models.CharField(max_length=300, blank=True)
    version = models.CharField(max_length=30, blank=True)
    error = models.CharField(max_length=500, blank=True)
    # for an installation WHPanel runs itself (servers that cannot run our agent): the result file to wait for and what was made, so a failure can be undone
    job_ref = models.CharField(max_length=80, blank=True)
    installer_ref = models.CharField(max_length=80, blank=True)
    db_name = models.CharField(max_length=80, blank=True)
    db_user = models.CharField(max_length=80, blank=True)

    def __str__(self):
        return f"{self.get_kind_display()} on {self.account.domain}"
