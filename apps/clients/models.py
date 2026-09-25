from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models

from apps.core.models import TimeStampedModel

country_code = RegexValidator(r"^[A-Z]{2}$", "Use a two-letter ISO 3166 country code, e.g. US, GB, PK.")
currency_code = RegexValidator(r"^[A-Z]{3}$", "Use a three-letter ISO 4217 currency code, e.g. USD.")


class ClientStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"
    CLOSED = "closed", "Closed"


class Client(TimeStampedModel):
    """
    The customer account a business relationship is held with.

    Orders, services, domains, invoices, transactions and tickets (later phases)
    belong to a Client, not to an individual User. Users reach a client through
    ``ClientContact``.
    """

    company_name = models.CharField(max_length=200, blank=True)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(help_text="Primary/billing email for the account.")
    phone = models.CharField(max_length=32, blank=True)

    address_line1 = models.CharField(max_length=200, blank=True)
    address_line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    postcode = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=2, blank=True, validators=[country_code])
    tax_id = models.CharField(max_length=50, blank=True, help_text="VAT/GST/NTN number, if any.")
    currency = models.CharField(max_length=3, default="USD", validators=[currency_code])
    tax_exempt = models.BooleanField(default=False, help_text="No tax is charged on new orders and invoices. Staff only.")

    status = models.CharField(max_length=16, choices=ClientStatus.choices, default=ClientStatus.ACTIVE,
                              db_index=True)
    notes = models.TextField(blank=True, help_text="Internal staff notes. Never shown to the client.")

    users = models.ManyToManyField(settings.AUTH_USER_MODEL, through="ClientContact", related_name="clients")

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["email"]), models.Index(fields=["company_name"])]

    def __str__(self):
        return self.display_name

    @property
    def contact_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def display_name(self):
        return self.company_name or self.contact_name or self.email

    @property
    def reference(self):
        return f"C{self.pk:06d}" if self.pk else ""


class ContactRole(models.TextChoices):
    OWNER = "owner", "Owner"
    BILLING = "billing", "Billing"
    TECHNICAL = "technical", "Technical"


class ClientContact(TimeStampedModel):
    """A user's membership of a client account."""

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="contacts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="client_contacts")
    role = models.CharField(max_length=16, choices=ContactRole.choices, default=ContactRole.TECHNICAL)

    class Meta:
        ordering = ["client_id", "role", "id"]
        constraints = [models.UniqueConstraint(fields=["client", "user"], name="unique_client_contact")]

    def __str__(self):
        return f"{self.user} → {self.client} ({self.role})"
