"""The domain price table on /domains/ and /pricing/ is a cached fragment that a price change replaces at once."""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts.roles import Role
from apps.domains import services
from apps.domains.models import RegistrarProvider, TldPricing

pytestmark = pytest.mark.django_db


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture(autouse=True)
def a_registrar():
    return RegistrarProvider.objects.create(name="Test", kind="manual", is_active=True)


def table(client, path):
    return client.get(path).content.decode()


@pytest.mark.parametrize("path", ["/domains/", "/pricing/"])
def test_a_price_change_shows_at_once_and_an_unsaved_one_does_not_(client, manager, path):
    services.set_tld_pricing(manager, ".com", register_price="12.00", renew_price="14.00", transfer_price="9.00")
    first = table(client, path)
    assert ".com" in first and "12.00" in first
    TldPricing.objects.filter(tld=".com").update(register_price="99.00")  # bypasses the save signal: the fragment is still the remembered one
    still = table(client, path)
    assert 'text-end">USD 12.00' in still and 'text-end">USD 99.00' not in still  # the table cells; the popular strip above it on /domains/ is not part of the fragment
    services.set_tld_pricing(manager, ".com", register_price="15.00", renew_price="14.00", transfer_price="9.00")  # the real staff action
    after = table(client, path)
    assert 'text-end">USD 15.00' in after and 'text-end">USD 12.00' not in after


@pytest.mark.parametrize("path", ["/domains/", "/pricing/"])
def test_a_new_extension_and_a_hidden_one_show_at_once(client, manager, path):
    services.set_tld_pricing(manager, ".com", register_price="12.00", renew_price="14.00", transfer_price="9.00")
    table(client, path)
    services.set_tld_pricing(manager, ".net", register_price="11.00", renew_price="13.00", transfer_price="8.00")
    assert ".net" in table(client, path)
    services.set_tld_pricing_active(manager, TldPricing.objects.get(tld=".net"), False)
    assert ".net" not in table(client, path)


def test_the_second_view_is_served_from_the_fragment(client, manager):
    for n, tld in enumerate((".com", ".net", ".org", ".pk", ".info", ".biz", ".xyz", ".store", ".shop", ".site", ".online", ".tech"), start=1):
        services.set_tld_pricing(manager, tld, register_price=f"{n}.00", renew_price="14.00", transfer_price="9.00")
    table(client, "/pricing/")
    with CaptureQueriesContext(connection) as warm:
        table(client, "/pricing/")
    assert len(warm) <= 3  # the extensions are not looked at row by row again
