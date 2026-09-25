"""
Staff actions on an order's lifecycle: flag or clear fraud, retry a failed fulfilment, suspend, unsuspend and
terminate. Each is one legal move in ``lifecycle`` plus, where the order's services must follow (suspend,
unsuspend, terminate), the same change made to each of them - through the ordinary hosting services, run as the
system; the requesting staff member is recorded on the order's own audit event.

A cascade that cannot finish (one server is unreachable) changes nothing about the order and says which service
failed, so it can simply be run again: every step skips what is already done.
"""
from django.db import transaction
from rest_framework import status

from apps.accounts.roles import perm
from apps.billing.models import Invoice, InvoiceStatus
from apps.core.exceptions import ServiceError
from apps.core.system import SYSTEM
from apps.hosting import services as hosting_services
from apps.hosting.models import HostingStatus

from . import fulfilment, lifecycle
from .models import Order, OrderStatus


def _require_manage(actor):
    if not actor.has_perm(perm("manage_orders")):
        raise ServiceError("You do not have permission to perform this action.", code="permission_denied",
                           status_code=status.HTTP_403_FORBIDDEN)


def _lock(order):
    """Lock in the system-wide order: the order's invoices first, then the order itself."""
    list(Invoice.objects.select_for_update().filter(order=order))
    return Order.objects.select_for_update().get(pk=order.pk)


def _is_paid(order):
    return Invoice.objects.filter(order=order, status__in=(InvoiceStatus.PAID, InvoiceStatus.REFUNDED)).exists()


@transaction.atomic
def mark_fraud(actor, order, *, reason="", request=None):
    """Hold an order as suspected fraud. Nothing is fulfilled for it while it is held."""
    _require_manage(actor)
    order = _lock(order)
    return lifecycle.transition(order, OrderStatus.FRAUD, actor=actor, action="order.fraud_flagged", reason=reason,
                                request=request)


@transaction.atomic
def clear_fraud(actor, order, *, reason="", request=None):
    """Release a held order: it resumes as paid (and is fulfilled) if its invoice was paid, else as awaiting payment."""
    _require_manage(actor)
    order = _lock(order)
    if order.status != OrderStatus.FRAUD:
        raise ServiceError("This order is not being held as fraud.", code="invalid_status")
    target = OrderStatus.PAID if _is_paid(order) else OrderStatus.PENDING_PAYMENT
    order = lifecycle.transition(order, target, actor=actor, action="order.fraud_cleared", reason=reason,
                                 request=request)
    if target == OrderStatus.PAID:
        fulfilment.schedule(order.pk)
    return order


def retry_fulfilment(actor, order, *, request=None):
    """Run fulfilment again for an order that failed (only what is still outstanding is redone)."""
    _require_manage(actor)
    order = Order.objects.get(pk=order.pk)
    if order.status != OrderStatus.FAILED:
        raise ServiceError("Only an order whose fulfilment failed can be retried.", code="invalid_status")
    from apps.audit import services as audit

    audit.record("order.fulfilment_retried", actor=actor, target=order, request=request)
    return fulfilment.fulfil_order(order.pk)


def _hosting_accounts(order):
    return [item.hosting_account for item in order.items.select_related("hosting_account")
            if item.hosting_account_id]


def _cascade(actor, order, eligible, action, label):
    failures = []
    for account in _hosting_accounts(order):
        account.refresh_from_db()
        if account.status not in eligible:
            continue
        try:
            action(account)
        except ServiceError as exc:
            failures.append(f"{account.domain}: {exc.message}")
    if failures:
        raise ServiceError(f"Could not {label} every service, so the order was left as it is. "
                           + "; ".join(failures) + " Fix that and run it again.", code="cascade_failed")


def suspend_order(actor, order, *, reason="", request=None):
    """Suspend an active order and its hosting accounts."""
    _require_manage(actor)
    order = Order.objects.get(pk=order.pk)
    if order.status != OrderStatus.ACTIVE:
        raise ServiceError("Only an active order can be suspended.", code="invalid_status")
    _cascade(actor, order, (HostingStatus.ACTIVE,),
             lambda a: hosting_services.suspend_account(SYSTEM, a, reason=reason or "Order suspended"), "suspend")
    with transaction.atomic():
        return lifecycle.transition(_lock(order), OrderStatus.SUSPENDED, actor=actor, action="order.suspended",
                                    reason=reason, request=request)


def unsuspend_order(actor, order, *, request=None):
    """Reactivate a suspended order and its suspended hosting accounts."""
    _require_manage(actor)
    order = Order.objects.get(pk=order.pk)
    if order.status != OrderStatus.SUSPENDED:
        raise ServiceError("Only a suspended order can be reactivated.", code="invalid_status")
    _cascade(actor, order, (HostingStatus.SUSPENDED,), lambda a: hosting_services.unsuspend_account(SYSTEM, a),
             "reactivate")
    with transaction.atomic():
        return lifecycle.transition(_lock(order), OrderStatus.ACTIVE, actor=actor, action="order.unsuspended",
                                    request=request)


def terminate_order(actor, order, *, reason="", request=None):
    """Terminate an order and its hosting accounts. Final. (Domains are registrations and are left as they are.)"""
    _require_manage(actor)
    order = Order.objects.get(pk=order.pk)
    if order.status not in (OrderStatus.ACTIVE, OrderStatus.SUSPENDED):
        raise ServiceError("Only an active or suspended order can be terminated.", code="invalid_status")
    _cascade(actor, order, (HostingStatus.ACTIVE, HostingStatus.SUSPENDED),
             lambda a: hosting_services.terminate_account(SYSTEM, a), "terminate")
    with transaction.atomic():
        return lifecycle.transition(_lock(order), OrderStatus.TERMINATED, actor=actor, action="order.terminated",
                                    reason=reason, request=request)
