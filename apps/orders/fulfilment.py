"""
Turning a paid order into services (roadmap Phase 09).

    Paid -> Processing -> Provisioning -> Active        (or Failed, which staff can retry)

Runs in the background (a Celery task started once the payment has committed), as the system actor, using the
same domain/hosting service functions staff use - no second provisioning code path. It is **idempotent and
resumable**: every order line records what it became (``hosting_account`` / ``domain``) and whether it is
done, so a retry after a failure only does the work that is still outstanding and can never create a second
hosting account or domain for a line.

The lifecycle claim (Paid/Failed -> Processing) is made under the order's row lock, so two workers can never
fulfil the same order at once; the slow calls (WHM, registrar) then run outside any lock.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.audit import services as audit
from apps.billing.models import InvoiceItem
from apps.core.exceptions import ServiceError
from apps.core.system import SYSTEM
from apps.domains import services as domain_services
from apps.hosting import services as hosting_services
from apps.notifications import services as notifications
from apps.renewals import services as renewal_services

from . import lifecycle
from .models import FulfilmentStatus, ItemKind, Order, OrderItem, OrderStatus

logger = logging.getLogger(__name__)

STUCK_PAID_AFTER = timedelta(minutes=5)
STUCK_IN_FLIGHT_AFTER = timedelta(minutes=30)


class _Waiting(Exception):
    """An item that cannot be done yet because something it depends on has not been (not a failure)."""


def schedule(order_id):
    """Queue fulfilment once the surrounding transaction commits. A broker outage must never undo a payment."""
    if not settings.ORDER_AUTO_FULFIL:
        return

    def enqueue():
        from .tasks import fulfil_order_task

        try:
            fulfil_order_task.delay(order_id)
        except Exception:  # noqa: BLE001 - the sweeper picks up a paid order that was never queued
            logger.exception("Could not queue fulfilment of order %s", order_id)

    transaction.on_commit(enqueue)


def _paid_value(item):
    """What the client paid for an order line's plan, excluding tax and setup fees (the term's valid paid value)."""
    lines = list(InvoiceItem.objects.filter(order_item=item).order_by("id"))
    return (lines[0].amount - lines[0].discount_amount) if lines else item.unit_price


def _fulfil_hosting(order, item, actor):
    account = item.hosting_account
    if account is None:
        with transaction.atomic():
            account = hosting_services.request_hosting(actor, order.client, item.product, item.domain_name)
            item.hosting_account = account
            item.save(update_fields=["hosting_account", "updated_at"])
    hosting_services.complete_provisioning(actor, account)  # not atomic: a failure must persist the FAILED account
    with transaction.atomic():
        renewal_services.start_hosting_term(account, billing_cycle=item.billing_cycle,
                                            custom_months=item.custom_months, term_paid=_paid_value(item))


def _fulfil_addon(order, item, actor):
    # Read the plan's outcome from the database: the copy loaded with the item predates this run.
    parent_done = item.parent_id and OrderItem.objects.filter(
        pk=item.parent_id, fulfilment_status=FulfilmentStatus.DONE).exists()
    if not parent_done:
        raise _Waiting()
    # Add-ons have no provisioning step of their own yet; they are part of what was sold with the plan and
    # recorded on the order. (Known Work: add-on provisioning.)


def _fulfil_domain_register(order, item, actor):
    domain = item.domain
    if domain is None:
        with transaction.atomic():
            domain = domain_services.request_registration(actor, order.client, item.domain_name, item.years)
            item.domain = domain
            item.save(update_fields=["domain", "updated_at"])
    domain_services.complete_registration(actor, domain)


def _fulfil_domain_transfer(order, item, actor):
    domain = item.domain
    if domain is None:
        with transaction.atomic():
            domain = domain_services.request_transfer_in(actor, order.client, item.domain_name,
                                                         item.get_auth_code(), item.years)
            item.domain = domain
            item.auth_code_encrypted = ""  # the domain holds it now; the order line no longer needs it
            item.save(update_fields=["domain", "auth_code_encrypted", "updated_at"])
    domain_services.complete_transfer(actor, domain)


HANDLERS = {
    ItemKind.HOSTING: _fulfil_hosting,
    ItemKind.ADDON: _fulfil_addon,
    ItemKind.DOMAIN_REGISTER: _fulfil_domain_register,
    ItemKind.DOMAIN_TRANSFER: _fulfil_domain_transfer,
}


def _fulfil_item(order, item, actor):
    """Returns an error message, or "" when the line is done (or still waiting on another line)."""
    try:
        HANDLERS[item.kind](order, item, actor)
    except _Waiting:
        return ""
    except ServiceError as exc:
        error = exc.message
    except Exception:  # noqa: BLE001 - one broken line must not stop the others
        logger.exception("Fulfilment of order item %s failed", item.pk)
        error = "An unexpected error occurred."
    else:
        OrderItem.objects.filter(pk=item.pk).update(fulfilment_status=FulfilmentStatus.DONE, fulfilment_error="",
                                                    updated_at=timezone.now())
        item.fulfilment_status = FulfilmentStatus.DONE
        return ""
    OrderItem.objects.filter(pk=item.pk).update(fulfilment_status=FulfilmentStatus.FAILED,
                                                fulfilment_error=error[:500], updated_at=timezone.now())
    audit.record("order.item_failed", target=item.order,
                 metadata={"item_id": item.pk, "description": item.description, "error": error})
    return f"{item.description}: {error}"


def fulfil_order(order_id, *, actor=SYSTEM):
    """
    Fulfil a paid (or previously failed) order. Safe to call repeatedly and concurrently: a call that finds
    the order in any other state does nothing. Returns the order as it ends up.
    """
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status not in (OrderStatus.PAID, OrderStatus.FAILED):
            return order
        order = lifecycle.transition(order, OrderStatus.PROCESSING, actor=actor, action="order.processing")
        order = lifecycle.transition(order, OrderStatus.PROVISIONING, actor=actor, action="order.provisioning")

    failures = []
    items = list(order.items.select_related("product", "parent").order_by("id"))
    for item in items:  # a plan is always ordered before its add-ons, so each add-on sees its plan's outcome
        if item.fulfilment_status == FulfilmentStatus.DONE:
            continue
        error = _fulfil_item(order, item, actor)
        if error:
            failures.append(error)

    # An order is only active when every line is done. A line still pending here (its plan failed) is not a
    # separate failure, but it must never be glossed over.
    if not failures and order.items.exclude(fulfilment_status=FulfilmentStatus.DONE).exists():
        failures.append("Some lines are still waiting to be fulfilled.")

    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status != OrderStatus.PROVISIONING:  # changed underneath us (e.g. reset by the sweeper)
            return order
        if failures:
            order = lifecycle.transition(order, OrderStatus.FAILED, actor=actor, action="order.failed",
                                         reason="; ".join(failures))
        else:
            order = lifecycle.transition(order, OrderStatus.ACTIVE, actor=actor, action="order.activated")
            _notify_active(order)
    return order


def _notify_active(order):
    link = reverse("orders_customer:detail", args=[order.pk])
    notifications.dispatch_client(
        "order.active", order.client, title=f"Order {order.reference} is active", body="Your services are ready.",
        link=link, context={"order": order, "link": link,
                            "items": list(order.items.select_related("hosting_account", "domain"))})


def sweep(now=None):
    """
    Safety net, run every few minutes: queue fulfilment for any order that was paid but never picked up, and
    fail (so staff can retry) any order stuck mid-fulfilment because its worker died.
    """
    now = now or timezone.now()
    result = {"queued": 0, "reset": 0}
    for order in Order.objects.filter(status=OrderStatus.PAID, updated_at__lt=now - STUCK_PAID_AFTER):
        fulfil_order(order.pk)
        result["queued"] += 1
    stuck = Order.objects.filter(status__in=lifecycle.IN_FLIGHT, updated_at__lt=now - STUCK_IN_FLIGHT_AFTER)
    for order in stuck:
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            if locked.status in lifecycle.IN_FLIGHT and locked.updated_at < now - STUCK_IN_FLIGHT_AFTER:
                if locked.status == OrderStatus.PROCESSING:
                    lifecycle.transition(locked, OrderStatus.PROVISIONING, action="order.provisioning")
                lifecycle.transition(locked, OrderStatus.FAILED, action="order.failed",
                                     reason="Fulfilment was interrupted. Retry it.")
                result["reset"] += 1
    return result
