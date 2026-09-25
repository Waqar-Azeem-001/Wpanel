"""
Billing configuration: what customers can pay with, how tax is charged, and
discount codes. These are *rules*; the transactional records that use them
(carts, orders, and in Phase 07 invoices/transactions) live elsewhere and only
reference these models. Phase 07 extends this app rather than creating a
second billing app.
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

from apps.core import crypto
from apps.core.models import TimeStampedModel

from .calculations import TWO_PLACES, discount_amount

MONEY = dict(max_digits=10, decimal_places=2)
DOC_MONEY = dict(max_digits=12, decimal_places=2)

country_code = RegexValidator(r"^[A-Z]{2}$", "Use a two-letter ISO 3166 country code, e.g. US, GB, PK.")


class PaymentMethod(TimeStampedModel):
    """
    A way for a customer to pay, as shown at checkout (e.g. "Bank transfer",
    "Credit card"). Deliberately separate from payment *gateways*: Phase 07
    attaches a live-configured gateway adapter to a method, so this stays a
    customer-facing label plus instructions.
    """

    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    instructions = models.TextField(blank=True, help_text="Shown to the customer at checkout and on their order.")
    provider = models.ForeignKey("billing.PaymentProvider", null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="payment_methods",
                                 help_text="Optional. Lets customers pay this way online through the gateway.")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class TaxRule(TimeStampedModel):
    """
    Tax charged on top of the price (tax-exclusive), chosen by the client's
    country. One rule per country; the rule with a blank country is the default
    for every other country. No matching active rule means no tax.
    """

    name = models.CharField(max_length=100, help_text='e.g. "VAT" or "GST".')
    country = models.CharField(max_length=2, blank=True, unique=True, validators=[country_code],
                               help_text="Blank = the default rule for all other countries.")
    rate = models.DecimalField(max_digits=5, decimal_places=2,
                               validators=[MinValueValidator(0), MaxValueValidator(100)],
                               help_text="Percentage, e.g. 15.00.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["country"]

    def __str__(self):
        return f"{self.name} {self.rate}% ({self.country or 'default'})"


class DiscountType(models.TextChoices):
    PERCENT = "percent", "Percentage"
    FIXED = "fixed", "Fixed amount"


class Coupon(TimeStampedModel):
    """
    A discount code, applied to the first payment of an order (recurring
    renewals are charged at the normal price - see the Phase 06 gap report).
    How many times a code has been used is derived from the orders that
    redeemed it (``orders.CouponRedemption``), not stored here.
    """

    code = models.CharField(max_length=50, unique=True, help_text="Stored upper-case; matched case-insensitively.")
    description = models.CharField(max_length=200, blank=True)
    discount_type = models.CharField(max_length=10, choices=DiscountType.choices)
    value = models.DecimalField(validators=[MinValueValidator(Decimal("0.01"))], **MONEY)
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    max_redemptions = models.PositiveIntegerField(null=True, blank=True, help_text="Blank = unlimited.")
    one_per_client = models.BooleanField(default=False)
    min_subtotal = models.DecimalField(default=0, validators=[MinValueValidator(0)], **MONEY)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def clean(self):
        if self.discount_type == DiscountType.PERCENT and self.value is not None and self.value > 100:
            raise ValidationError({"value": "A percentage discount cannot exceed 100."})
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            raise ValidationError({"valid_until": "Must be after the start date."})

    def amount_for(self, subtotal):
        """Discount this coupon gives on ``subtotal`` (never more than the subtotal)."""
        return discount_amount(self.discount_type, self.value, subtotal)

    def check_window(self, now=None):
        """Raise ``ValueError`` (with a customer-safe message) if the coupon can't be used right now."""
        now = now or timezone.now()
        if not self.is_active:
            raise ValueError("This coupon code is not valid.")
        if self.valid_from and now < self.valid_from:
            raise ValueError("This coupon is not active yet.")
        if self.valid_until and now > self.valid_until:
            raise ValueError("This coupon has expired.")


# --- Phase 07: invoices, payments, quotes ---------------------------------------------------

class NumberSequence(models.Model):
    """
    Gap-free document numbering. A number is only taken when a document is
    issued (inside the issuing transaction, under a row lock), so a rolled-back
    issue never leaves a hole in the sequence.
    """

    key = models.SlugField(max_length=30, unique=True)
    last_number = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.key}: {self.last_number}"


class BillingSettings(models.Model):
    """Single row (pk=1): the seller details and defaults printed on invoices and quotes."""

    company_name = models.CharField(max_length=200, blank=True)
    address = models.TextField(blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    tax_id = models.CharField(max_length=50, blank=True, help_text="Your own VAT/GST/NTN number.")
    invoice_prefix = models.CharField(max_length=10, default="INV-", blank=True)
    quote_prefix = models.CharField(max_length=10, default="QUO-", blank=True)
    payment_terms_days = models.PositiveSmallIntegerField(default=14, validators=[MaxValueValidator(365)],
                                                          help_text="Days from issue until an invoice is due.")
    quote_validity_days = models.PositiveSmallIntegerField(default=30, validators=[MinValueValidator(1),
                                                                                    MaxValueValidator(365)])
    invoice_footer = models.TextField(blank=True, help_text="Printed at the bottom of every invoice, e.g. bank details.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "billing settings"

    def __str__(self):
        return "Billing settings"

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]


class PaymentProvider(TimeStampedModel):
    """
    A live, admin-configured payment gateway connection - the payment-world
    equivalent of ``EmailProvider`` / ``RegistrarProvider``. Credentials and the
    webhook signing secret are encrypted at rest and are never read from the
    environment. More than one provider may be active (a payment method chooses
    which one it uses). Managed only through Django admin (superuser).

    ``kind`` selects the adapter class (see ``apps.billing.gateways``). Only the
    simulated "test" gateway ships in Phase 07; connecting a real gateway means
    adding one adapter class and one ``kind`` choice.
    """

    class Kind(models.TextChoices):
        TEST = "test", "Test gateway (simulated checkout - no real money)"

    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.TEST)
    sandbox = models.BooleanField(default=True, help_text="Use the gateway's test mode where supported.")
    credentials_encrypted = models.TextField(blank=True, editable=False)
    webhook_secret_encrypted = models.TextField(blank=True, editable=False)
    is_active = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.name} ({'active' if self.is_active else 'inactive'})"

    def clean(self):
        if self.kind == self.Kind.TEST and self.is_active and not settings.ALLOW_TEST_PAYMENT_GATEWAY:
            raise ValidationError("The test gateway is disabled in this environment (ALLOW_TEST_PAYMENT_GATEWAY).")

    def set_credentials(self, data):
        import json

        self.credentials_encrypted = crypto.encrypt(json.dumps(data or {}))

    def get_credentials(self):
        import json

        raw = crypto.decrypt(self.credentials_encrypted)
        return json.loads(raw) if raw else {}

    def set_webhook_secret(self, raw):
        self.webhook_secret_encrypted = crypto.encrypt(raw)

    def get_webhook_secret(self):
        return crypto.decrypt(self.webhook_secret_encrypted)


class InvoiceStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    UNPAID = "unpaid", "Unpaid"
    PARTIALLY_PAID = "partially_paid", "Partially paid"
    PAID = "paid", "Paid"
    CANCELLED = "cancelled", "Cancelled"
    REFUNDED = "refunded", "Refunded"


# "Overdue" is not stored: it is an unpaid/partially paid invoice past its due date, so it can never be stale.
OVERDUE = "overdue"
OPEN_STATUSES = (InvoiceStatus.UNPAID, InvoiceStatus.PARTIALLY_PAID)


class BillingDocument(TimeStampedModel):
    """Fields shared by invoices and quotes: the client, totals and a snapshot of the billing details."""

    client = models.ForeignKey("clients.Client", on_delete=models.PROTECT, related_name="%(class)ss")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    number = models.CharField(max_length=30, blank=True, help_text="Assigned when issued.")
    currency = models.CharField(max_length=3)
    issue_date = models.DateField(null=True, blank=True)

    subtotal = models.DecimalField(default=0, validators=[MinValueValidator(0)], **DOC_MONEY)
    discount_total = models.DecimalField(default=0, validators=[MinValueValidator(0)], **DOC_MONEY)
    discount_label = models.CharField(max_length=100, blank=True)
    tax_name = models.CharField(max_length=100, blank=True)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0,
                                   validators=[MinValueValidator(0), MaxValueValidator(100)])
    tax_total = models.DecimalField(default=0, validators=[MinValueValidator(0)], **DOC_MONEY)
    total = models.DecimalField(default=0, validators=[MinValueValidator(0)], **DOC_MONEY)
    notes = models.TextField(blank=True, help_text="Shown to the customer on the document.")

    billing_name = models.CharField(max_length=300, blank=True)
    billing_company = models.CharField(max_length=200, blank=True)
    billing_email = models.EmailField(blank=True)
    billing_phone = models.CharField(max_length=32, blank=True)
    billing_address_line1 = models.CharField(max_length=200, blank=True)
    billing_address_line2 = models.CharField(max_length=200, blank=True)
    billing_city = models.CharField(max_length=100, blank=True)
    billing_state = models.CharField(max_length=100, blank=True)
    billing_postcode = models.CharField(max_length=20, blank=True)
    billing_country = models.CharField(max_length=2, blank=True)
    billing_tax_id = models.CharField(max_length=50, blank=True)

    class Meta:
        abstract = True
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.reference


class Invoice(BillingDocument):
    order = models.ForeignKey("orders.Order", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="invoices")
    quote = models.ForeignKey("billing.Quote", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="invoices")
    status = models.CharField(max_length=16, choices=InvoiceStatus.choices, default=InvoiceStatus.DRAFT,
                              db_index=True)
    due_date = models.DateField(null=True, blank=True, db_index=True)
    amount_paid = models.DecimalField(default=0, validators=[MinValueValidator(0)], **DOC_MONEY,
                                      help_text="Derived from succeeded payment transactions.")
    amount_refunded = models.DecimalField(default=0, validators=[MinValueValidator(0)], **DOC_MONEY,
                                          help_text="Derived from succeeded refund transactions.")
    paid_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=500, blank=True)
    payment_method = models.ForeignKey(PaymentMethod, null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name="invoices")
    payment_method_name = models.CharField(max_length=100, blank=True)

    class Meta(BillingDocument.Meta):
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~models.Q(number=""), name="unique_invoice_number"),
        ]

    @property
    def reference(self):
        return self.number or f"Draft #{self.pk}"

    @property
    def is_issued(self):
        return self.status != InvoiceStatus.DRAFT

    @property
    def balance_due(self):
        if self.status in (InvoiceStatus.DRAFT, InvoiceStatus.CANCELLED):
            return Decimal("0.00")
        return max(self.total - self.amount_paid, Decimal("0.00"))

    @property
    def is_overdue(self):
        return (self.status in OPEN_STATUSES and self.due_date is not None
                and self.due_date < timezone.localdate())

    @property
    def display_status(self):
        return OVERDUE if self.is_overdue else self.status

    @property
    def display_status_label(self):
        return "Overdue" if self.is_overdue else self.get_status_display()

    @property
    def can_be_paid(self):
        return self.status in OPEN_STATUSES and self.balance_due > 0

    @property
    def overpaid(self):
        """Money held beyond the invoice total (a payment that landed after the invoice was settled or cancelled)."""
        owed = Decimal("0.00") if self.status == InvoiceStatus.CANCELLED else self.total
        return max(self.amount_paid - self.amount_refunded - owed, Decimal("0.00"))


class LineItem(models.Model):
    """One line of an invoice or quote. Amounts are stored, not recomputed, so a document is permanent."""

    description = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(validators=[MinValueValidator(0)], **DOC_MONEY)
    amount = models.DecimalField(validators=[MinValueValidator(0)], **DOC_MONEY,
                                 help_text="Quantity x unit price, before discount and tax.")
    discount_amount = models.DecimalField(default=0, validators=[MinValueValidator(0)], **DOC_MONEY)
    taxable = models.BooleanField(default=True)
    tax_amount = models.DecimalField(default=0, validators=[MinValueValidator(0)], **DOC_MONEY)
    total = models.DecimalField(default=0, validators=[MinValueValidator(0)], **DOC_MONEY)

    class Meta:
        abstract = True
        ordering = ["id"]

    def __str__(self):
        return self.description


class InvoiceItem(LineItem):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="items")
    order_item = models.ForeignKey("orders.OrderItem", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")


class QuoteStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SENT = "sent", "Sent"
    ACCEPTED = "accepted", "Accepted"
    DECLINED = "declined", "Declined"
    CANCELLED = "cancelled", "Cancelled"


class Quote(BillingDocument):
    status = models.CharField(max_length=16, choices=QuoteStatus.choices, default=QuoteStatus.DRAFT, db_index=True)
    valid_until = models.DateField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta(BillingDocument.Meta):
        constraints = [
            models.UniqueConstraint(fields=["number"], condition=~models.Q(number=""), name="unique_quote_number"),
        ]

    @property
    def reference(self):
        return self.number or f"Draft #{self.pk}"

    @property
    def is_expired(self):
        return (self.status == QuoteStatus.SENT and self.valid_until is not None
                and self.valid_until < timezone.localdate())

    @property
    def display_status_label(self):
        return "Expired" if self.is_expired else self.get_status_display()

    @property
    def can_be_accepted(self):
        return self.status == QuoteStatus.SENT and not self.is_expired


class QuoteItem(LineItem):
    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name="items")


class BillableItem(TimeStampedModel):
    """
    A one-off charge staff record against a client (e.g. "Server migration") that
    has not been invoiced yet. It is collected onto a draft invoice; the link to
    that invoice is what marks it as billed.
    """

    client = models.ForeignKey("clients.Client", on_delete=models.PROTECT, related_name="billable_items")
    description = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(validators=[MinValueValidator(Decimal("0.01"))], **DOC_MONEY)
    taxable = models.BooleanField(default=True)
    invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="billable_items")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.description

    @property
    def amount(self):
        return self.unit_price * self.quantity


class TransactionType(models.TextChoices):
    PAYMENT = "payment", "Payment"
    REFUND = "refund", "Refund"


class TransactionStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"


class Transaction(TimeStampedModel):
    """
    One movement of money against an invoice. The invoice's paid/refunded
    amounts and status are *derived* from its succeeded transactions, so the
    financial position can always be rebuilt from these permanent records.
    """

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="transactions")
    client = models.ForeignKey("clients.Client", on_delete=models.PROTECT, related_name="transactions")
    type = models.CharField(max_length=10, choices=TransactionType.choices, default=TransactionType.PAYMENT)
    status = models.CharField(max_length=10, choices=TransactionStatus.choices, default=TransactionStatus.PENDING,
                              db_index=True)
    amount = models.DecimalField(**DOC_MONEY)
    currency = models.CharField(max_length=3)
    payment_method = models.ForeignKey(PaymentMethod, null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name="transactions")
    method_name = models.CharField(max_length=100, blank=True)
    provider = models.ForeignKey(PaymentProvider, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="transactions")
    external_id = models.CharField(max_length=128, blank=True, help_text="The gateway's reference.")
    idempotency_key = models.CharField(max_length=100, blank=True)
    reference = models.CharField(max_length=200, blank=True, help_text="e.g. a bank transfer reference.")
    note = models.CharField(max_length=500, blank=True)
    failure_reason = models.CharField(max_length=500, blank=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="refunds")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    occurred_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="transaction_amount_positive"),
            models.UniqueConstraint(fields=["provider", "external_id"],
                                    condition=~models.Q(external_id="") & models.Q(provider__isnull=False),
                                    name="unique_provider_external_id"),
            models.UniqueConstraint(fields=["idempotency_key"], condition=~models.Q(idempotency_key=""),
                                    name="unique_transaction_idempotency_key"),
        ]

    def __str__(self):
        return f"{self.get_type_display()} {self.amount} {self.currency} ({self.get_status_display()})"

    @property
    def code(self):
        return f"T{self.pk:06d}" if self.pk else ""


class WebhookStatus(models.TextChoices):
    PROCESSED = "processed", "Processed"
    IGNORED = "ignored", "Ignored"
    FAILED = "failed", "Failed"


class WebhookEvent(TimeStampedModel):
    """Every verified gateway event, keyed by the gateway's event id so a redelivery is recognised and skipped."""

    provider = models.ForeignKey(PaymentProvider, on_delete=models.CASCADE, related_name="webhook_events")
    event_id = models.CharField(max_length=128)
    event_type = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=10, choices=WebhookStatus.choices, default=WebhookStatus.PROCESSED)
    detail = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["provider", "event_id"], name="unique_webhook_event")]

    def __str__(self):
        return f"{self.provider_id}:{self.event_id}"
