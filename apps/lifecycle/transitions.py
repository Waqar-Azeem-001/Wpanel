"""
The one table of legal moves for a cancellation request; every status change goes through ``transition``.

    Awaiting review -> Approved -> Completed
         |                |
         +-> Rejected     +-> Withdrawn        (Awaiting review -> Withdrawn too)
"""
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import ServiceError

from .models import CancellationStatus as S

TRANSITIONS = {
    S.PENDING: {S.APPROVED, S.REJECTED, S.WITHDRAWN},
    S.APPROVED: {S.COMPLETED, S.WITHDRAWN},
    S.COMPLETED: set(),
    S.REJECTED: set(),
    S.WITHDRAWN: set(),
}


def can_transition(current, to):
    return to in TRANSITIONS.get(current, set())


def transition(cr, to, *, actor=None, request=None, **fields):
    """Move ``cr`` to ``to`` (the caller holds the row lock), saving ``fields`` in the same write."""
    if not can_transition(cr.status, to):
        raise ServiceError(f"A cancellation request that is {cr.get_status_display().lower()} cannot be "
                           f"{S(to).label.lower()}.", code="invalid_status")
    previous, cr.status = cr.status, to
    for name, value in fields.items():
        setattr(cr, name, value)
    cr.save(update_fields=["status", "updated_at", *fields])
    audit.record(f"cancellation.{to}", actor=actor, target=cr,
                 metadata={"from": previous, "to": to, "service": cr.service_name, "kind": cr.kind,
                           "client_id": cr.client_id, "at": timezone.now().isoformat()}, request=request)
    return cr
