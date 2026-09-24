import re

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models

from apps.core import crypto
from apps.core.models import TimeStampedModel

# Single-label TLDs only for the MVP (.com, .io, .dev, ...). Multi-part public
# suffixes (.co.uk, .com.au) need a public-suffix list and are a known gap -
# see the Phase 04 gap report.
DOMAIN_NAME_VALIDATOR = RegexValidator(
    re.compile(r"^(?=.{1,253}$)(?:[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"),
    "Enter a valid domain name, e.g. example.com.",
)
HOSTNAME_VALIDATOR = RegexValidator(
    re.compile(r"^(?=.{1,253}$)(?:[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"),
    "Enter a valid hostname, e.g. ns1.example.com.",
)


def domain_tld(name):
    """'.com' from 'example.com'. Single-label TLDs only - see the module docstring."""
    return "." + name.rsplit(".", 1)[-1]


class RegistrarProvider(TimeStampedModel):
    """
    Live, admin-configured registrar connection - the domain-world equivalent of
    ``notifications.EmailProvider``. Exactly one may be active; credentials are
    encrypted at rest. Managed only through Django admin (superuser), the same
    precedent set for EmailProvider: no staff web page, no custom portal
    permission check.

    ``kind`` selects the adapter class (see ``apps.domains.adapters``). Only
    "manual" ships in Phase 04 - it does not call any real registrar, it
    simulates registration entirely in our own database so the rest of the
    system (models, services, screens) can be built and tested before a real
    registrar contract/API is connected. Adding a real registrar later means
    writing one adapter class and one new ``kind`` choice; nothing else changes.
    """

    class Kind(models.TextChoices):
        MANUAL = "manual", "Manual (no real registration - development/testing only)"

    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.MANUAL)
    sandbox = models.BooleanField(default=True, help_text="Use the registrar's test/sandbox API where supported.")
    # Free-form so each future adapter can define its own credential shape
    # (API key, secret, reseller ID, ...) without a schema migration.
    credentials_encrypted = models.TextField(blank=True, editable=False)
    is_active = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"], condition=models.Q(is_active=True), name="single_active_registrar_provider"
            )
        ]

    def __str__(self):
        return f"{self.name} ({'active' if self.is_active else 'inactive'})"

    def set_credentials(self, data: dict):
        import json

        self.credentials_encrypted = crypto.encrypt(json.dumps(data or {}))

    def get_credentials(self) -> dict:
        import json

        raw = crypto.decrypt(self.credentials_encrypted)
        return json.loads(raw) if raw else {}


class TldPricing(TimeStampedModel):
    """
    Per-TLD pricing. Domains are deliberately NOT ``apps.products.Product`` rows
    (see the Phase 03 gap report) - register/renew/transfer pricing keyed by TLD
    doesn't fit the billing-cycle shape used for hosting products.
    """

    tld = models.CharField(max_length=20, unique=True, help_text='Including the dot, e.g. ".com".')
    register_price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    renew_price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    transfer_price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    redemption_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)],
        help_text="Fee to recover a domain shortly after it expires, where the registry supports it.",
    )
    min_years = models.PositiveSmallIntegerField(default=1)
    max_years = models.PositiveSmallIntegerField(default=10)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["tld"]

    def __str__(self):
        return self.tld

    def clean(self):
        if not self.tld.startswith("."):
            raise ValidationError({"tld": 'Must start with a dot, e.g. ".com".'})
        if self.min_years > self.max_years:
            raise ValidationError({"max_years": "Must be greater than or equal to the minimum."})


class DomainStatus(models.TextChoices):
    PENDING_REGISTRATION = "pending_registration", "Pending Registration"
    PENDING_TRANSFER_IN = "pending_transfer_in", "Pending Transfer"
    ACTIVE = "active", "Active"
    EXPIRED = "expired", "Expired"
    CANCELLED = "cancelled", "Cancelled"
    FAILED = "failed", "Failed"


# Statuses that hold a live claim on the name - a second request for the same
# name is blocked while one of these is in effect.
LIVE_STATUSES = (
    DomainStatus.PENDING_REGISTRATION, DomainStatus.PENDING_TRANSFER_IN, DomainStatus.ACTIVE, DomainStatus.EXPIRED,
)


class Domain(TimeStampedModel):
    """
    A domain name belonging to a client. Belongs to Client (not directly to a
    User) - see apps.clients for why: business records live on the client,
    users reach them through ClientContact.
    """

    client = models.ForeignKey("clients.Client", on_delete=models.PROTECT, related_name="domains")
    name = models.CharField(max_length=253, validators=[DOMAIN_NAME_VALIDATOR])
    tld = models.CharField(max_length=20, editable=False, db_index=True)
    registrar = models.ForeignKey(RegistrarProvider, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="domains")
    status = models.CharField(max_length=24, choices=DomainStatus.choices, default=DomainStatus.PENDING_REGISTRATION,
                              db_index=True)
    years = models.PositiveSmallIntegerField(default=1)
    registered_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    auto_renew = models.BooleanField(default=True)
    is_locked = models.BooleanField(default=True, help_text="Registrar transfer lock.")
    nameservers = models.JSONField(default=list, blank=True)
    # Transfer-in authorization code, encrypted; cleared once the transfer completes.
    auth_code_encrypted = models.TextField(blank=True, editable=False)
    provider_ref = models.CharField(max_length=100, blank=True, help_text="Registrar's reference for this domain.")
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["name"], condition=models.Q(status__in=LIVE_STATUSES),
                                    name="unique_live_domain_name")
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = self.name.lower()
        self.tld = domain_tld(self.name)
        super().save(*args, **kwargs)

    def clean(self):
        if self.nameservers:
            if not (2 <= len(self.nameservers) <= 13):
                raise ValidationError({"nameservers": "Provide between 2 and 13 nameservers."})
            for ns in self.nameservers:
                HOSTNAME_VALIDATOR(ns)

    def set_auth_code(self, raw):
        self.auth_code_encrypted = crypto.encrypt(raw) if raw else ""

    def get_auth_code(self):
        return crypto.decrypt(self.auth_code_encrypted)


class DnsRecordType(models.TextChoices):
    A = "A", "A"
    AAAA = "AAAA", "AAAA"
    CNAME = "CNAME", "CNAME"
    MX = "MX", "MX"
    TXT = "TXT", "TXT"
    NS = "NS", "NS"
    SRV = "SRV", "SRV"


class DnsRecord(TimeStampedModel):
    """
    One zone record for a domain. Records are stored regardless of which
    nameservers the domain currently uses - if they point elsewhere, the UI
    notes that these records aren't live, in case the customer switches back.
    """

    domain = models.ForeignKey(Domain, on_delete=models.CASCADE, related_name="dns_records")
    record_type = models.CharField(max_length=8, choices=DnsRecordType.choices)
    name = models.CharField(max_length=253, blank=True, help_text="Subdomain part, blank for the root.")
    content = models.CharField(max_length=500)
    ttl = models.PositiveIntegerField(default=3600, validators=[MinValueValidator(60)])
    priority = models.PositiveSmallIntegerField(null=True, blank=True, help_text="MX/SRV only.")

    class Meta:
        ordering = ["record_type", "name"]

    def __str__(self):
        return f"{self.record_type} {self.name or '@'} -> {self.content}"

    def clean(self):
        if self.record_type in (DnsRecordType.MX, DnsRecordType.SRV) and self.priority is None:
            raise ValidationError({"priority": "Required for MX and SRV records."})
        if self.record_type not in (DnsRecordType.MX, DnsRecordType.SRV) and self.priority is not None:
            raise ValidationError({"priority": "Only used for MX and SRV records."})
