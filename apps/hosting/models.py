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

    account = models.ForeignKey(HostingAccount, on_delete=models.CASCADE, related_name="site_apps")
    # "" is the plan's own (primary) domain; another value is a domain added to the plan (an addon domain with its own folder)
    domain = models.CharField(max_length=253, blank=True, default="")
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
    progress_step = models.CharField(max_length=20, blank=True)  # where the installer is: start, download, verify, unpack, config, install, finish
    progress_percent = models.PositiveSmallIntegerField(default=0)
    db_name = models.CharField(max_length=80, blank=True)
    db_user = models.CharField(max_length=80, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["account", "domain"], name="one_website_per_domain")]

    @property
    def site_domain(self):
        return self.domain or self.account.domain

    def __str__(self):
        return f"{self.get_kind_display()} on {self.site_domain}"


class SiteCare(TimeStampedModel):
    """
    What the customer chose for looking after one website (the Care page): how WordPress keeps itself up to date, how often the site is scanned for malware, and,
    on servers that cannot schedule backups themselves, how often WHPanel takes one. One row per hosting account; no row means nothing is switched on.
    """

    CORE_CHOICES = [("off", "Do not update"), ("minor", "Security and bug-fix releases"), ("all", "Every release")]
    SCAN_CHOICES = [("off", "Only when I ask"), ("weekly", "Every week"), ("daily", "Every day")]
    BACKUP_CHOICES = [("off", "Off"), ("weekly", "Every week"), ("daily", "Every day")]

    account = models.ForeignKey(HostingAccount, on_delete=models.CASCADE, related_name="care_rows")
    domain = models.CharField(max_length=253, blank=True, default="")  # "" is the plan's own website; another value is a website on a domain added to the plan
    wp_core = models.CharField(max_length=8, choices=CORE_CHOICES, default="minor")
    wp_plugins = models.BooleanField(default=False)
    wp_themes = models.BooleanField(default=False)
    updates_written_at = models.DateTimeField(null=True, blank=True)  # when the rule file was last put on the website
    scan_schedule = models.CharField(max_length=8, choices=SCAN_CHOICES, default="off")
    backup_schedule = models.CharField(max_length=8, choices=BACKUP_CHOICES, default="off")  # used only where the server cannot schedule backups itself
    last_backup_at = models.DateTimeField(null=True, blank=True)
    backup_note = models.CharField(max_length=300, blank=True)
    maintenance = models.BooleanField(default=False)  # visitors see a "be right back" page; signed-in WordPress users still see the site
    discourage_search = models.BooleanField(default=False)  # WordPress asks search engines not to index the site
    performance = models.BooleanField(default=False)  # compression and browser caching rules in .htaccess
    quarantine = models.JSONField(default=list, blank=True)  # [{"path", "stored", "rule", "at"}] files moved aside by the customer

    class Meta:
        constraints = [models.UniqueConstraint(fields=["account", "domain"], name="one_care_per_website")]

    def __str__(self):
        return f"Care of {self.domain or self.account.domain}"


class StagingSite(TimeStampedModel):
    """
    A private copy of a WordPress website to try changes on before they go live: its own subdomain, folder and database. One per website ("" is the plan's own).
    Only paths and names are kept, never passwords (the staging database password lives in the copied wp-config.php on the server).
    """

    STATES = [("creating", "Creating"), ("ready", "Ready"), ("pushing", "Publishing"), ("failed", "Failed")]

    account = models.ForeignKey(HostingAccount, on_delete=models.CASCADE, related_name="staging_sites")
    site_domain = models.CharField(max_length=253, blank=True, default="")  # which website of the plan is copied; "" = the plan's own
    state = models.CharField(max_length=10, choices=STATES, default="creating")
    domain = models.CharField(max_length=253)
    docroot = models.CharField(max_length=300, blank=True)
    db_name = models.CharField(max_length=80, blank=True)
    db_user = models.CharField(max_length=80, blank=True)
    script_ref = models.CharField(max_length=80, blank=True)
    result_ref = models.CharField(max_length=80, blank=True)
    error = models.CharField(max_length=500, blank=True)
    pushed_at = models.DateTimeField(null=True, blank=True)
    made_domain = models.BooleanField(default=False)  # WHPanel added the subdomain, so it removes it again

    class Meta:
        constraints = [models.UniqueConstraint(fields=["account", "site_domain"], name="one_staging_per_website")]

    def __str__(self):
        return f"Staging of {self.account.domain} ({self.state})"


class SiteScan(TimeStampedModel):
    """One malware scan of a website: running, then done (with what was found) or failed. Only paths and rule names are kept, never file contents."""

    STATES = [("running", "Running"), ("done", "Done"), ("failed", "Failed")]

    account = models.ForeignKey(HostingAccount, on_delete=models.CASCADE, related_name="scans")
    domain = models.CharField(max_length=253, blank=True, default="")  # "" is the plan's own website
    state = models.CharField(max_length=8, choices=STATES, default="running")
    trigger = models.CharField(max_length=10, default="manual")  # "manual" or "scheduled"
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    files_scanned = models.PositiveIntegerField(default=0)
    findings = models.JSONField(default=list, blank=True)  # [{"path", "rule", "severity", "detail"}], most severe first
    wp_version = models.CharField(max_length=30, blank=True)
    wp_checked = models.PositiveIntegerField(default=0)  # WordPress system files compared with the official release
    error = models.CharField(max_length=300, blank=True)
    result_ref = models.CharField(max_length=80, blank=True)  # the result file the scanner leaves, until it is read and removed
    script_ref = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["-started_at", "-id"]

    def __str__(self):
        return f"Scan of {self.account.domain} ({self.state})"


class ResourceReading(models.Model):
    """What a website's account was using at one moment (CPU, memory, processes, input/output), as the server reported it. Kept for a week so the last 24 hours can be drawn."""

    account = models.ForeignKey(HostingAccount, on_delete=models.CASCADE, related_name="resource_readings")
    taken_at = models.DateTimeField(db_index=True)
    values = models.JSONField(default=dict)  # {"cpu": {"used": 3.0, "limit": 100.0, "unit": "%"}, ...}

    class Meta:
        ordering = ["-taken_at"]
        indexes = [models.Index(fields=["account", "-taken_at"])]

    def __str__(self):
        return f"{self.account_id} at {self.taken_at:%Y-%m-%d %H:%M}"


class SpeedTest(TimeStampedModel):
    """One speed test of a website's home page, measured from the panel. Figures only."""

    account = models.ForeignKey(HostingAccount, on_delete=models.CASCADE, related_name="speed_tests")
    domain = models.CharField(max_length=253, blank=True, default="")  # "" is the plan's own website
    url = models.CharField(max_length=300)
    ok = models.BooleanField(default=False)
    status_code = models.PositiveSmallIntegerField(default=0)
    first_byte_ms = models.PositiveIntegerField(default=0)
    total_ms = models.PositiveIntegerField(default=0)
    size_bytes = models.PositiveIntegerField(default=0)
    redirects = models.PositiveSmallIntegerField(default=0)
    https = models.BooleanField(default=False)
    compressed = models.BooleanField(default=False)
    cached = models.BooleanField(default=False)  # the page says browsers may keep it for a while
    score = models.PositiveSmallIntegerField(default=0)
    advice = models.JSONField(default=list, blank=True)  # short plain-language notes
    error = models.CharField(max_length=300, blank=True)
    kind = models.CharField(max_length=10, default="server")  # "server" (measured from the panel) or "pagespeed" (Google's lab report)
    report = models.JSONField(default=dict, blank=True)  # the figures of a PageSpeed report

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Speed of {self.url}: {self.score}"
