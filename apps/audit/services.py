"""The single audit-logging entry point. All modules record events through ``record``."""
import logging

from apps.core.request_context import get_request_id
from apps.core.utils import client_ip

from .models import AuditEvent

logger = logging.getLogger(__name__)

SENSITIVE_KEYS = {"password", "new_password", "old_password", "token", "secret", "api_key", "access", "refresh"}


def _scrub(metadata):
    if not isinstance(metadata, dict):
        return {}
    return {k: ("[redacted]" if k.lower() in SENSITIVE_KEYS else v) for k, v in metadata.items()}


def record(action, *, actor=None, target=None, metadata=None, request=None):
    """
    Record an audit event.

    ``actor`` is the user performing the action (None for system actions).
    ``target`` is any model instance the action applies to.
    """
    if actor is not None and (getattr(actor, "is_system", False) or not getattr(actor, "is_authenticated", False)):
        actor = None  # the system actor (or nobody) is recorded as a NULL actor, i.e. "system"
    event = AuditEvent(
        action=action,
        actor=actor,
        actor_repr=str(actor) if actor else "",
        metadata=_scrub(metadata or {}),
        request_id=get_request_id() if get_request_id() != "-" else "",
    )
    if target is not None:
        event.target_type = target._meta.label_lower
        event.target_id = str(target.pk)
        event.target_repr = str(target)[:255]
    if request is not None:
        event.ip_address = client_ip(request)
        event.user_agent = request.META.get("HTTP_USER_AGENT", "")[:255]
    event.save()
    logger.info("audit %s actor=%s target=%s:%s", action, event.actor_repr or "system", event.target_type, event.target_id)
    return event
