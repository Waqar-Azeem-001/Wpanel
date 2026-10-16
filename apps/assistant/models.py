from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class AssistantSettings(TimeStampedModel):
    """The owner's switches for the AI assistant (one row). It is off until the owner turns it on. The API key is never stored here: it lives on the server."""

    enabled = models.BooleanField(default=False, help_text="Off = the assistant answers nobody. The kill switch.")
    model = models.CharField(max_length=80, default="claude-sonnet-5-5")
    daily_limit = models.PositiveSmallIntegerField(default=30, help_text="Messages one client may send per day; it keeps the cost in hand.")
    change_hours = models.PositiveSmallIntegerField(default=24, help_text="How long the team needs for a change the Agent asks for on the server; the Agent tells the customer this.")

    class Meta:
        verbose_name_plural = "assistant settings"

    @classmethod
    def load(cls):
        row, _ = cls.objects.get_or_create(pk=1)
        return row


class ConversationStatus(models.TextChoices):
    AI = "ai", "AI handling"
    WAITING = "waiting", "Waiting for staff"
    WITH_STAFF = "with_staff", "With staff"
    CLOSED = "closed", "Closed"


class Conversation(TimeStampedModel):
    client = models.ForeignKey("clients.Client", on_delete=models.CASCADE, related_name="assistant_conversations")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assistant_conversations")
    status = models.CharField(max_length=12, choices=ConversationStatus.choices, default=ConversationStatus.AI, db_index=True)
    ticket = models.ForeignKey("support.Ticket", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")  # where a human took over
    handoff_reason = models.CharField(max_length=300, blank=True)
    handoff_summary = models.TextField(blank=True)  # a few lines for the person who takes the chat, so the client never repeats themselves
    handoff_requested_at = models.DateTimeField(null=True, blank=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        return f"Assistant chat #{self.pk} ({self.status})"


class SenderType(models.TextChoices):
    CLIENT = "client", "Client"
    AI = "ai", "Assistant"
    STAFF = "staff", "Staff"
    EVENT = "event", "Event"


class Message(TimeStampedModel):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.CharField(max_length=8, choices=SenderType.choices)
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    body = models.TextField(blank=True)
    tool_call = models.JSONField(null=True, blank=True)  # for an event line: which tool the assistant used (name and what it looked at, never secrets)

    class Meta:
        ordering = ["created_at", "id"]
