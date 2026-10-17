"""Buying an SSL certificate or WHOIS privacy for something already owned: an invoice at once, set up by a person once paid."""
import pytest
from django.urls import reverse

from apps.core.exceptions import ServiceError
from apps.products import services as product_services
from apps.products.models import AddonKind, BillingCycle
from apps.renewals import addon_orders
from apps.renewals.models import AddonOrder, AddonOrderStatus
from apps.renewals.tests.conftest import pay

pytestmark = pytest.mark.django_db


def extra(manager, name, kind, price="12.00"):
    addon = product_services.create_addon(manager, {"name": name, "kind": kind})
    product_services.set_addon_status(manager, addon, "active")
    product_services.set_price(manager, addon, billing_cycle=BillingCycle.ANNUAL, price=price)
    return addon


def test_buying_ssl_for_a_plan_makes_an_invoice_at_the_catalogue_price(owner, account, manager):
    addon = extra(manager, "Positive SSL", AddonKind.SSL, "3499.00")
    order = addon_orders.buy_addon(owner, addon, hosting_account=account)
    assert order.status == AddonOrderStatus.PENDING and order.invoice.total == order.price
    assert str(order.price) == "3499.00" and "example.com" in order.invoice.items.get().description


def test_asking_twice_gives_the_same_order_not_a_second_invoice(owner, account, manager):
    addon = extra(manager, "Positive SSL", AddonKind.SSL)
    first = addon_orders.buy_addon(owner, addon, hosting_account=account)
    assert addon_orders.buy_addon(owner, addon, hosting_account=account).pk == first.pk
    assert AddonOrder.objects.count() == 1


def test_paying_marks_it_paid_alerts_the_team_and_marking_done_tells_the_customer(owner, account, manager):
    from apps.notifications.models import Notification

    order = addon_orders.buy_addon(owner, extra(manager, "Positive SSL", AddonKind.SSL), hosting_account=account)
    pay(manager, order.invoice)
    order.refresh_from_db()
    assert order.status == AddonOrderStatus.PAID and order.paid_at
    assert Notification.objects.filter(user=manager, title__contains="set it up").exists()
    with pytest.raises(ServiceError):
        addon_orders.mark_done(owner, order)  # a customer cannot mark it done
    addon_orders.mark_done(manager, order)
    order.refresh_from_db()
    assert order.status == AddonOrderStatus.DONE
    with pytest.raises(ServiceError):
        addon_orders.buy_addon(owner, order.addon, hosting_account=account)  # already active here


def test_whois_privacy_is_for_a_domain_and_ssl_is_for_a_plan(owner, account, domain, manager):
    whois = extra(manager, "WHOIS Privacy", AddonKind.WHOIS_PRIVACY, "1499.00")
    ssl = extra(manager, "Rapid SSL", AddonKind.SSL)
    with pytest.raises(ServiceError):
        addon_orders.buy_addon(owner, whois, hosting_account=account)
    with pytest.raises(ServiceError):
        addon_orders.buy_addon(owner, ssl, domain=domain)
    assert addon_orders.buy_addon(owner, whois, domain=domain).domain == domain


def test_a_stranger_cannot_buy_for_someone_elses_service(customer, account, manager):
    with pytest.raises(ServiceError):
        addon_orders.buy_addon(customer, extra(manager, "Rapid SSL", AddonKind.SSL), hosting_account=account)


def test_an_unpriced_or_hidden_extra_cannot_be_bought(owner, account, manager):
    addon = product_services.create_addon(manager, {"name": "Free thing", "kind": AddonKind.SSL})
    with pytest.raises(ServiceError):
        addon_orders.buy_addon(owner, addon, hosting_account=account)  # hidden
    product_services.set_addon_status(manager, addon, "active")
    with pytest.raises(ServiceError):
        addon_orders.buy_addon(owner, addon, hosting_account=account)  # no price


def test_cancelling_the_invoice_voids_the_order(owner, account, manager):
    from apps.billing import invoicing

    order = addon_orders.buy_addon(owner, extra(manager, "Rapid SSL", AddonKind.SSL), hosting_account=account)
    invoicing.cancel_invoice(manager, order.invoice, reason="test")
    order.refresh_from_db()
    assert order.status == AddonOrderStatus.VOID


def test_the_buy_button_opens_the_invoice_and_refuses_an_off_site_return_address(client, owner, account, manager):
    addon = extra(manager, "Rapid SSL", AddonKind.SSL)
    client.force_login(owner)
    response = client.post(reverse("renewals_customer:addon_buy", args=[addon.pk]), {"hosting": account.pk, "next": "https://elsewhere.test/"})
    order = AddonOrder.objects.get()
    assert response.status_code == 302 and response["Location"] == reverse("billing_customer:invoice_detail", args=[order.invoice_id])
    bad = client.post(reverse("renewals_customer:addon_buy", args=[addon.pk]), {"hosting": 999999, "next": "https://elsewhere.test/"})
    assert bad.status_code == 404 or bad["Location"] == "/"


def test_staff_see_the_orders_and_mark_one_done(client, owner, account, manager):
    order = addon_orders.buy_addon(owner, extra(manager, "Rapid SSL", AddonKind.SSL), hosting_account=account)
    pay(manager, order.invoice)
    client.force_login(manager)
    page = client.get(reverse("renewals_staff:addon_orders")).content.decode()
    assert "Rapid SSL" in page and "Mark done" in page
    client.post(reverse("renewals_staff:addon_done", args=[order.pk]))
    order.refresh_from_db()
    assert order.status == AddonOrderStatus.DONE
    assert client.get(reverse("renewals_staff:addon_orders"), {"status": "done"}).status_code == 200
