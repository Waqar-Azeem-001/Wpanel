"""Shared fixtures: a small catalogue plus the billing configuration checkout needs."""
from decimal import Decimal

import pytest

from apps.accounts.roles import Role
from apps.billing import services as billing
from apps.clients import services as client_services
from apps.domains import services as domain_services
from apps.domains.models import RegistrarProvider
from apps.products import services as product_services
from apps.products.models import BillingCycle, ProductType


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def product(manager):
    """Starter hosting: monthly 10, annual 100 (+10 setup), a configured 3-month term at 28."""
    p = product_services.create_product(
        manager, {"name": "Starter", "type": ProductType.SHARED_HOSTING, "whm_package_name": "starter_pkg"})
    product_services.set_price(manager, p, billing_cycle=BillingCycle.MONTHLY, price="10.00")
    product_services.set_price(manager, p, billing_cycle=BillingCycle.ANNUAL, price="100.00", setup_fee="10.00")
    product_services.set_price(manager, p, billing_cycle=BillingCycle.CUSTOM, custom_months=3, price="28.00")
    product_services.set_product_status(manager, p, "active")
    return p


@pytest.fixture
def addon(manager):
    """Backups: 20 annually, 2 monthly, or a 5 one-time fee."""
    a = product_services.create_addon(manager, {"name": "Backups"})
    product_services.set_price(manager, a, billing_cycle=BillingCycle.ANNUAL, price="20.00")
    product_services.set_price(manager, a, billing_cycle=BillingCycle.MONTHLY, price="2.00")
    product_services.set_price(manager, a, billing_cycle=BillingCycle.ONE_TIME, price="5.00")
    product_services.set_addon_status(manager, a, "active")
    return a


@pytest.fixture
def registrar():
    return RegistrarProvider.objects.create(name="Test", kind="manual", is_active=True)


@pytest.fixture
def com(manager, registrar):
    return domain_services.set_tld_pricing(manager, ".com", register_price="12.00", renew_price="14.00",
                                           transfer_price="9.00")


@pytest.fixture
def bank(manager):
    return billing.save_payment_method(manager, "bank-transfer", name="Bank transfer",
                                       instructions="Pay to IBAN PK00 TEST 0001.")


@pytest.fixture
def client_obj(manager):
    return client_services.create_client(manager, {"first_name": "Ada", "last_name": "Lovelace",
                                                   "email": "ada@acme.test", "company_name": "Acme Ltd",
                                                   "country": "US"})


@pytest.fixture
def owner(client_obj):
    return client_obj.contacts.get().user


@pytest.fixture
def coupon(manager):
    return billing.save_coupon(manager, "SAVE10", {"discount_type": "percent", "value": Decimal("10")})


@pytest.fixture
def shop(product, addon, com, bank):
    """Everything needed to place an order."""
    return {"product": product, "addon": addon, "com": com, "bank": bank}
