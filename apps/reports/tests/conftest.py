"""Fixtures for report tests: a small business with dated orders, payments, tickets and services."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission
from django.utils import timezone

from apps.accounts.roles import Role
from apps.billing import invoicing, payments
from apps.billing import services as billing
from apps.clients import services as client_services
from apps.orders.models import Order, OrderStatus
from apps.renewals.tests.conftest import (account, client_obj, domain, manager, owner, pro, registrar,  # noqa: F401
                                          server, starter)

D = Decimal


@pytest.fixture
def admin(staff):
    return staff(Role.ADMIN)


@pytest.fixture
def agent(staff):
    return staff(Role.SUPPORT_AGENT)


@pytest.fixture
def bank(manager):
    return billing.save_payment_method(manager, "bank-transfer", name="Bank transfer")


def ago(days=0, hours=0):
    return timezone.now() - timedelta(days=days, hours=hours)


def new_client(manager, name, email, *, days_ago=0):
    client = client_services.create_client(manager, {"first_name": name, "email": email, "country": "US"})
    type(client).objects.filter(pk=client.pk).update(created_at=ago(days_ago))
    return client


def make_order(client, *, days_ago=0, total="100.00", status=OrderStatus.ACTIVE, currency="USD"):
    order = Order.objects.create(client=client, currency=currency, subtotal=D(total), total=D(total), status=status,
                                 billing_name=client.display_name, billing_email=client.email)
    Order.objects.filter(pk=order.pk).update(created_at=ago(days_ago))
    return order


def invoice_for(manager, client, price="100.00"):
    return invoicing.issue_invoice(manager, invoicing.create_invoice(
        manager, client, lines=[{"description": "Hosting", "quantity": 1, "unit_price": price}]))


def pay_on(manager, invoice, days_ago, amount=None, *, method=None, reference=""):
    invoice.refresh_from_db()
    return payments.record_payment(manager, invoice, amount=str(amount if amount is not None else invoice.total),
                                   method=method, reference=reference, occurred_at=ago(days_ago))


@pytest.fixture
def support_reporter(make_user):
    """Someone allowed to view reports and support tickets, and nothing else: only support reports are open to them."""
    user = make_user("reporter@example.com", role=Role.CUSTOMER)
    user.user_permissions.add(*Permission.objects.filter(codename__in=("view_reports", "view_support")))
    return type(user).objects.get(pk=user.pk)  # a fresh object, so cached permissions are not reused
