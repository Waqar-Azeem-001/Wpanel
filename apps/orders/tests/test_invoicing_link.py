"""How orders and invoices work together: checkout issues the invoice; payment and cancellation flow both ways."""
from decimal import Decimal

import pytest
from django.core import mail

from apps.audit.models import AuditEvent
from apps.billing import integrity, invoicing, payments
from apps.billing import services as billing
from apps.billing.models import Invoice, InvoiceStatus
from apps.clients import services as client_services
from apps.core.exceptions import ServiceError
from apps.orders import services
from apps.orders.models import Order, OrderStatus
from apps.orders.signals import order_paid

pytestmark = pytest.mark.django_db

D = Decimal


def place(owner, client_obj, shop, *, coupon=None):
    host = services.add_hosting(owner, client_obj, shop["product"], "example.com", "annual")  # 100.00 + 10.00 setup
    services.add_addon(owner, host, shop["addon"])                                              # 20.00
    services.add_domain_registration(owner, client_obj, "example.com", 2)                       # 24.00
    cart = services.get_open_cart(owner, client_obj)
    if coupon:
        services.apply_coupon(owner, cart, coupon)
    return services.checkout(owner, cart, payment_method_code="bank-transfer")


def test_checkout_issues_an_invoice_equal_to_the_order(manager, owner, client_obj, shop, coupon):
    billing.save_tax_rule(manager, "US", name="Sales tax", rate=D("7.5"))
    mail.outbox.clear()
    order = place(owner, client_obj, shop, coupon="SAVE10")
    invoice = order.invoices.get()
    assert invoice.status == InvoiceStatus.UNPAID and invoice.number == "INV-000001"
    assert (invoice.subtotal, invoice.discount_total, invoice.tax_total, invoice.total) == (
        order.subtotal, order.discount_total, order.tax_total, order.total)
    assert invoice.currency == order.currency and invoice.billing_company == "Acme Ltd"
    descriptions = [i.description for i in invoice.items.all()]
    assert any(d.endswith("- setup fee") for d in descriptions) and len(descriptions) == 4
    assert integrity.verify_all() == []
    assert len(mail.outbox) == 1 and invoice.number in mail.outbox[0].body  # still one email, now naming the invoice


def test_paying_the_invoice_marks_the_order_paid_and_announces_it(manager, owner, client_obj, shop):
    received = []
    handler = lambda sender, order, invoice, actor, **kw: received.append((order.pk, invoice.pk))  # noqa: E731
    order_paid.connect(handler, weak=False, dispatch_uid="t")
    try:
        order = place(owner, client_obj, shop)
        invoice = order.invoices.get()
        payments.record_payment(manager, invoice, amount=str(invoice.total))
    finally:
        order_paid.disconnect(dispatch_uid="t")
    order.refresh_from_db()
    assert order.status == OrderStatus.PAID and received == [(order.pk, invoice.pk)]
    assert AuditEvent.objects.filter(action="order.paid", target_id=str(order.pk)).exists()


def test_a_part_payment_leaves_the_order_awaiting_payment(manager, owner, client_obj, shop):
    order = place(owner, client_obj, shop)
    payments.record_payment(manager, order.invoices.get(), amount="10.00")
    order.refresh_from_db()
    assert order.status == OrderStatus.PENDING_PAYMENT


def test_cancelling_the_order_cancels_its_invoice(owner, client_obj, shop):
    order = place(owner, client_obj, shop)
    services.cancel_order(owner, order, reason="Changed my mind")
    invoice = order.invoices.get()
    assert invoice.status == InvoiceStatus.CANCELLED and invoice.cancel_reason == "Changed my mind"
    order.refresh_from_db()
    assert order.status == OrderStatus.CANCELLED


def test_cancelling_the_invoice_cancels_the_order_and_frees_the_domain(manager, owner, client_obj, shop):
    order = place(owner, client_obj, shop)
    invoicing.cancel_invoice(manager, order.invoices.get(), reason="Duplicate")
    order.refresh_from_db()
    assert order.status == OrderStatus.CANCELLED and order.cancel_reason == "Duplicate"
    place(owner, client_obj, shop)  # the reserved domain is free again, so the same cart can be ordered anew


def test_an_order_with_a_part_payment_cannot_be_cancelled(manager, owner, client_obj, shop):
    order = place(owner, client_obj, shop)
    payments.record_payment(manager, order.invoices.get(), amount="10.00")
    with pytest.raises(ServiceError) as exc:
        services.cancel_order(owner, order)
    assert exc.value.code == "invoice_not_cancellable"
    order.refresh_from_db()
    assert order.status == OrderStatus.PENDING_PAYMENT  # nothing was half-applied


def test_a_free_order_is_settled_at_checkout(manager, owner, client_obj, shop):
    billing.save_coupon(manager, "FREE", {"discount_type": "percent", "value": D("100")})
    cart_item = services.add_domain_registration(owner, client_obj, "free-example.com", 1)
    services.apply_coupon(owner, services.get_open_cart(owner, client_obj), "FREE")
    order = services.checkout(owner, services.get_open_cart(owner, client_obj), payment_method_code="bank-transfer")
    invoice = order.invoices.get()
    assert cart_item and order.total == 0
    assert invoice.status == InvoiceStatus.PAID and order.status == OrderStatus.PAID


def test_a_tax_exempt_client_is_not_taxed_on_orders(manager, owner, client_obj, shop):
    billing.save_tax_rule(manager, "US", name="Sales tax", rate=D("10"))
    client_services.update_client(manager, client_obj, {"tax_exempt": True})
    client_obj.refresh_from_db()
    order = place(owner, client_obj, shop)
    assert order.tax_total == 0 and order.tax_name == "" and order.invoices.get().tax_total == 0


def test_the_invoice_keeps_the_billing_details_of_the_order(manager, owner, client_obj, shop):
    order = place(owner, client_obj, shop)
    client_services.update_client(manager, client_obj, {"company_name": "Renamed"})
    assert order.invoices.get().billing_company == "Acme Ltd"


def test_each_order_gets_exactly_one_invoice_with_the_next_number(owner, client_obj, shop, manager):
    first = place(owner, client_obj, shop)
    services.cancel_order(owner, first)
    second = place(owner, client_obj, shop)
    assert Invoice.objects.count() == 2
    assert second.invoices.get().number == "INV-000002"
    assert integrity.verify_all() == []


def test_the_order_is_not_created_when_the_invoice_cannot_be_built(monkeypatch, owner, client_obj, shop):
    def boom(order, **kwargs):
        raise ServiceError("no invoice", code="order_not_invoiceable")

    monkeypatch.setattr("apps.orders.services.create_invoice_for_order", boom)
    services.add_domain_registration(owner, client_obj, "solo.com", 1)
    with pytest.raises(ServiceError):
        services.checkout(owner, services.get_open_cart(owner, client_obj), payment_method_code="bank-transfer")
    assert not Order.objects.exists()  # the checkout transaction rolled back as one unit
