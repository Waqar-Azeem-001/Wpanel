import datetime as dt

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
    offline_ticket = models.BooleanField(default=False, help_text="When nobody on the team is online (or it is outside working hours), a request for a person becomes a ticket straight away instead of waiting.")
    hours_enabled = models.BooleanField(default=False, help_text="Count only the working hours below as 'team available'.")
    hours_start = models.TimeField(default=dt.time(9, 0))
    hours_end = models.TimeField(default=dt.time(18, 0))
    hours_days = models.CharField(max_length=20, default="0,1,2,3,4,5", help_text="Weekdays the team works, 0 = Monday.")
    hours_timezone = models.CharField(max_length=60, default="Asia/Karachi")
    changes_enabled = models.BooleanField(default=True, help_text="Off = the Agent can no longer propose changes (SSL, mailbox size, PHP, scans, backups). The kill switch for changes.")
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
    action = models.ForeignKey("assistant.PendingAction", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    feedback = models.SmallIntegerField(default=0)  # the customer's thumb on an answer: 1 helped, -1 did not
    body = models.TextField(blank=True)
    tool_call = models.JSONField(null=True, blank=True)  # for an event line: which tool the assistant used (name and what it looked at, never secrets)

    class Meta:
        ordering = ["created_at", "id"]


class KnowledgeEntry(TimeStampedModel):
    """One thing the Agent knows: the ways customers ask it (one per line, any language) and the answer, optionally with a live check on the customer's own account."""

    ACTIONS = [("add_dns_record", "Propose with code: add a DNS record"), ("delete_dns_record", "Propose with code: delete a DNS record"),
               ("restore_backup", "Propose with code: restore a backup"), ("delete_mailbox", "Propose with code: delete a mailbox"),
               ("renew_ssl", "Propose: renew SSL"), ("force_https", "Propose: force HTTPS"), ("set_mailbox_quota", "Propose: change a mailbox size"),
               ("set_php_setting", "Propose: change a PHP limit"), ("scan_website", "Propose: malware scan"), ("create_backup", "Propose: backup now"),
               ("", "Just answer"), ("website_status", "Check the website"), ("ssl", "Check SSL"), ("disk", "Check disk and bandwidth"), ("error_log", "Show the error log"),
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


class PendingAction(TimeStampedModel):
    """A change the Agent proposed. It runs only when the customer who was shown it presses Approve, once, within half an hour."""

    STATES = [("pending", "Waiting for approval"), ("running", "Running"), ("done", "Done"), ("failed", "Failed"), ("cancelled", "Cancelled"), ("expired", "Expired"), ("locked", "Locked")]
    NEEDS = [("approve", "Approve"), ("code", "Security code")]

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="actions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    tool = models.CharField(max_length=40)
    params = models.JSONField(default=dict)
    summary = models.CharField(max_length=300)
    account_id = models.PositiveIntegerField(default=0)  # the hosting plan it is about; set by the panel, never by the model
    needs = models.CharField(max_length=8, choices=NEEDS, default="approve")
    status = models.CharField(max_length=10, choices=STATES, default="pending", db_index=True)
    result = models.CharField(max_length=500, blank=True)
    executed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.tool}: {self.summary} ({self.status})"


class VerificationCode(TimeStampedModel):
    """The security code for one change. Only a hash is kept; the code itself goes by email to the address on the customer's account and is never shown in the chat."""

    action = models.OneToOneField(PendingAction, on_delete=models.CASCADE, related_name="verification")
    code_hash = models.CharField(max_length=128)
    sent_to = models.CharField(max_length=120)  # masked, for the page: a*****@gmail.com
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    last_sent_at = models.DateTimeField()


class StaffAvailability(TimeStampedModel):
    """Whether a support person says they are online for live chats. It counts only while they keep a chat page open (the pages refresh it)."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_availability")
    online = models.BooleanField(default=False)
    last_seen = models.DateTimeField(null=True, blank=True)
