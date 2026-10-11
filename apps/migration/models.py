from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class MigrationState(models.TextChoices):
    AWAITING_ARCHIVE = "awaiting_archive", "Waiting for the cPanel backup to arrive"
    ANALYSING = "analysing", "Reading the backup"
    MANUAL_REVIEW = "manual_review", "Needs a person"
    READY = "ready", "Ready to import"
    IMPORTING = "importing", "Importing"
    VALIDATED = "validated", "Imported and checked: ready for cutover"
    WINDOW = "window", "Switched over: rollback window open"
    COMPLETE = "complete", "Complete"
    FAILED = "failed", "Failed"
    ROLLED_BACK = "rolled_back", "Rolled back"
    CANCELLED = "cancelled", "Cancelled"


ACTIVE = (MigrationState.AWAITING_ARCHIVE, MigrationState.ANALYSING, MigrationState.MANUAL_REVIEW, MigrationState.READY, MigrationState.IMPORTING,
          MigrationState.VALIDATED, MigrationState.WINDOW, MigrationState.FAILED)


class Migration(TimeStampedModel):
    """
    One cPanel account moving to a native node (M2, ADR 19). The customer's HostingAccount keeps its identity; only the server it points to changes at cutover, and changes back on rollback.
    The cPanel account is never touched by this: it stays as it is until staff retire it after the rollback window.
    """

    account = models.ForeignKey("hosting.HostingAccount", on_delete=models.PROTECT, related_name="migrations")
    source = models.ForeignKey("products.Server", on_delete=models.PROTECT, related_name="migrations_out")
    target = models.ForeignKey("products.Server", on_delete=models.PROTECT, related_name="migrations_in")
    mail_target = models.ForeignKey("products.Server", null=True, blank=True, on_delete=models.PROTECT, related_name="migrations_mail")
    state = models.CharField(max_length=20, choices=MigrationState.choices, default=MigrationState.AWAITING_ARCHIVE, db_index=True)
    step = models.CharField(max_length=20, blank=True, help_text="Which import step is running or failed: site, files, databases, mail, validate.")
    archive_name = models.CharField(max_length=120)
    expected_sha256 = models.CharField(max_length=64, blank=True)
    report = models.JSONField(default=dict, blank=True)
    validation = models.JSONField(default=dict, blank=True)
    parts_done = models.JSONField(default=list, blank=True)
    attempt = models.PositiveSmallIntegerField(default=1)
    previous_dns = models.JSONField(default=dict, blank=True, help_text="The website's address record before cutover, so rollback can put it back.")
    dns_note = models.CharField(max_length=300, blank=True)
    window_days = models.PositiveSmallIntegerField(default=7)
    cutover_at = models.DateTimeField(null=True, blank=True)
    rollback_until = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["account"], condition=models.Q(state__in=[s.value for s in ACTIVE]), name="one_active_migration_per_account")]

    def __str__(self):
        return f"{self.account.username}: {self.get_state_display()}"


class MigrationEvent(models.Model):
    migration = models.ForeignKey(Migration, on_delete=models.CASCADE, related_name="events")
    at = models.DateTimeField(auto_now_add=True)
    stage = models.CharField(max_length=20)
    ok = models.BooleanField(default=True)
    message = models.CharField(max_length=500)

    class Meta:
        ordering = ["at", "id"]


class RetirementSignoff(TimeStampedModel):
    """A person's statement that one condition for retiring cPanel (M4) is met, with what they saw. Only for conditions WHPanel cannot measure itself."""

    key = models.CharField(max_length=40, unique=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=500)
