from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import TimeStampedModel

MONEY = {"max_digits": 12, "decimal_places": 2}


class CommissionKind(models.TextChoices):
    PERCENTAGE = "percentage", "Percentage of the sale"
    FIXED = "fixed", "Fixed amount per sale"


class AffiliateSettings(models.Model):
    """Single row (pk=1): the affiliate programme's switches and its default commission rule."""

    enabled = models.BooleanField(default=True, help_text="Turn the programme off: links stop tracking, nobody can "
                                                          "join and no new commissions are created.")
    require_approval = models.BooleanField(default=True, help_text="Staff approve each new affiliate before their "
                                                                   "link works.")
    cookie_days = models.PositiveSmallIntegerField(
        default=30, validators=[MinValueValidator(1), MaxValueValidator(365)],
        help_text="How long a visitor who followed a referral link is remembered for when they sign up.")
    commission_kind = models.CharField(max_length=12, choices=CommissionKind.choices,
                                       default=CommissionKind.PERCENTAGE)
    commission_value = models.DecimalField(default=10, validators=[MinValueValidator(0)], **MONEY,
                                           help_text="A percentage (e.g. 10) or an amount, by the type above.")
    recurring_months = models.PositiveSmallIntegerField(
        default=0, validators=[MaxValueValidator(60)],
        help_text="Commission on every payment for this many months after the customer signs up. "
                  "0 = only on the customer's first paid invoice.")
    hold_days = models.PositiveSmallIntegerField(
        default=14, validators=[MaxValueValidator(90)],
        help_text="A commission stays Pending this long (a refund window) before it is approved automatically.")
    minimum_payout = models.DecimalField(default=50, validators=[MinValueValidator(0)], **MONEY,
                                         help_text="An affiliate must have at least this much approved to be paid.")
    program_terms = models.TextField(blank=True, help_text="Shown to people joining the programme.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "affiliate settings"

    def __str__(self):
        return "Affiliate settings"

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]

    def clean(self):
        validate_rule(self.commission_kind, self.commission_value)


def validate_rule(kind, value):
    if kind not in CommissionKind.values:
        raise ValidationError("Choose percentage or fixed amount.")
    if value is None or value <= 0:
        raise ValidationError("The commission must be more than zero.")
    if kind == CommissionKind.PERCENTAGE and value > 100:
        raise ValidationError("A percentage cannot be more than 100.")


class AffiliateStatus(models.TextChoices):
    PENDING = "pending", "Awaiting approval"
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    REJECTED = "rejected", "Rejected"


class Affiliate(TimeStampedModel):
    """A person who refers customers and earns commission on what they pay."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="affiliate")
    code = models.SlugField(max_length=32, unique=True, help_text="Used in the referral link.")
    status = models.CharField(max_length=10, choices=AffiliateStatus.choices, default=AffiliateStatus.PENDING,
                              db_index=True)
    commission_kind = models.CharField(max_length=12, choices=CommissionKind.choices, blank=True,
                                       help_text="Blank = the programme's default rule.")
    commission_value = models.DecimalField(null=True, blank=True, validators=[MinValueValidator(0)], **MONEY)
    payout_details = models.TextField(blank=True, max_length=1000,
                                      help_text="Where to send payouts (bank account, wallet number...). "
                                                "Visible to the affiliate and to staff who manage affiliates.")
    visit_count = models.PositiveIntegerField(default=0, help_text="Visitors who followed the link (a rough count).")
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=500, blank=True, help_text="Shown to the affiliate.")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(commission_kind="", commission_value__isnull=True)
                | (~models.Q(commission_kind="") & models.Q(commission_value__isnull=False)),
                name="affiliate_rule_kind_and_value_together"),
        ]

    def __str__(self):
        return f"Affiliate {self.code}"

    @property
    def is_active(self):
        return self.status == AffiliateStatus.ACTIVE

    @property
    def has_override(self):
        return bool(self.commission_kind)


class ReferralSource(models.TextChoices):
    LINK = "link", "Referral link"
    STAFF = "staff", "Added by staff"


class Referral(TimeStampedModel):
    """Which affiliate a client came from. Decided once, at sign-up, and never changed by a later click."""

    affiliate = models.ForeignKey(Affiliate, on_delete=models.PROTECT, related_name="referrals")
    client = models.OneToOneField("clients.Client", on_delete=models.PROTECT, related_name="referral")
    source = models.CharField(max_length=8, choices=ReferralSource.choices, default=ReferralSource.LINK)
    attributed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Referral {self.pk}: {self.affiliate.code}"

    @property
    def label(self):
        """What the affiliate sees instead of the customer's identity."""
        return f"Customer R{self.pk:05d}"


class CommissionStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    PAID = "paid", "Paid"
    REJECTED = "rejected", "Rejected"


class Payout(TimeStampedModel):
    """A payment made to an affiliate, recorded by staff (the money itself moves outside the portal)."""

    affiliate = models.ForeignKey(Affiliate, on_delete=models.PROTECT, related_name="payouts")
    amount = models.DecimalField(**MONEY)
    currency = models.CharField(max_length=3)
    method = models.CharField(max_length=100, help_text="e.g. Bank transfer, JazzCash.")
    reference = models.CharField(max_length=200, blank=True, help_text="The transaction ID.")
    paid_on = models.DateField()
    note = models.CharField(max_length=500, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")

    class Meta:
        ordering = ["-paid_on", "-id"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="payout_amount_positive")]

    def __str__(self):
        return f"Payout {self.code}"

    @property
    def code(self):
        return f"P{self.pk:06d}" if self.pk else ""


class Commission(TimeStampedModel):
    """What one paid invoice earned an affiliate. One per invoice, figures frozen at the time of payment."""

    affiliate = models.ForeignKey(Affiliate, on_delete=models.PROTECT, related_name="commissions")
    referral = models.ForeignKey(Referral, on_delete=models.PROTECT, related_name="commissions")
    invoice = models.OneToOneField("billing.Invoice", on_delete=models.PROTECT, related_name="commission")
    status = models.CharField(max_length=10, choices=CommissionStatus.choices, default=CommissionStatus.PENDING,
                              db_index=True)
    currency = models.CharField(max_length=3)
    base_amount = models.DecimalField(**MONEY, help_text="What the commission is worked out on: the invoice "
                                                         "excluding tax, after discount.")
    kind = models.CharField(max_length=12, choices=CommissionKind.choices)
    rate = models.DecimalField(**MONEY, help_text="The percentage or amount that applied.")
    original_amount = models.DecimalField(**MONEY, help_text="What it was when the invoice was paid.")
    amount = models.DecimalField(**MONEY, help_text="What it is now (lower after a refund).")
    eligible_at = models.DateTimeField(help_text="When the hold ends and it can be approved automatically.")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=500, blank=True)
    needs_review = models.BooleanField(default=False, help_text="The invoice was refunded after this was paid out.")
    payout = models.ForeignKey(Payout, null=True, blank=True, on_delete=models.PROTECT, related_name="commissions")

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gte=0), name="commission_amount_not_negative"),
            models.CheckConstraint(
                condition=~models.Q(status="paid") | models.Q(payout__isnull=False),
                name="paid_commission_has_a_payout"),
        ]

    def __str__(self):
        return f"Commission {self.code} ({self.get_status_display()})"

    @property
    def code(self):
        return f"K{self.pk:06d}" if self.pk else ""
