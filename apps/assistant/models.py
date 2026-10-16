from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class AssistantSettings(TimeStampedModel):
    """The owner's switches for the AI assistant (one row). It is off until the owner turns it on. The API key is never stored here: it lives on the server."""

    MODES = [("knowledge", "Knowledge base (no outside service)"), ("claude", "Claude (needs an API key)")]

    enabled = models.BooleanField(default=False, help_text="Off = the assistant answers nobody. The kill switch.")
    mode = models.CharField(max_length=10, choices=MODES, default="knowledge", help_text="Knowledge base: answers from the entries you keep in Setup and checks the customer's own account. Claude: free conversation (needs the key).")
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
    pending = models.JSONField(default=dict, blank=True)  # what the Agent is waiting to hear, for example which domain a check is for

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


class KnowledgeEntry(TimeStampedModel):
    """One thing the Agent knows: the ways customers ask it (one per line, any language) and the answer, optionally with a live check on the customer's own account."""

    ACTIONS = [("", "Just answer"), ("website_status", "Check the website"), ("ssl", "Check SSL"), ("disk", "Check disk and bandwidth"), ("error_log", "Show the error log"),
               ("dns", "Show DNS records"), ("email_quota", "Check mailboxes"), ("invoices", "Show invoices"), ("services", "List their services"),
               ("handoff", "Ask a person to join"), ("ticket", "Open a ticket")]

    title = models.CharField(max_length=120)
    keywords = models.TextField(help_text="How customers ask this, one wording per line. Add Urdu and Roman Urdu wordings too.")
    answer = models.TextField(help_text="What the Agent says. Say where in the panel to go.")
    action = models.CharField(max_length=20, choices=ACTIONS, blank=True, default="")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=100)
    hits = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "title"]
        verbose_name_plural = "knowledge entries"

    def __str__(self):
        return self.title


class Unanswered(TimeStampedModel):
    """A question the Agent could not match, kept (without who asked) so staff can add what is missing."""

    text = models.CharField(max_length=300, unique=True)
    count = models.PositiveIntegerField(default=1)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-count", "-last_seen"]
