"""
The order lifecycle (roadmap Phase 09).

    Draft -> Pending Payment -> Paid -> Processing -> Provisioning -> Active

with the exceptional states Fraud, Failed, Cancelled, Suspended and Terminated. Every status change in
the system goes through ``transition``, which refuses a move the table below does not allow and writes
an audit event carrying the old and new status - so "every state transition must be auditable" holds
by construction, not by each caller remembering to.

Who may trigger which move is decided by the callers (customers cancel their own unpaid order; staff
mark fraud, suspend, terminate; the system pays and fulfils); this module only knows what is *legal*.
"""
from rest_framework import status

from apps.audit import services as audit
from apps.core.exceptions import ServiceError

from .models import Order, OrderStatus

S = OrderStatus

ALLOWED = {
    S.DRAFT: {S.PENDING_PAYMENT, S.CANCELLED},
    S.PENDING_PAYMENT: {S.PAID, S.CANCELLED, S.FRAUD},
    S.PAID: {S.PROCESSING, S.FRAUD},
    S.PROCESSING: {S.PROVISIONING, S.FAILED},
    S.PROVISIONING: {S.ACTIVE, S.FAILED},
    S.ACTIVE: {S.SUSPENDED, S.TERMINATED},
    S.SUSPENDED: {S.ACTIVE, S.TERMINATED},
    S.FAILED: {S.PROCESSING, S.FRAUD, S.CANCELLED},
    S.FRAUD: {S.PENDING_PAYMENT, S.PAID, S.CANCELLED},
    S.CANCELLED: set(),
    S.TERMINATED: set(),
}

# Groups used by the staff screens (roadmap: All, Pending, Active, Fraud, Cancelled).
GROUPS = {
    "pending": (S.DRAFT, S.PENDING_PAYMENT, S.PAID, S.PROCESSING, S.PROVISIONING, S.FAILED),
    "active": (S.ACTIVE, S.SUSPENDED),
    "fraud": (S.FRAUD,),
    "cancelled": (S.CANCELLED, S.TERMINATED),
}
GROUP_LABELS = {"": "All", "pending": "Pending", "active": "Active", "fraud": "Fraud", "cancelled": "Cancelled"}

# Statuses in which the customer's services are in use, or being made ready.
IN_FLIGHT = (S.PROCESSING, S.PROVISIONING)


def can_transition(current, new):
    return new in ALLOWED.get(current, set())


def transition(order, new, *, actor=None, action="order.status_changed", reason="", request=None, **metadata):
    """
    Move ``order`` to ``new``. Locks the order row, so call it inside a transaction (and, where an
    invoice is also being changed, after locking the invoice - the system-wide lock order is
    invoice -> order). Returns the refreshed order; a move to the status it already has is a no-op.
    """
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status == new:
        return order
    if not can_transition(order.status, new):
        raise ServiceError(f"An order that is {order.get_status_display().lower()} cannot become "
                           f"{S(new).label.lower()}.", code="invalid_transition",
                           status_code=status.HTTP_409_CONFLICT)
    previous = order.status
    order.status = new
    fields = ["status", "updated_at"]
    reason = (reason or "").strip()[:500]
    if new == S.CANCELLED:
        order.cancel_reason = reason
        fields.append("cancel_reason")
    else:
        order.status_reason = reason
        fields.append("status_reason")
    order.save(update_fields=fields)
    audit.record(action, actor=actor, target=order,
                 metadata={"from": previous, "to": new, "reason": reason, **metadata}, request=request)
    _announce(order, new)
    return order


def _announce(order, new):
    """The moves that people need to hear about: the customer when an order is cancelled, the team when one fails."""
    from django.urls import reverse

    from apps.notifications import services as notifications

    if new == S.CANCELLED:
        link = reverse("orders_customer:detail", args=[order.pk])
        notifications.dispatch_client("order.cancelled", order.client, title=f"Order {order.reference} was cancelled",
                                      link=link, context={"order": order, "link": link})
    elif new == S.FAILED:
        notifications.notify_team("order.failed", "manage_orders",
                                  title=f"Order {order.reference} could not be fully fulfilled",
                                  body=order.status_reason[:200], link=reverse("orders_staff:detail", args=[order.pk]))


def timeline(order):
    """The audit events that describe this order's life, oldest first."""
    from apps.audit.models import AuditEvent

    return AuditEvent.objects.filter(target_type="orders.order", target_id=str(order.pk)).order_by("created_at", "id")
