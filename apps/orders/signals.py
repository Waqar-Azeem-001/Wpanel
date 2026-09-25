"""
Order reactions to billing events, plus the hooks later phases attach to.

``order_paid`` is sent when an order's invoice has been paid in full; Phase 09
(fulfilment/provisioning) subscribes to it instead of billing calling into
provisioning.
"""
from django.dispatch import Signal, receiver

from apps.billing.signals import invoice_cancelled, invoice_paid

from . import lifecycle
from .models import Order, OrderStatus

order_paid = Signal()


@receiver(invoice_paid)
def mark_order_paid(sender, invoice, actor=None, **kwargs):
    if not invoice.order_id:
        return
    order = Order.objects.select_for_update().get(pk=invoice.order_id)
    if order.status != OrderStatus.PENDING_PAYMENT:
        return  # already paid, cancelled, or held as fraud: payment alone does not release it
    order = lifecycle.transition(order, OrderStatus.PAID, actor=actor, action="order.paid", invoice=invoice.reference)
    order_paid.send(sender=Order, order=order, invoice=invoice, actor=actor)


@receiver(order_paid)
def fulfil_when_paid(sender, order, **kwargs):
    from . import fulfilment

    fulfilment.schedule(order.pk)


@receiver(invoice_cancelled)
def cancel_unpaid_order(sender, invoice, actor=None, reason="", **kwargs):
    if not invoice.order_id:
        return
    order = Order.objects.select_for_update().get(pk=invoice.order_id)
    if order.status != OrderStatus.PENDING_PAYMENT:
        return
    lifecycle.transition(order, OrderStatus.CANCELLED, actor=actor, action="order.cancelled",
                         reason=reason or "Invoice cancelled.", via="invoice")
