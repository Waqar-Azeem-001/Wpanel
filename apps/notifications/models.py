import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core import crypto
from apps.core.models import TimeStampedModel


class EmailProvider(TimeStampedModel):
    """
    Live, admin-configured outgoing email connection.

    Exactly one provider may be active. Credentials are encrypted at rest.
    """

    class Kind(models.TextChoices):
        SMTP = "smtp", "SMTP"

    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.SMTP)
    host = models.CharField(max_length=255)
    port = models.PositiveIntegerField(default=587)
    username = models.CharField(max_length=255, blank=True)
    password_encrypted = models.TextField(blank=True, editable=False)
    use_tls = models.BooleanField(default=True, help_text="STARTTLS (usually port 587).")
    use_ssl = models.BooleanField(default=False, help_text="Implicit TLS (usually port 465).")
    timeout = models.PositiveIntegerField(default=20, help_text="Seconds.")
    from_email = models.CharField(max_length=255, help_text='e.g. "Wpanel <billing@example.com>"')
    is_active = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"], condition=models.Q(is_active=True), name="single_active_email_provider"
            )
        ]

    def __str__(self):
        return f"{self.name} ({'active' if self.is_active else 'inactive'})"

    def clean(self):
        if self.use_tls and self.use_ssl:
            raise ValidationError("Use either STARTTLS or SSL, not both.")

    def set_password(self, raw):
        self.password_encrypted = crypto.encrypt(raw)

    def get_password(self):
        return crypto.decrypt(self.password_encrypted)


class EmailMessage(TimeStampedModel):
    """Every outgoing email is recorded here before delivery is attempted."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="emails"
    )
    event = models.CharField(max_length=100, blank=True, db_index=True)
    template = models.CharField(max_length=100)
    to_email = models.EmailField()
    from_email = models.CharField(max_length=255, blank=True)
    subject = models.CharField(max_length=255)
    body_text = models.TextField()
    body_html = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    # Emails carrying a secret (a temporary password, a one-time link) are kept encrypted only until they are
    # delivered, then wiped - the log must not be a second copy of a credential.
    is_sensitive = models.BooleanField(default=False)
    sensitive_body = models.TextField(blank=True, editable=False)

    # Open tracking (a signal, not proof of reading): the first open and how many times the pixel was fetched.
    track_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    opened_at = models.DateTimeField(null=True, blank=True)
    open_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.subject} -> {self.to_email} [{self.status}]"

    @property
    def was_opened(self):
        return self.opened_at is not None

    @property
    def event_label(self):
        from . import events

        return events.EVENTS[self.event].label if self.event in events.EVENTS else ""


class Notification(TimeStampedModel):
    """In-app notification shown in the web portal and exposed to API clients."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    event = models.CharField(max_length=100, db_index=True)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    link = models.CharField(max_length=500, blank=True)
    read_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.title} -> {self.user}"

    @property
    def is_read(self):
        return self.read_at is not None


class NotificationPreference(TimeStampedModel):
    """
    A person's choice for one category of optional message (orders, services, support tickets, team alerts).
    No row means "the event's defaults". Essential messages (account security, billing, service continuity) ignore
    preferences entirely.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_preferences")
    category = models.CharField(max_length=20)
    email_enabled = models.BooleanField(default=True)
    in_app_enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "category"], name="unique_preference_per_category")]

    def __str__(self):
        return f"{self.user} / {self.category}"
