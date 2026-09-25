from django.db import models

from apps.core.models import TimeStampedModel


class ChangeKind(models.TextChoices):
    RENEWAL = "renewal", "Renewal"
    UPGRADE = "upgrade", "Upgrade"


class ChangeStatus(models.TextChoices):
    PENDING = "pending", "Awaiting payment"
    APPLIED = "applied", "Applied"
    FAILED = "failed", "Paid - needs attention"
    VOID = "void", "Void"


class ServiceChange(TimeStampedModel):
    """
    A renewal or upgrade of one existing service, tied to the invoice that pays for it.

    The row is created together with the invoice and holds everything needed to apply the change
    when that invoice is paid - the figures are *frozen here at invoicing time*, never read from a
    browser and never recomputed at payment time. ``calculation`` keeps the inputs and results of the
    proration so the credit on an invoice can be re-derived later (``verify_billing``).
    """

    client = models.ForeignKey("clients.Client", on_delete=models.PROTECT, related_name="service_changes")
    invoice = models.ForeignKey("billing.Invoice", on_delete=models.PROTECT, related_name="service_changes")
    kind = models.CharField(max_length=10, choices=ChangeKind.choices)
    status = models.CharField(max_length=10, choices=ChangeStatus.choices, default=ChangeStatus.PENDING,
                              db_index=True)

    hosting_account = models.ForeignKey("hosting.HostingAccount", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="service_changes")
    domain = models.ForeignKey("domains.Domain", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="service_changes")

    from_product = models.ForeignKey("products.Product", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+")
    to_product = models.ForeignKey("products.Product", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+")
    billing_cycle = models.CharField(max_length=16, blank=True)
    custom_months = models.PositiveSmallIntegerField(default=0)
    period_months = models.PositiveSmallIntegerField(help_text="Length of the term this change buys.")
    paid_value = models.DecimalField(max_digits=12, decimal_places=2,
                                     help_text="What this change adds to the term's valid paid value (ex tax).")
    expected_expires_at = models.DateTimeField(null=True, blank=True,
                                               help_text="The service's expiry when this was invoiced.")
    calculation = models.JSONField(default=dict, blank=True)

    applied_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(hosting_account__isnull=False, domain__isnull=True)
                           | models.Q(hosting_account__isnull=True, domain__isnull=False)),
                name="service_change_exactly_one_service"),
            models.UniqueConstraint(fields=["hosting_account"],
                                    condition=models.Q(status="pending", hosting_account__isnull=False),
                                    name="one_pending_change_per_hosting_account"),
            models.UniqueConstraint(fields=["domain"], condition=models.Q(status="pending", domain__isnull=False),
                                    name="one_pending_change_per_domain"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} of {self.service_label} ({self.get_status_display()})"

    @property
    def service(self):
        return self.hosting_account or self.domain

    @property
    def service_label(self):
        return self.hosting_account.domain if self.hosting_account_id else (self.domain.name if self.domain_id else "")
