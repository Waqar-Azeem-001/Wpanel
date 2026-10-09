from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class NodeStatus(models.TextChoices):
    PENDING = "pending", "Waiting for the agent to enrol"
    ENROLLED = "enrolled", "Enrolled"
    REVOKED = "revoked", "Revoked"


class AgentNode(TimeStampedModel):
    """
    The agent installed on one server. It is identified by a client certificate from WHPanel's own CA (mutual TLS, ADR 16) plus a long random token
    (only its hash is stored). Both are issued once, at enrolment with a one-time token.
    """

    server = models.OneToOneField("products.Server", on_delete=models.CASCADE, related_name="agent")
    status = models.CharField(max_length=10, choices=NodeStatus.choices, default=NodeStatus.ENROLLED)
    token_hash = models.CharField(max_length=64, unique=True, null=True, blank=True)
    agent_version = models.CharField(max_length=20, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    # what the node reported about itself at enrolment
    hostname = models.CharField(max_length=255, blank=True)
    ip_addresses = models.JSONField(default=list, blank=True)
    os_name = models.CharField(max_length=80, blank=True)
    cpu_cores = models.PositiveIntegerField(null=True, blank=True)
    ram_mb = models.PositiveIntegerField(null=True, blank=True)
    disk_gb = models.PositiveIntegerField(null=True, blank=True)
    capabilities = models.JSONField(default=list, blank=True, help_text="Which kinds of work this node can do, for example web, php, database.")
    capacity_sites = models.PositiveIntegerField(null=True, blank=True, help_text="How many websites staff allow on this node. Blank means not set.")
    # the latest heartbeat
    health = models.JSONField(default=dict, blank=True)
    health_at = models.DateTimeField(null=True, blank=True)
    # the identity issued at enrolment
    cert_fingerprint = models.CharField(max_length=64, blank=True)
    cert_serial = models.CharField(max_length=40, blank=True)
    cert_expires_at = models.DateTimeField(null=True, blank=True)
    enrolled_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Agent on {self.server}"


class EnrollmentToken(TimeStampedModel):
    """A one-time secret that lets one fresh server enrol. Only its hash is stored; it works once and expires."""

    node = models.ForeignKey(AgentNode, on_delete=models.CASCADE, related_name="enrollment_tokens")
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")


class CertificateAuthority(models.Model):
    """WHPanel's own CA for node certificates (one row). The key is encrypted like every other stored credential."""

    certificate_pem = models.TextField()
    key_encrypted = models.TextField(editable=False)
    created_at = models.DateTimeField(auto_now_add=True)


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
