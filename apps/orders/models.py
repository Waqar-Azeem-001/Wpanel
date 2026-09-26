"""
Carts and orders.

A ``Cart`` holds only *selections* (which product, which billing cycle, which
domain) - never prices. Prices are always recomputed server-side from the
catalogue (``apps.orders.pricing``), so nothing a browser sends can change what
something costs. Checkout turns a cart into an ``Order`` whose items carry a
snapshot of the price, setup fee and billing details at that moment.

``Order`` is created once, here, with the roadmap's complete lifecycle
vocabulary; Phase 06 only uses PENDING_PAYMENT and CANCELLED. Phase 07
(billing) adds invoices/payments and Phase 09 the remaining transitions and
screens - they extend this model rather than creating another.
"""
from django.conf import settings
from django.db import models

from apps.core import crypto
from apps.core.models import TimeStampedModel


class CartStatus(models.TextChoices):
    OPEN = "open", "Open"
    CONVERTED = "converted", "Converted to an order"


class ItemKind(models.TextChoices):
    HOSTING = "hosting", "Hosting"
    ADDON = "addon", "Addon"
    DOMAIN_REGISTER = "domain_register", "Domain registration"
    DOMAIN_TRANSFER = "domain_transfer", "Domain transfer"


class Cart(TimeStampedModel):
    """
    One open cart per (user, client); the client is the account the order will belong to.

    A visitor who has not signed in has a *guest cart*: no user and no client, only a ``guest_token`` kept in their session.
    When they sign in or create an account at checkout, its selections move into their own cart and the guest cart is
    deleted. A guest cart holds selections only, like every cart, so nothing about it can change a price.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE,
                             related_name="carts")
    client = models.ForeignKey("clients.Client", null=True, blank=True, on_delete=models.CASCADE, related_name="carts")
    guest_token = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    status = models.CharField(max_length=16, choices=CartStatus.choices, default=CartStatus.OPEN, db_index=True)
    coupon = models.ForeignKey("billing.Coupon", null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="carts")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "client"], condition=models.Q(status=CartStatus.OPEN),
                                    name="unique_open_cart_per_user_client")
        ]

    def __str__(self):
        return f"Cart {self.pk} ({self.user or 'guest'})"

    @property
    def is_guest(self):
        return self.user_id is None


class CartItem(TimeStampedModel):
    """
    A selection in a cart. Which fields matter depends on ``kind``:

    * hosting          - product, domain_name, billing_cycle (+ custom_months)
    * addon            - addon, parent (the hosting item), billing_cycle (+ custom_months)
    * domain_register  - domain_name, years
    * domain_transfer  - domain_name, auth code (encrypted; never returned by the API)
    """

    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    kind = models.CharField(max_length=20, choices=ItemKind.choices)
    product = models.ForeignKey("products.Product", null=True, blank=True, on_delete=models.CASCADE)
    addon = models.ForeignKey("products.Addon", null=True, blank=True, on_delete=models.CASCADE)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="addon_items")
    domain_name = models.CharField(max_length=253, blank=True)
    billing_cycle = models.CharField(max_length=16, blank=True)
    custom_months = models.PositiveSmallIntegerField(default=0)
    years = models.PositiveSmallIntegerField(default=1)
    auth_code_encrypted = models.TextField(blank=True, editable=False)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.get_kind_display()} #{self.pk}"

    def set_auth_code(self, raw):
        self.auth_code_encrypted = crypto.encrypt(raw) if raw else ""

    def get_auth_code(self):
        return crypto.decrypt(self.auth_code_encrypted)


class OrderStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PENDING_PAYMENT = "pending_payment", "Pending Payment"
    PAID = "paid", "Paid"
    PROCESSING = "processing", "Processing"
    PROVISIONING = "provisioning", "Provisioning"
    ACTIVE = "active", "Active"
    # Exceptional states (roadmap Phase 09).
    FRAUD = "fraud", "Fraud"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"
    SUSPENDED = "suspended", "Suspended"
    TERMINATED = "terminated", "Terminated"


class Order(TimeStampedModel):
    """A commercial request/purchase. Distinct from an invoice (financial document) and a service (what's provisioned)."""

    client = models.ForeignKey("clients.Client", on_delete=models.PROTECT, related_name="orders")
    placed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="orders_placed")
    status = models.CharField(max_length=20, choices=OrderStatus.choices, default=OrderStatus.PENDING_PAYMENT,
                              db_index=True)
    currency = models.CharField(max_length=3)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_name = models.CharField(max_length=100, blank=True)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2)

    coupon = models.ForeignKey("billing.Coupon", null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="orders")
    coupon_code = models.CharField(max_length=50, blank=True)
    payment_method = models.ForeignKey("billing.PaymentMethod", null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name="orders")
    payment_method_name = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True, help_text="Customer notes.")
    cancel_reason = models.CharField(max_length=500, blank=True)
    status_reason = models.CharField(max_length=500, blank=True,
                                     help_text="Why the order is in a fraud/failed/suspended/terminated state.")

    # Billing details as they were when the order was placed (later edits to the
    # client must not rewrite history - invoices will be built from these).
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
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.reference

    @property
    def reference(self):
        return f"O{self.pk:06d}" if self.pk else ""


class FulfilmentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"


class OrderItem(TimeStampedModel):
    """One line of an order, with the price/setup fee snapshotted at checkout."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    kind = models.CharField(max_length=20, choices=ItemKind.choices)
    description = models.CharField(max_length=255)
    product = models.ForeignKey("products.Product", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="+")
    addon = models.ForeignKey("products.Addon", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="addon_items")
    domain_name = models.CharField(max_length=253, blank=True)
    billing_cycle = models.CharField(max_length=16, blank=True)
    custom_months = models.PositiveSmallIntegerField(default=0)
    years = models.PositiveSmallIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    setup_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    line_total = models.DecimalField(max_digits=10, decimal_places=2,
                                     help_text="First payment for this line: unit price plus setup fee.")
    # Carried over from the cart for fulfilment; cleared once the transfer is submitted.
    auth_code_encrypted = models.TextField(blank=True, editable=False)

    # Fulfilment (Phase 09): what this line became once the order was paid.
    hosting_account = models.ForeignKey("hosting.HostingAccount", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="order_items")
    domain = models.ForeignKey("domains.Domain", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="order_items")
    fulfilment_status = models.CharField(max_length=10, choices=FulfilmentStatus.choices,
                                         default=FulfilmentStatus.PENDING)
    fulfilment_error = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.order.reference}: {self.description}"

    def get_auth_code(self):
        return crypto.decrypt(self.auth_code_encrypted)


class CouponRedemption(TimeStampedModel):
    """Records that an order used a coupon. Usage counts are derived from these (excluding cancelled orders)."""

    coupon = models.ForeignKey("billing.Coupon", on_delete=models.PROTECT, related_name="redemptions")
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="coupon_redemption")
    client = models.ForeignKey("clients.Client", on_delete=models.PROTECT, related_name="coupon_redemptions")
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.coupon.code} on {self.order.reference}"
