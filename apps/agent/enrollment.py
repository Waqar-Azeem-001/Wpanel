"""
Node enrolment (N2): staff invite a fresh server, the agent proves it holds the one-time token, WHPanel signs its certificate request and records
what the node says about itself. After that the node keeps in touch with heartbeats, can renew its certificate, and can be revoked.
"""
import hashlib
import ipaddress
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import ServiceError

from . import envelope, pki
from .models import AgentNode, EnrollmentToken, NodeStatus

TOKEN_HOURS = 24
OFFLINE_AFTER = timedelta(minutes=3)  # no contact for this long and the node is called offline
KNOWN_CAPABILITIES = ("web", "php", "database", "ssl", "dns", "mail", "backup", "metrics")
SERVICE_STATES = ("up", "degraded", "down")


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def invite(user, server, request=None):
    """Make a one-time enrolment token for a server. Returned once; only its hash is kept. A node that is already enrolled must be revoked first."""
    node, created = AgentNode.objects.get_or_create(server=server, defaults={"status": NodeStatus.PENDING, "token_hash": None})
    if node.status == NodeStatus.ENROLLED:
        raise ServiceError("This server already has an enrolled agent. Revoke it first to enrol a new one.")
    node.enrollment_tokens.filter(used_at__isnull=True).delete()  # only the newest invitation works
    token = "whe_" + secrets.token_urlsafe(32)
    EnrollmentToken.objects.create(node=node, token_hash=_hash(token), expires_at=timezone.now() + timedelta(hours=TOKEN_HOURS),
                                   created_by=user if getattr(user, "is_authenticated", False) else None)
    if node.status == NodeStatus.REVOKED:
        node.status, node.revoked_at = NodeStatus.PENDING, None
        node.save(update_fields=["status", "revoked_at", "updated_at"])
    audit.record("agent.node_invited", actor=user, target=node, request=request, metadata={"server": server.name, "valid_hours": TOKEN_HOURS})
    return node, token


def _text(data, key, limit, required=False):
    value = data.get(key, "")
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ServiceError(f"{key} is missing or not valid.", status_code=400)
    return value.strip()


def _count(data, key, high):
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= high:
        raise ServiceError(f"{key} must be a whole number from 1 to {high}.", status_code=400)
    return value


def clean_inventory(data):
    if not isinstance(data, dict):
        raise ServiceError("Send the node's details as an object.", status_code=400)
    ips = data.get("ip_addresses")
    if not isinstance(ips, list) or not 1 <= len(ips) <= 16:
        raise ServiceError("ip_addresses must list 1 to 16 addresses.", status_code=400)
    try:
        ips = [str(ipaddress.ip_address(i)) for i in ips]
    except ValueError:
        raise ServiceError("ip_addresses contains something that is not an IP address.", status_code=400) from None
    caps = data.get("capabilities")
    if not isinstance(caps, list) or not caps or any(c not in KNOWN_CAPABILITIES for c in caps):
        raise ServiceError(f"capabilities must be a non-empty list of: {', '.join(KNOWN_CAPABILITIES)}.", status_code=400)
    return {"hostname": _text(data, "hostname", 255, True), "ip_addresses": ips, "os_name": _text(data, "os_name", 80), "cpu_cores": _count(data, "cpu_cores", 4096),
            "ram_mb": _count(data, "ram_mb", 16 * 1024 * 1024), "disk_gb": _count(data, "disk_gb", 1024 * 1024), "capabilities": sorted(set(caps))}


def _identity(node, signed):
    node.cert_fingerprint, node.cert_serial, node.cert_expires_at = signed["fingerprint"], signed["serial"], signed["expires_at"]


def enroll(token, csr_pem, inventory, agent_version, request=None):
    """
    The agent's first contact. Everything is checked before anything is spent: the details and the certificate request are validated, then the
    token is consumed in the same transaction that creates the identity, so a bad request does not burn the token and a good one works exactly once.
    """
    details = clean_inventory(inventory)
    version = str(agent_version)[:20]
    refused = False
    with transaction.atomic():
        row = (EnrollmentToken.objects.select_for_update().select_related("node__server").filter(token_hash=_hash(token or "")).first())
        refused = row is None or row.used_at is not None or row.expires_at < timezone.now() or row.node.status != NodeStatus.PENDING
        if not refused:
            return _enroll_locked(row, csr_pem, details, version, request)
    audit.record("agent.enroll_refused", metadata={"reason": "unknown, used or expired token"}, request=request)  # outside the transaction so it is kept
    raise ServiceError("This enrolment token is not valid.", status_code=401)


def _enroll_locked(row, csr_pem, details, version, request):
    from .services import _hash as _token_hash  # the same hashing as the agent's long-lived token

    node = row.node
    try:
        signed = pki.sign_csr(csr_pem, node.pk)
    except pki.CsrError as exc:
        raise ServiceError(str(exc), status_code=400) from None
    api_token = secrets.token_urlsafe(32)
    now = timezone.now()
    row.used_at = now
    row.save(update_fields=["used_at", "updated_at"])
    for field, value in details.items():
        setattr(node, field, value)
    node.status, node.token_hash, node.agent_version, node.enrolled_at, node.last_seen_at = NodeStatus.ENROLLED, _token_hash(api_token), version, now, now
    _identity(node, signed)
    node.save()
    server = node.server
    if not server.ip_address:
        server.ip_address = details["ip_addresses"][0]
        server.save(update_fields=["ip_address", "updated_at"])
    audit.record("agent.node_enrolled", target=node, request=request, metadata={"server": server.name, "hostname": details["hostname"], "cert_serial": signed["serial"]})
    return {"node_id": node.server_id, "token": api_token, "certificate": signed["pem"], "ca_certificate": pki.ca_certificate_pem(),
            "signing_public_key": envelope.public_key_b64(), "certificate_expires_at": signed["expires_at"].isoformat()}


def renew(node, csr_pem, request=None):
    """A node swaps its certificate before it expires. The old one stops working at once."""
    try:
        signed = pki.sign_csr(csr_pem, node.pk)
    except pki.CsrError as exc:
        raise ServiceError(str(exc), status_code=400) from None
    _identity(node, signed)
    node.save(update_fields=["cert_fingerprint", "cert_serial", "cert_expires_at", "updated_at"])
    audit.record("agent.cert_renewed", target=node, request=request, metadata={"cert_serial": signed["serial"]})
    return {"certificate": signed["pem"], "ca_certificate": pki.ca_certificate_pem(), "certificate_expires_at": signed["expires_at"].isoformat()}


def heartbeat(node, data):
    """Store the node's latest figures. Only known fields with sane values are kept; the rest is dropped, not trusted."""
    if not isinstance(data, dict):
        raise ServiceError("Send an object.", status_code=400)
    health = {}
    for key, high in (("load_1m", 100000), ("cpu_percent", 100), ("memory_used_mb", 16 * 1024 * 1024), ("disk_used_gb", 1024 * 1024), ("sites", 1000000), ("mail_queue", 10_000_000)):
        value = data.get(key)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= high:
                raise ServiceError(f"{key} is not a sensible number.", status_code=400)
            health[key] = value
    services = data.get("services", {})
    if not isinstance(services, dict) or len(services) > 20 or any(not isinstance(k, str) or len(k) > 30 or v not in SERVICE_STATES for k, v in services.items()):
        raise ServiceError(f"services must map names to one of: {', '.join(SERVICE_STATES)}.", status_code=400)
    health["services"] = services
    node.health, node.health_at, node.last_seen_at = health, timezone.now(), timezone.now()
    node.save(update_fields=["health", "health_at", "last_seen_at", "updated_at"])
    return node


def revoke(user, node, request=None):
    """Cut a node off now: its token and certificate stop being accepted and its queued commands are cancelled."""
    from .models import JobStatus
    from .services import _finish

    node.status, node.token_hash, node.revoked_at = NodeStatus.REVOKED, None, timezone.now()
    node.save(update_fields=["status", "token_hash", "revoked_at", "updated_at"])
    node.enrollment_tokens.filter(used_at__isnull=True).delete()
    for job in node.jobs.filter(status=JobStatus.QUEUED):
        _finish(job, JobStatus.CANCELLED, "revoked", "The node was revoked before this ran.")
    audit.record("agent.node_revoked", actor=user, target=node, request=request, metadata={"server": node.server.name, "cert_serial": node.cert_serial})
    return node


def health_state(node, now=None):
    """healthy / degraded / down / offline / pending / revoked, from what the node last reported and when."""
    now = now or timezone.now()
    if node.status != NodeStatus.ENROLLED:
        return node.status
    if node.last_seen_at is None or now - node.last_seen_at > OFFLINE_AFTER:
        return "offline"
    states = list((node.health or {}).get("services", {}).values())
    if "down" in states:
        return "down"
    if "degraded" in states:
        return "degraded"
    return "healthy"
