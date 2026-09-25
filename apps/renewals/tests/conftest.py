"""A client with an active, paid-for hosting account and domain to renew and upgrade."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.roles import Role
from apps.billing import services as billing
from apps.clients import services as client_services
from apps.domains import services as domain_services
from apps.domains.models import RegistrarProvider
from apps.hosting import services as hosting_services
from apps.products import services as product_services
from apps.products.models import BillingCycle, ProductType
from apps.renewals import services

D = Decimal


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def server(manager):
    return product_services.create_server(manager, {"name": "srv1", "hostname": "srv1.example.com"})


def _plan(manager, server, name, package, monthly, annual):
    product = product_services.create_product(
        manager, {"name": name, "type": ProductType.SHARED_HOSTING, "whm_package_name": package})
    product_services.set_price(manager, product, billing_cycle=BillingCycle.MONTHLY, price=monthly)
    product_services.set_price(manager, product, billing_cycle=BillingCycle.ANNUAL, price=annual)
    product_services.set_product_servers(manager, product, [server.pk])
    product_services.set_product_status(manager, product, "active")
    return product


@pytest.fixture
def starter(manager, server):
    return _plan(manager, server, "Starter", "starter_pkg", "10.00", "100.00")


@pytest.fixture
def pro(manager, server):
    return _plan(manager, server, "Pro", "pro_pkg", "20.00", "200.00")


@pytest.fixture
def basic(manager, server):
    """Cheaper than Starter - moving to it is a downgrade."""
    return _plan(manager, server, "Basic", "basic_pkg", "5.00", "50.00")


@pytest.fixture
def client_obj(manager):
    return client_services.create_client(manager, {"first_name": "Ada", "last_name": "Lovelace",
                                                   "email": "ada@acme.test", "company_name": "Acme Ltd",
                                                   "country": "US"})


@pytest.fixture
def owner(client_obj):
    return client_obj.contacts.get().user


@pytest.fixture
def account(manager, client_obj, starter):
    """An active Starter account on an annual term paid 100.00, started 100 days ago (265 of 365 days left)."""
    account = hosting_services.request_hosting(manager, client_obj, starter, "example.com")
    hosting_services.complete_provisioning(manager, account)
    start = timezone.now() - timedelta(days=100)
    return services.set_hosting_term(manager, account, billing_cycle="annual", term_start=start,
                                     expires_at=start + timedelta(days=365), term_paid=D("100.00"))


@pytest.fixture
def registrar():
    return RegistrarProvider.objects.create(name="Test", kind="manual", is_active=True)


@pytest.fixture
def domain(manager, client_obj, registrar):
    domain_services.set_tld_pricing(manager, ".com", register_price="12.00", renew_price="14.00",
                                    transfer_price="9.00")
    domain = domain_services.request_registration(manager, client_obj, "example.com", 1)
    return domain_services.complete_registration(manager, domain)


@pytest.fixture
def tax(manager):
    return billing.save_tax_rule(manager, "US", name="Sales tax", rate=D("10"))


def pay(manager, invoice, amount=None):
    """Settle an invoice in full (the payment path that fires the paid signal)."""
    from apps.billing import payments

    invoice.refresh_from_db()
    return payments.record_payment(manager, invoice, amount=str(amount if amount is not None else invoice.total))
