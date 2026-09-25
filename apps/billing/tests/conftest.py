"""Fixtures for invoicing and payments: a client, a payment gateway and the methods that use it."""
import json
import time
from decimal import Decimal

import pytest

from apps.accounts.roles import Role
from apps.billing import gateways, services
from apps.billing.models import PaymentProvider, TaxRule
from apps.clients import services as client_services

D = Decimal
WEBHOOK_SECRET = "whsec_test_secret_value"


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def client_obj(manager):
    return client_services.create_client(manager, {"first_name": "Ada", "last_name": "Lovelace",
                                                   "email": "ada@acme.test", "company_name": "Acme Ltd",
                                                   "country": "US"})


@pytest.fixture
def owner(client_obj):
    return client_obj.contacts.get().user


@pytest.fixture
def vat(manager):
    return services.save_tax_rule(manager, "US", name="Sales tax", rate=D("10"))


@pytest.fixture
def bank(manager):
    return services.save_payment_method(manager, "bank-transfer", name="Bank transfer", instructions="IBAN PK00")


@pytest.fixture
def provider():
    p = PaymentProvider(name="Test gateway", kind="test", is_active=True)
    p.set_webhook_secret(WEBHOOK_SECRET)
    p.save()
    return p


@pytest.fixture
def card(manager, provider):
    method = services.save_payment_method(manager, "card", name="Credit card")
    method.provider = provider
    method.save()
    return method


def lines(*specs):
    """lines(("Hosting", 1, "100.00"), ...) -> the service's line dicts."""
    return [{"description": d, "quantity": q, "unit_price": p} for d, q, p in specs]


def signed_event(provider, event_id, event_type, external_id, amount, currency="USD", timestamp=None):
    """(body, headers) exactly as the test gateway would deliver them."""
    body = json.dumps({"id": event_id, "type": event_type,
                       "data": {"payment_id": external_id, "amount": str(amount), "currency": currency}}).encode()
    signature = gateways.get_adapter(provider).sign(body, timestamp if timestamp is not None else int(time.time()))
    return body, {gateways.SIGNATURE_HEADER: signature}
