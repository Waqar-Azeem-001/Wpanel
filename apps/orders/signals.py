"""
Order reactions to billing events, plus the hooks later phases attach to.

``order_paid`` is sent when an order's invoice has been paid in full; Phase 09
(fulfilment/provisioning) subscribes to it instead of billing calling into
provisioning.
"""
from django.dispatch import Signal, receiver

from apps.audit import services as audit
from apps.billing.signals import invoice_cancelled, invoice_paid

from .models import Order, OrderStatus

order_paid = Signal()


@receiver(invoice_paid)
def mark_order_paid(sender, invoice, actor=None, **kwargs):
    if not invoice.order_id:
        return
    order = Order.objects.select_for_update().get(pk=invoice.order_id)
    if order.status != OrderStatus.PENDING_PAYMENT:
        return
    order.status = OrderStatus.PAID
    order.save(update_fields=["status", "updated_at"])
    audit.record("order.paid", actor=actor, target=order, metadata={"invoice": invoice.reference})
    order_paid.send(sender=Order, order=order, invoice=invoice, actor=actor)


@receiver(invoice_cancelled)
def cancel_unpaid_order(sender, invoice, actor=None, reason="", **kwargs):
    if not invoice.order_id:
        return
    order = Order.objects.select_for_update().get(pk=invoice.order_id)
    if order.status != OrderStatus.PENDING_PAYMENT:
        return
    order.status, order.cancel_reason = OrderStatus.CANCELLED, (reason or "Invoice cancelled.")[:500]
    order.save(update_fields=["status", "cancel_reason", "updated_at"])
    audit.record("order.cancelled", actor=actor, target=order, metadata={"reason": order.cancel_reason,
                                                                        "via": "invoice"})
