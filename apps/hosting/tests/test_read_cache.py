"""A slow provider's reads are remembered for a few seconds; any change makes them stale at once; private things are never remembered."""
import pytest

from apps.accounts.roles import Role
from apps.clients import services as client_services
from apps.hosting import control
from apps.hosting.adapters import capabilities as caps
from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.manual import ManualAdapter
from apps.hosting.models import HostingAccount, HostingStatus
from apps.products import services as product_services
from apps.products.models import Product, ProductType, Server, ServerKind

pytestmark = pytest.mark.django_db


class SlowDns(caps.DnsCapability, ManualAdapter):
    read_cache_seconds = 30
    zone = []
    reads = 0

    def list_dns_records(self, username, domain):
        type(self).reads += 1
        return list(type(self).zone)

    def add_dns_record(self, username, domain, record):
        type(self).zone.append(record)
        return "id"

    def update_dns_record(self, username, domain, record):
        raise HostingError("zone locked")

    def delete_dns_record(self, username, domain, record_id):
        type(self).zone.clear()


@pytest.fixture(autouse=True)
def fresh():
    SlowDns.zone, SlowDns.reads = [], 0


@pytest.fixture
def setup(staff, monkeypatch):
    manager = staff(Role.MANAGER)
    client = client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test"})
    product = Product.objects.get(pk=product_services.create_product(manager, {"name": "S", "type": ProductType.SHARED_HOSTING}).pk)
    server = Server.objects.create(name="s", hostname="s.test", kind=ServerKind.MANUAL)
    account = HostingAccount.objects.create(client=client, product=product, server=server, domain="acme.com", username="acme", status=HostingStatus.ACTIVE)
    monkeypatch.setattr(control, "get_adapter", lambda server: SlowDns(host="h", port=1, credentials={}))
    return manager, account, client.contacts.get().user


def test_the_same_read_is_answered_from_memory_until_something_changes(setup):
    manager, account, owner = setup
    for _ in range(3):
        assert control.perform(owner, account, "dns", "list_dns_records", "acme.com") == []
    assert SlowDns.reads == 1
    control.perform(owner, account, "dns", "add_dns_record", "acme.com", caps.DnsRecord("", "A", "x", "1.2.3.4"))
    assert len(control.perform(owner, account, "dns", "list_dns_records", "acme.com")) == 1 and SlowDns.reads == 2


def test_a_failed_change_also_makes_the_memory_stale_because_it_may_have_half_worked(setup):
    manager, account, owner = setup
    control.perform(owner, account, "dns", "list_dns_records", "acme.com")
    control.perform(owner, account, "dns", "list_dns_records", "acme.com")
    assert SlowDns.reads == 1
    with pytest.raises(Exception):
        control.perform(owner, account, "dns", "update_dns_record", "acme.com", caps.DnsRecord("r", "A", "x", "1.2.3.4"))
    reads_after_failure = SlowDns.reads
    control.perform(owner, account, "dns", "list_dns_records", "acme.com")
    assert SlowDns.reads == reads_after_failure + 1  # asked the server again, not the memory


def test_different_questions_accounts_and_arguments_are_remembered_separately(setup):
    manager, account, owner = setup
    control.perform(owner, account, "dns", "list_dns_records", "acme.com")
    control.perform(owner, account, "dns", "list_dns_records", "other.com")
    assert SlowDns.reads == 2
    second = HostingAccount.objects.create(client=account.client, product=account.product, server=account.server, domain="b.com", username="bee",
                                           status=HostingStatus.ACTIVE)
    control.perform(owner, second, "dns", "list_dns_records", "acme.com")
    assert SlowDns.reads == 3
    control.perform(owner, account, "dns", "add_dns_record", "acme.com", caps.DnsRecord("", "A", "x", "1.2.3.4"))  # a change to one account...
    control.perform(owner, second, "dns", "list_dns_records", "acme.com")
    assert SlowDns.reads == 3  # ...leaves the other account's answers alone


def test_permission_is_checked_before_the_memory_is_used(setup, make_user):
    manager, account, owner = setup
    control.perform(owner, account, "dns", "list_dns_records", "acme.com")
    from apps.core.exceptions import ServiceError

    with pytest.raises(ServiceError):
        control.perform(make_user("stranger@example.com"), account, "dns", "list_dns_records", "acme.com")


def test_a_provider_that_does_not_ask_for_it_is_never_remembered(setup, monkeypatch):
    manager, account, owner = setup

    class Quick(SlowDns):
        read_cache_seconds = 0

    monkeypatch.setattr(control, "get_adapter", lambda server: Quick(host="h", port=1, credentials={}))
    Quick.reads = 0
    for _ in range(3):
        control.perform(owner, account, "dns", "list_dns_records", "acme.com")
    assert Quick.reads == 3


def test_contents_and_one_time_links_are_never_in_the_list_of_remembered_things():
    for operation in ("read_file", "read_message", "list_messages", "webmail_access", "browse_table", "database_admin_access", "open_native_panel"):
        assert operation in control.NOT_CACHED
