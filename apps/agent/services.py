"""
WHPanel's side of the agent protocol (N1): queue a command, hand it to the node that polls for it, take the structured result.

Nothing here runs anything. A job is only a validated, signed request that sits in a queue until the node's own agent fetches it.
"""
import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import ServiceError

from . import catalog, envelope
from django.conf import settings

from .models import FINISHED, AgentJob, AgentNode, JobStatus, NodeStatus

VALID_FOR = 600  # seconds a signed command stays valid if the node does not pick it up
MAX_MESSAGE = 500
MAX_RESULT_BYTES = 20000


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(server):
    """Create (or replace) the agent identity for a server. The token is returned once and never stored. N2 turns this into proper enrolment."""
    token = secrets.token_urlsafe(32)
    node, _ = AgentNode.objects.update_or_create(server=server, defaults={"token_hash": _hash(token), "status": NodeStatus.ENROLLED})
    return node, token


def authenticate(token, fingerprint=""):
    """
    The node a request comes from, or None. The token must belong to an enrolled node. When ``AGENT_REQUIRE_CLIENT_CERT`` is on, the TLS-terminating
    proxy must also have verified a client certificate and passed its SHA-256 fingerprint, and it must be the one issued to this node.
    """
    if not token:
        return None
    node = AgentNode.objects.select_related("server").filter(token_hash=_hash(token), status=NodeStatus.ENROLLED).first()
    if node is None or not hmac.compare_digest(node.token_hash, _hash(token)):
        return None
    if getattr(settings, "AGENT_REQUIRE_CLIENT_CERT", False) and not (fingerprint and node.cert_fingerprint
                                                                      and hmac.compare_digest(node.cert_fingerprint, fingerprint.lower().replace(":", ""))):
        return None
    return node


def dispatch(user, server, command, params, *, idempotency_key=None, correlation_id=None, subject="", request=None):
    """
    Queue one allow-listed command for a server. Same ``idempotency_key`` with the same command and parameters returns the job already made;
    with different ones it is refused. Raises ServiceError for anything not on the allow-list.
    """
    node = AgentNode.objects.filter(server=server).first()  # fresh from the database: a cached server.agent may be from before enrolment
    if node is None or node.status != NodeStatus.ENROLLED:
        raise ServiceError("This server has no enrolled agent.")
    try:
        cleaned = catalog.validate(command, params)
    except catalog.InvalidCommand as exc:
        raise ServiceError(str(exc)) from None
    key = (idempotency_key or uuid.uuid4().hex)[:64]
    existing = AgentJob.objects.filter(node=node, idempotency_key=key).first()
    if existing:
        same = existing.command == command and (existing.params == cleaned or existing.params == catalog.scrub(command, cleaned))
        if not same:
            raise ServiceError("That idempotency key was already used for a different command.")
        return existing
    job = AgentJob.objects.create(node=node, command=command, params=cleaned, correlation_id=(correlation_id or uuid.uuid4().hex)[:64],
                                  idempotency_key=key, subject=subject[:40], nonce=secrets.token_hex(16), requested_by=user if getattr(user, "is_authenticated", False) else None,
                                  expires_at=timezone.now() + timedelta(seconds=VALID_FOR))
    audit.record("agent.job_queued", actor=user, target=job, request=request,
                 metadata={"command": command, "params": catalog.scrub(command, cleaned), "correlation_id": job.correlation_id, "node": server.name})
    return job


def _envelope(job):
    return envelope.sign({"v": catalog.PROTOCOL_VERSION, "job": job.pk, "correlation_id": job.correlation_id, "node": job.node.server_id,
                          "command": job.command, "params": job.params, "issued_at": int(job.created_at.timestamp()),
                          "expires_at": int(job.expires_at.timestamp()), "nonce": job.nonce, "idempotency_key": job.idempotency_key})


def _sweep(node, now):
    """Give up on what can no longer run: commands past their expiry, and leases whose agent never answered."""
    for job in AgentJob.objects.filter(node=node, status=JobStatus.QUEUED, expires_at__lt=now):
        _finish(job, JobStatus.EXPIRED, "expired", "The command expired before the node picked it up.")
    for job in AgentJob.objects.filter(node=node, status=JobStatus.LEASED, leased_until__lt=now):
        spec = catalog.COMMANDS[job.command]
        if spec.idempotent and job.attempts < spec.max_attempts and job.expires_at > now:
            job.status, job.leased_until = JobStatus.QUEUED, None  # safe to run again: same parameters, same result
            job.nonce = secrets.token_hex(16)  # a fresh nonce, or the agent's replay guard would refuse the second delivery
            job.save(update_fields=["status", "leased_until", "nonce", "updated_at"])
        else:  # never run a non-idempotent command twice on a guess
            _finish(job, JobStatus.TIMED_OUT, "timed_out", "The node did not report back in time; the outcome is unknown.")


def _finish(job, status, code, message, ok=None, data=None):
    job.status, job.finished_at, job.result_ok = status, timezone.now(), ok
    job.result_code, job.result_message, job.result_data = code[:60], message[:MAX_MESSAGE], data or {}
    job.params = catalog.scrub(job.command, job.params)
    job.leased_until = None
    job.save()
    audit.record("agent.job_" + status, target=job, metadata={"command": job.command, "correlation_id": job.correlation_id, "code": job.result_code})
    from . import hooks

    hooks.job_finished(job)


def poll(node, agent_version):
    """The agent asks for work. Returns a signed envelope, or None if there is nothing it may run. Raises ServiceError (426) if the agent is too old."""
    version = catalog.parse_version(agent_version)
    if version is None or version < catalog.MIN_AGENT_VERSION:
        raise ServiceError("This agent version is too old; upgrade it.", status_code=426)
    now = timezone.now()
    with transaction.atomic():
        node = AgentNode.objects.select_for_update().get(pk=node.pk)
        node.agent_version, node.last_seen_at = str(agent_version)[:20], now
        node.save(update_fields=["agent_version", "last_seen_at", "updated_at"])
        _sweep(node, now)
        for job in AgentJob.objects.filter(node=node, status=JobStatus.QUEUED).order_by("id"):
            spec = catalog.COMMANDS[job.command]
            if version < spec.min_agent_version:
                continue  # stays queued until the agent is upgraded
            job.status, job.attempts = JobStatus.LEASED, job.attempts + 1
            job.leased_until = now + timedelta(seconds=spec.timeout + 30)
            job.save(update_fields=["status", "attempts", "leased_until", "updated_at"])
            return _envelope(job)
    return None


def record_result(node, job_id, result):
    """The agent reports. ``result`` must be {ok: bool, code: str, message: str, data: object}. Anything else is refused."""
    job = AgentJob.objects.filter(node=node, pk=job_id).first()
    if job is None:
        raise ServiceError("Unknown job.", status_code=404)
    if not (isinstance(result, dict) and isinstance(result.get("ok"), bool) and isinstance(result.get("code"), str)
            and isinstance(result.get("message", ""), str) and isinstance(result.get("data", {}), dict)):
        raise ServiceError("The result is not in the structured format.", status_code=400)
    if len(str(result.get("data", {}))) > MAX_RESULT_BYTES:
        raise ServiceError("The result is too large.", status_code=400)
    if job.status in FINISHED:
        if job.result_ok == result["ok"] and job.result_code == result["code"][:60]:
            return job  # the agent repeated a report whose answer was lost: same outcome, nothing changes
        raise ServiceError("This job is already finished with a different outcome.", status_code=409)
    if job.status != JobStatus.LEASED:
        raise ServiceError("This job was not handed to the agent.", status_code=409)
    _finish(job, JobStatus.SUCCEEDED if result["ok"] else JobStatus.FAILED, result["code"], result.get("message", ""), result["ok"], result.get("data", {}))
    return job


def cancel(user, job, request=None):
    if job.status != JobStatus.QUEUED:
        raise ServiceError("Only a command that has not been started can be cancelled.")
    _finish(job, JobStatus.CANCELLED, "cancelled", "Cancelled before it ran.")
    audit.record("agent.job_cancel_requested", actor=user, target=job, request=request)
    return job
