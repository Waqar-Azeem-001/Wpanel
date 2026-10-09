from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class AgentNode(TimeStampedModel):
    """
    The agent installed on one server. N1 identifies it with a long random token (only its hash is stored); N2 replaces enrolment with a one-time
    token and adds the per-node client certificate (mutual TLS) from ADR 16.
    """

    server = models.OneToOneField("products.Server", on_delete=models.CASCADE, related_name="agent")
    token_hash = models.CharField(max_length=64, unique=True)
    agent_version = models.CharField(max_length=20, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Agent on {self.server}"


class JobStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    LEASED = "leased", "Running on the node"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    EXPIRED = "expired", "Expired before it ran"
    TIMED_OUT = "timed_out", "Timed out"
    CANCELLED = "cancelled", "Cancelled"


FINISHED = (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.EXPIRED, JobStatus.TIMED_OUT, JobStatus.CANCELLED)


class AgentJob(TimeStampedModel):
    node = models.ForeignKey(AgentNode, on_delete=models.CASCADE, related_name="jobs")
    command = models.CharField(max_length=40, db_index=True)
    params = models.JSONField(default=dict, help_text="Secret values are replaced with *** once the job has finished.")
    status = models.CharField(max_length=12, choices=JobStatus.choices, default=JobStatus.QUEUED, db_index=True)
    correlation_id = models.CharField(max_length=64, db_index=True)
    idempotency_key = models.CharField(max_length=64)
    nonce = models.CharField(max_length=64, unique=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    expires_at = models.DateTimeField(help_text="After this the command is no longer signed as valid and will not be started.")
    leased_until = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    finished_at = models.DateTimeField(null=True, blank=True)
    result_ok = models.BooleanField(null=True)
    result_code = models.CharField(max_length=60, blank=True)
    result_message = models.CharField(max_length=500, blank=True)
    result_data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["node", "idempotency_key"], name="agent_job_one_per_idempotency_key")]

    def __str__(self):
        return f"{self.command} #{self.pk} ({self.status})"
