from celery import shared_task

from . import fulfilment


@shared_task
def fulfil_order_task(order_id):
    """Fulfil one paid order (see ``fulfilment.fulfil_order``). Idempotent."""
    return fulfilment.fulfil_order(order_id).status


@shared_task
def sweep_stuck_orders_task():
    """Every few minutes: pick up paid orders that were never fulfilled and reset ones stuck mid-way."""
    return fulfilment.sweep()
