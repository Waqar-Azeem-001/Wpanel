"""
Ticket states (roadmap Phase 10): Open -> Agent Reply -> Customer Reply -> Pending -> Resolved -> Closed.

The states describe whose move it is, not a fixed path:

* **Open** - new, waiting for an agent.
* **Agent Reply** - an agent has answered; waiting for the customer.
* **Customer Reply** - the customer has answered; waiting for an agent.
* **Pending** - on hold (waiting on a third party, or for something to happen).
* **Resolved** - the agent believes it is solved. A customer reply reopens it; otherwise it closes by itself.
* **Closed** - finished. Only staff can reopen it, and a customer's reply reopens it for a short window.

Every status change goes through ``transition``, which enforces the table and writes an audit event with the old
and new status, so a ticket's history is complete by construction.
"""
from django.utils import timezone
from rest_framework import status as http

from apps.audit import services as audit
from apps.core.exceptions import ServiceError

from .models import Ticket, TicketStatus

S = TicketStatus

# A closed ticket can only be reopened; every other status can move to any other status.
REOPEN_TO = {S.OPEN}
AWAITING_AGENT = (S.OPEN, S.CUSTOMER_REPLY)
ACTIVE = (S.OPEN, S.AGENT_REPLY, S.CUSTOMER_REPLY, S.PENDING)
DONE = (S.RESOLVED, S.CLOSED)


def can_transition(current, new):
    if current == new or new not in S.values:
        return False
    if current == S.CLOSED:
        return new in REOPEN_TO
    return True


def transition(ticket, new, *, actor=None, action="ticket.status_changed", reason="", request=None, **metadata):
    """
    Move ``ticket`` to ``new``. Locks the row, so call it inside a transaction. Returns the refreshed ticket; a move to
    the status it already has is a quiet no-op.
    """
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    if ticket.status == new:
        return ticket
    if not can_transition(ticket.status, new):
        raise ServiceError(f"A {ticket.get_status_display().lower()} ticket cannot become {S(new).label.lower()}.",
                           code="invalid_transition", status_code=http.HTTP_409_CONFLICT)
    previous, now = ticket.status, timezone.now()
    ticket.status = new
    ticket.resolved_at = now if new == S.RESOLVED else (ticket.resolved_at if new == S.CLOSED else None)
    ticket.closed_at = now if new == S.CLOSED else None
    ticket.last_activity_at = now
    ticket.save(update_fields=["status", "resolved_at", "closed_at", "last_activity_at", "updated_at"])
    audit.record(action, actor=actor, target=ticket,
                 metadata={"from": previous, "to": new, "client_id": ticket.client_id,
                           "reason": (reason or "")[:500], **metadata}, request=request)
    return ticket
