"""Fixtures for cancellation and lifecycle tests: reuses the renewals world (a client with paid hosting and a domain)."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.roles import Role
from apps.billing import payments, services as billing
from apps.clients import services as client_services
from apps.clients.models import ContactRole
from apps.renewals.tests.conftest import (account, basic, client_obj, domain, manager, owner, pro, pay,  # noqa: F401
                                          registrar, server, starter, tax)

D = Decimal


@pytest.fixture
def admin(staff):
    return staff(Role.ADMIN)


@pytest.fixture
def agent(staff):
    return staff(Role.SUPPORT_AGENT)


@pytest.fixture
def billing_contact(manager, client_obj):
    """A second person on the account who is not its owner."""
    contact = client_services.add_contact(manager, client_obj, email="finance@acme.test", role=ContactRole.BILLING)
    return contact.user


@pytest.fixture
def bank(manager):
    return billing.save_payment_method(manager, "bank-transfer", name="Bank transfer")


@pytest.fixture
def stranger(make_user):
    """Someone with their own client, unrelated to ``client_obj``."""
    from apps.clients import services

    other = services.create_client(make_user("root@example.com", role=Role.MANAGER),
                                   {"first_name": "Eve", "email": "eve@other.test", "company_name": "Other Ltd",
                                    "country": "US"})
    return other.contacts.get().user


def expire(service, days_ago):
    """Make a service's paid period have ended ``days_ago`` days ago (negative = ends in that many days)."""
    type(service).objects.filter(pk=service.pk).update(expires_at=timezone.now() - timedelta(days=days_ago))
    service.refresh_from_db()
    return service


def paid_account_payment(manager, account):
    """The account's renewal invoice, paid - so there is a real payment to refund."""
    from apps.renewals import services as renewals

    change = renewals.create_hosting_renewal(manager, account)
    payments.record_payment(manager, change.invoice, amount=str(change.invoice.total), reference="TXN-1")
    account.refresh_from_db()
    return change.invoice.transactions.filter(type="payment").first()
