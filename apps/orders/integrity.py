"""Consistency checks for the order lifecycle, run by ``manage.py verify_billing``."""
from apps.billing.models import InvoiceStatus

from .models import FulfilmentStatus, Order, OrderStatus

PAID_STATES = (OrderStatus.PAID, OrderStatus.PROCESSING, OrderStatus.PROVISIONING, OrderStatus.ACTIVE,
               OrderStatus.SUSPENDED, OrderStatus.TERMINATED)


def verify_order(order):
    problems = []
    label = f"Order {order.reference}"
    invoices = list(order.invoices.all())
    settled = any(i.status in (InvoiceStatus.PAID, InvoiceStatus.REFUNDED) for i in invoices)
    if order.status in PAID_STATES and invoices and not settled:
        problems.append(f"{label}: is {order.status} but its invoice has not been paid.")
    if order.status == OrderStatus.PENDING_PAYMENT and settled:
        problems.append(f"{label}: its invoice is paid but the order is still awaiting payment.")
    if order.status in (OrderStatus.ACTIVE, OrderStatus.SUSPENDED):
        undone = order.items.exclude(fulfilment_status=FulfilmentStatus.DONE).count()
        if undone:
            problems.append(f"{label}: is {order.status} but {undone} line(s) were never fulfilled.")
    for item in order.items.select_related("hosting_account", "domain"):
        if item.fulfilment_status == FulfilmentStatus.DONE and item.kind in ("hosting", "domain_register",
                                                                              "domain_transfer"):
            if not (item.hosting_account_id or item.domain_id):
                problems.append(f"{label}: '{item.description}' is done but has no service attached.")
        for service in (item.hosting_account, item.domain):
            if service is not None and service.client_id != order.client_id:
                problems.append(f"{label}: '{item.description}' points at another client's service.")
    return problems


def verify_all():
    problems = []
    for order in Order.objects.iterator():
        problems += verify_order(order)
    return problems
