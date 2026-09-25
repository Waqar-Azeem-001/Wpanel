from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel

from .storage import PrivateStorage, attachment_path


class Department(TimeStampedModel):
    """Who a ticket is for (Technical, Billing, Sales...). Customers choose one; staff can move a ticket."""

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=60, unique=True)
    description = models.CharField(max_length=300, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    default_assignee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                         related_name="+", help_text="New tickets in this department go to this agent.")

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class TicketStatus(models.TextChoices):
    OPEN = "open", "Open"
    AGENT_REPLY = "agent_reply", "Agent Reply"
    CUSTOMER_REPLY = "customer_reply", "Customer Reply"
    PENDING = "pending", "Pending"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"


class TicketPriority(models.TextChoices):
    LOW = "low", "Low"
    NORMAL = "normal", "Normal"
    HIGH = "high", "High"
    URGENT = "urgent", "Urgent"


class Ticket(TimeStampedModel):
    client = models.ForeignKey("clients.Client", on_delete=models.PROTECT, related_name="tickets")
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="tickets_opened")
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="tickets")
    subject = models.CharField(max_length=200)
    status = models.CharField(max_length=16, choices=TicketStatus.choices, default=TicketStatus.OPEN, db_index=True)
    priority = models.CharField(max_length=8, choices=TicketPriority.choices, default=TicketPriority.NORMAL,
                                db_index=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="tickets_assigned")
    hosting_account = models.ForeignKey("hosting.HostingAccount", null=True, blank=True, on_delete=models.SET_NULL,
                                        related_name="tickets")
    domain = models.ForeignKey("domains.Domain", null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="tickets")
    last_activity_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_customer_reply_at = models.DateTimeField(null=True, blank=True)
    last_staff_reply_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_activity_at", "-id"]

    def __str__(self):
        return f"{self.reference}: {self.subject}"

    @property
    def reference(self):
        return f"T{self.pk:06d}" if self.pk else ""

    @property
    def is_closed(self):
        return self.status == TicketStatus.CLOSED

    @property
    def assignee_label(self):
        agent = self.assigned_to
        return (agent.full_name or agent.email) if agent else "Unassigned"

    @property
    def awaiting_agent(self):
        return self.status in (TicketStatus.OPEN, TicketStatus.CUSTOMER_REPLY)


class MessageKind(models.TextChoices):
    CUSTOMER = "customer", "Customer"
    STAFF = "staff", "Staff"
    SYSTEM = "system", "System"


class TicketMessage(TimeStampedModel):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="+")
    author_name = models.CharField(max_length=200, blank=True, help_text="The author's name when it was written.")
    kind = models.CharField(max_length=10, choices=MessageKind.choices)
    body = models.TextField()
    is_internal = models.BooleanField(default=False, help_text="A note for staff only. Never shown to the customer.")

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.ticket.reference} #{self.pk} ({self.kind})"


class TicketAttachment(TimeStampedModel):
    message = models.ForeignKey(TicketMessage, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(storage=PrivateStorage(), upload_to=attachment_path, max_length=255)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField()

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.original_name


class CannedReply(TimeStampedModel):
    """A predefined reply staff can insert into an answer. ``{client}``, ``{ticket}`` and ``{agent}`` are filled in."""

    title = models.CharField(max_length=100)
    body = models.TextField()
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.CASCADE,
                                   related_name="canned_replies", help_text="Blank = offered in every department.")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "title"]
        verbose_name_plural = "canned replies"

    def __str__(self):
        return self.title


class KBCategory(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=60, unique=True)
    description = models.CharField(max_length=300, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "knowledgebase category"
        verbose_name_plural = "knowledgebase categories"

    def __str__(self):
        return self.name


class KBArticle(TimeStampedModel):
    category = models.ForeignKey(KBCategory, on_delete=models.PROTECT, related_name="articles")
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=80)
    body = models.TextField(help_text="Plain text. Blank lines separate paragraphs.")
    is_published = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        ordering = ["sort_order", "title"]
        constraints = [models.UniqueConstraint(fields=["category", "slug"], name="unique_kb_slug_per_category")]
        verbose_name = "knowledgebase article"

    def __str__(self):
        return self.title
