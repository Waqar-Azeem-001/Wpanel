"""Fixtures for affiliate tests: an active affiliate, referred customers, and a way to have them pay."""
from decimal import Decimal

import pytest

from apps.accounts.roles import Role
from apps.accounts.services import register_user
from apps.affiliates import services
from apps.affiliates.models import AffiliateSettings
from apps.billing import invoicing, payments
from apps.billing import services as billing
from apps.clients.models import Client

D = Decimal
PASSWORD = "Str0ng-Passw0rd!x"


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def admin(staff):
    return staff(Role.ADMIN)


@pytest.fixture
def agent(staff):
    return staff(Role.SUPPORT_AGENT)


@pytest.fixture
def tax(manager):
    """10% sales tax for every client (the default rule), so a 100.00 sale is invoiced as 110.00."""
    return billing.save_tax_rule(manager, "", name="Sales tax", rate=D("10"))


@pytest.fixture
def config():
    """Programme settings with approval switched off, so a new affiliate is active at once (other defaults kept)."""
    row = AffiliateSettings.load()
    row.require_approval = False
    row.save()
    return row


@pytest.fixture
def make_customer(db):
    """A signed-up customer (with their own client), optionally arriving through an affiliate code."""
    def make(email, code=""):
        user = register_user(email=email, password=PASSWORD, first_name=email.split("@")[0], referral_code=code)
        return user, Client.objects.get(email=user.email)

    return make


@pytest.fixture
def affiliate_user(make_customer):
    return make_customer("aff@example.com")[0]


@pytest.fixture
def affiliate(config, affiliate_user):
    return services.enrol(affiliate_user, accept_terms=True)


@pytest.fixture
def referred(affiliate, make_customer):
    """A customer who signed up through the affiliate's link: (user, client)."""
    return make_customer("buyer@example.com", code=affiliate.code)


def pay_invoice(manager, client, price="100.00", *, discount=None, amount=None):
    """Issue an invoice for ``client`` and pay it (in full unless ``amount``); returns the invoice."""
    kwargs = {"discount_type": "fixed", "discount_value": discount} if discount else {}
    invoice = invoicing.issue_invoice(manager, invoicing.create_invoice(
        manager, client, lines=[{"description": "Hosting", "quantity": 1, "unit_price": price}], **kwargs))
    payments.record_payment(manager, invoice, amount=str(amount if amount is not None else invoice.total))
    invoice.refresh_from_db()
    return invoice
