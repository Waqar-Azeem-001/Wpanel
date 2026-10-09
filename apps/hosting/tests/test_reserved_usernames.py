"""WHM refuses reserved account names such as "testing6"; WHPanel must never ask for one (and fixes an older request that has one)."""
import pytest

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.hosting import services
from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.manual import ManualAdapter
from apps.hosting.models import HostingAccount, HostingStatus
from apps.products import services as product_services
from apps.products.models import ProductType

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("name", ["test", "testing6", "test123", "Testing", "root", "mysql", "cpanel", "cpanelx", "postgres", "admin", "mail", "nobody"])
def test_reserved_names_are_recognised(name):
    assert services.is_reserved_username(name)


@pytest.mark.parametrize("name", ["example", "mycoolsite", "waqarazeem", "contest", "attest", "protest1", "onboard", "webtesting6", "shop"])
def test_ordinary_names_are_not_reserved(name):
    assert not services.is_reserved_username(name)


@pytest.mark.parametrize("domain", ["testing6.com", "test.com", "root.org", "cpanel-shop.com", "mail.co", "example.com"])
def test_a_generated_username_is_never_reserved_whatever_the_domain(domain):
    for _ in range(200):
        assert not services.is_reserved_username(services._generate_username(domain))


def test_a_random_name_that_happens_to_be_reserved_is_skipped(monkeypatch):
    picks = iter(list("t") + list("esting1") + list("a") + list("bcdefgh"))
    monkeypatch.setattr("apps.hosting.services.secrets.choice", lambda seq: next(picks))
    assert services._generate_username("x.com") == "abcdefgh"


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def setup(manager):
    from apps.products.models import Product, Server, ServerKind

    client = client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test"})
    product = Product.objects.get(pk=product_services.create_product(manager, {"name": "Starter", "type": ProductType.SHARED_HOSTING,
                                                                                 "whm_package_name": "starter_pkg"}).pk)
    server = Server.objects.create(name="srv", hostname="srv.test", kind=ServerKind.MANUAL)
    product.servers.add(server)
    return client, product, server


def test_a_new_request_for_a_test_domain_gets_a_random_safe_username(manager, setup):
    client, product, _ = setup
    account = services.request_hosting(manager, client, product, "testing6.com")
    assert len(account.username) == 8 and not services.is_reserved_username(account.username) and "testing" not in account.username


def test_provisioning_an_older_request_with_a_reserved_name_fixes_it_first_and_records_the_change(manager, setup, monkeypatch):
    client, product, server = setup
    seen = {}

    def create(self, **kwargs):
        seen.update(kwargs)
        if services.is_reserved_username(kwargs["username"]):
            raise HostingError(f"({kwargs['username']}) is a reserved username on this system.")
        return {"provider_ref": "ok"}

    monkeypatch.setattr(ManualAdapter, "create_account", create)
    account = HostingAccount.objects.create(client=client, product=product, server=server, domain="testing6.com", username="testing6",
                                            package_name="starter_pkg", status=HostingStatus.FAILED,
                                            last_error="testing6 is a reserved username on this system")
    services.complete_provisioning(manager, account)
    account.refresh_from_db()
    assert account.status == HostingStatus.ACTIVE and account.username != "testing6" and seen["username"] == account.username
    event = AuditEvent.objects.get(action="hosting.username_changed")
    assert event.metadata == {"from": "testing6", "to": account.username, "reason": "reserved on the server"}


def test_a_good_username_is_left_alone(manager, setup, monkeypatch):
    client, product, server = setup
    monkeypatch.setattr(ManualAdapter, "create_account", lambda self, **kw: {"provider_ref": "ok"})
    account = HostingAccount.objects.create(client=client, product=product, server=server, domain="shop.com", username="shop",
                                            package_name="starter_pkg", status=HostingStatus.PENDING)
    services.complete_provisioning(manager, account)
    account.refresh_from_db()
    assert account.username == "shop" and not AuditEvent.objects.filter(action="hosting.username_changed").exists()
