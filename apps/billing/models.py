"""
Billing configuration: what customers can pay with, how tax is charged, and
discount codes. These are *rules*; the transactional records that use them
(carts, orders, and in Phase 07 invoices/transactions) live elsewhere and only
reference these models. Phase 07 extends this app rather than creating a
second billing app.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel

MONEY = dict(max_digits=10, decimal_places=2)
TWO_PLACES = Decimal("0.01")

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
        if self.discount_type == DiscountType.PERCENT:
            amount = (subtotal * self.value / Decimal(100)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        else:
            amount = self.value
        return min(amount, subtotal)

    def check_window(self, now=None):
        """Raise ``ValueError`` (with a customer-safe message) if the coupon can't be used right now."""
        now = now or timezone.now()
        if not self.is_active:
            raise ValueError("This coupon code is not valid.")
        if self.valid_from and now < self.valid_from:
            raise ValueError("This coupon is not active yet.")
        if self.valid_until and now > self.valid_until:
            raise ValueError("This coupon has expired.")
