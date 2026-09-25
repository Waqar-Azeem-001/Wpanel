from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import TimeStampedModel


class Stage(models.TextChoices):
    """Where a service is in its life. Derived from the paid-through date and the service's status, never stored."""

    ACTIVE = "active", "Active"
    RENEWAL_DUE = "renewal_due", "Renewal due"
    OVERDUE = "overdue", "Overdue"
    GRACE = "grace", "Grace period"
    SUSPENDED = "suspended", "Suspended"
    TERMINATED = "terminated", "Terminated"
    EXPIRED = "expired", "Expired"


class LifecycleSettings(models.Model):
    """
    Single row (pk=1): when an unpaid service moves along ``Active -> Renewal due -> Overdue -> Grace -> Suspended ->
    Terminated``. Every number counts days after the paid period ends.
    """

    grace_after_days = models.PositiveSmallIntegerField(
        default=3, validators=[MinValueValidator(1), MaxValueValidator(365)],
        help_text="The service is Overdue until this many days after it expires, then in its Grace period "
                  "(the customer gets a final notice).")
    suspend_after_days = models.PositiveSmallIntegerField(
        default=7, validators=[MinValueValidator(1), MaxValueValidator(365)],
        help_text="Days after expiry when an unpaid hosting account is suspended.")
    terminate_after_days = models.PositiveSmallIntegerField(
        default=30, validators=[MinValueValidator(1), MaxValueValidator(730)],
        help_text="Days after expiry when a hosting account suspended for non-payment is terminated.")
    auto_suspend = models.BooleanField(default=True, help_text="Suspend unpaid accounts automatically.")
    auto_terminate = models.BooleanField(
        default=False, help_text="Terminate accounts automatically. Termination deletes the customer's data, so it "
                                 "is off unless you turn it on; otherwise staff terminate by hand.")
    unsuspend_on_payment = models.BooleanField(
        default=True, help_text="Lift an automatic suspension as soon as the renewal is paid.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "lifecycle settings"

    def __str__(self):
        return "Lifecycle settings"

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]

    def clean(self):
        if None in (self.grace_after_days, self.suspend_after_days, self.terminate_after_days):
            return
        if not self.grace_after_days < self.suspend_after_days < self.terminate_after_days:
            raise ValidationError("The steps must be in order: grace period, then suspension, then termination "
                                  "(each later than the one before).")


class CancellationReason(models.TextChoices):
    TOO_EXPENSIVE = "too_expensive", "Too expensive"
    NOT_NEEDED = "not_needed", "I no longer need it"
    MOVING = "moving", "Moving to another provider"
    SERVICE = "service", "Unhappy with the service"
    TEMPORARY = "temporary", "It was only temporary"
    OTHER = "other", "Other"


class Timing(models.TextChoices):
    IMMEDIATE = "immediate", "Immediately"
    END_OF_TERM = "end_of_term", "At the end of the paid period"


class CancellationStatus(models.TextChoices):
    PENDING = "pending", "Awaiting review"
    APPROVED = "approved", "Approved"
    COMPLETED = "completed", "Completed"
    REJECTED = "rejected", "Rejected"
    WITHDRAWN = "withdrawn", "Withdrawn"


OPEN_STATUSES = (CancellationStatus.PENDING, CancellationStatus.APPROVED)


class CancellationRequest(TimeStampedModel):
    """A customer's (or staff's) request to end one service, and what was decided and done about it."""

    client = models.ForeignKey("clients.Client", on_delete=models.PROTECT, related_name="cancellation_requests")
    hosting_account = models.ForeignKey("hosting.HostingAccount", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="cancellation_requests")
    domain = models.ForeignKey("domains.Domain", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="cancellation_requests")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    on_behalf = models.BooleanField(default=False, help_text="Started by staff, not by the customer.")
    reason_code = models.CharField(max_length=16, choices=CancellationReason.choices)
    reason_text = models.CharField(max_length=1000, blank=True)
    timing = models.CharField(max_length=12, choices=Timing.choices)
    status = models.CharField(max_length=10, choices=CancellationStatus.choices, default=CancellationStatus.PENDING,
                              db_index=True)
    term_expires_at = models.DateTimeField(null=True, blank=True,
                                           help_text="The service's paid-through date when asked.")

    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=1000, blank=True, help_text="Shown to the customer.")
    effective_at = models.DateTimeField(null=True, blank=True, help_text="When the service ends (set on approval).")
    auto_renew_before = models.BooleanField(
        null=True, blank=True, help_text="A domain's auto-renew setting before approval, restored if withdrawn.")

    refund_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    refund_payment = models.ForeignKey("billing.Transaction", null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="+")
    refund_transaction = models.ForeignKey("billing.Transaction", null=True, blank=True, on_delete=models.PROTECT,
                                           related_name="+")
    refund_error = models.CharField(max_length=500, blank=True)

    claimed_at = models.DateTimeField(null=True, blank=True, help_text="Set while a worker is carrying it out.")
    last_error = models.CharField(max_length=500, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(hosting_account__isnull=False, domain__isnull=True)
                           | models.Q(hosting_account__isnull=True, domain__isnull=False)),
                name="cancellation_exactly_one_service"),
            models.UniqueConstraint(fields=["hosting_account"], condition=models.Q(status__in=OPEN_STATUSES),
                                    name="one_open_cancellation_per_hosting"),
            models.UniqueConstraint(fields=["domain"], condition=models.Q(status__in=OPEN_STATUSES),
                                    name="one_open_cancellation_per_domain"),
            models.CheckConstraint(condition=models.Q(refund_amount__gte=0), name="cancellation_refund_not_negative"),
        ]

    def __str__(self):
        return f"Cancellation {self.code} ({self.get_status_display()})"

    @property
    def code(self):
        return f"C{self.pk:06d}" if self.pk else ""

    @property
    def service(self):
        return self.hosting_account or self.domain

    @property
    def kind(self):
        return "hosting" if self.hosting_account_id else "domain"

    @property
    def service_name(self):
        return self.hosting_account.domain if self.hosting_account_id else self.domain.name

    @property
    def is_open(self):
        return self.status in OPEN_STATUSES

    @property
    def needs_attention(self):
        return self.status == CancellationStatus.APPROVED and bool(self.last_error)


class LifecycleNotice(TimeStampedModel):
    """A one-off warning already sent for one paid term of one account (so the daily job never repeats it)."""

    hosting_account = models.ForeignKey("hosting.HostingAccount", on_delete=models.CASCADE,
                                        related_name="lifecycle_notices")
    kind = models.CharField(max_length=20)
    term_expires_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["hosting_account", "kind", "term_expires_at"],
                                               name="one_lifecycle_notice_per_term")]
