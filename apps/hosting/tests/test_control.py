"""The provider-neutral hosting-control gateway (H0): capability discovery, authorization, the allowlist, audit."""
import pytest

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.core.exceptions import ServiceError
from apps.hosting import control, services
from apps.hosting.adapters import capabilities as caps
from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.capabilities import Capability
from apps.hosting.adapters.manual import ManualAdapter
from apps.hosting.models import HostingStatus
from apps.products import services as product_services
from apps.products.models import ProductType

pytestmark = pytest.mark.django_db


class FakeDnsProvider(caps.DnsCapability, ManualAdapter):
    """A provider that implements only the DNS capability, in memory."""

    zone = {}

    def list_dns_records(self, username, domain):
        return list(self.zone.get(domain, []))

    def add_dns_record(self, username, domain, record):
        self.zone.setdefault(domain, []).append(record)
        return record.record_id

    def update_dns_record(self, username, domain, record):
        if record.name == "boom":
            raise HostingError("zone locked")

    def delete_dns_record(self, username, domain, record_id):
        self.zone[domain] = [r for r in self.zone.get(domain, []) if r.record_id != record_id]


@pytest.fixture(autouse=True)
def fresh_zone():
    FakeDnsProvider.zone = {}


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def server(manager):
    return product_services.create_server(manager, {"name": "srv1", "hostname": "srv1.example.com"})


@pytest.fixture
def product(manager, server):
    p = product_services.create_product(manager, {"name": "Starter", "type": ProductType.SHARED_HOSTING,
                                                   "whm_package_name": "starter_pkg"})
    product_services.set_product_servers(manager, p, [server.pk])
    return p


@pytest.fixture
def client_obj(manager):
    return client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test"})


@pytest.fixture
def owner(client_obj):
    return client_obj.contacts.get(role="owner").user


@pytest.fixture
def account(manager, client_obj, product):
    return services.complete_provisioning(manager, services.request_hosting(manager, client_obj, product,
                                                                            "example.com"))


@pytest.fixture
def dns_provider(monkeypatch):
    monkeypatch.setattr(control, "get_adapter", lambda server: FakeDnsProvider(host="h", port=1, credentials={}))


# --- Capability discovery ---------------------------------------------------------------------------------------

def test_a_provider_with_only_the_lifecycle_supports_account_and_usage(account):
    assert control.capabilities_for(account) == ["account", "usage"]


def test_a_provider_declares_what_it_implements_by_the_contracts_it_subclasses(account, dns_provider):
    assert control.capabilities_for(account) == ["account", "dns", "usage"]


def test_an_account_without_a_server_has_no_capabilities(client_obj, product, manager):
    pending = services.request_hosting(manager, client_obj, product, "bare.example.com")
    pending.server = None
    assert control.capabilities_for(pending) == []


def test_every_capability_has_a_contract_and_an_allowlist_that_matches_it():
    assert set(caps.OPERATIONS) == set(Capability)
    assert set(caps.CONTRACTS) | {Capability.ACCOUNT, Capability.USAGE} == set(Capability)
    for capability, contract in caps.CONTRACTS.items():
        methods = {n for n, m in vars(contract).items() if getattr(m, "__isabstractmethod__", False)}
        assert methods == caps.OPERATIONS[capability], capability


# --- Performing an operation ------------------------------------------------------------------------------------

def test_a_supported_read_returns_the_providers_answer_without_an_audit_entry(manager, account, dns_provider):
    assert control.perform(manager, account, "dns", "list_dns_records", "example.com") == []
    assert not AuditEvent.objects.filter(action="hosting.control").exists()


def test_a_change_is_performed_and_audited(manager, account, dns_provider):
    record = caps.DnsRecord(record_id="r1", type="A", name="www", value="203.0.113.5")
    control.perform(manager, account, Capability.DNS, "add_dns_record", "example.com", record,
                    audit_metadata={"record": "A www"})
    assert [r.record_id for r in control.perform(manager, account, "dns", "list_dns_records", "example.com")] == ["r1"]
    event = AuditEvent.objects.get(action="hosting.control")
    assert event.metadata == {"capability": "dns", "operation": "add_dns_record", "record": "A www", "admin_access": True, "client": account.client_id, "service": account.pk}  # a manager is staff acting on a customer's account


def test_a_provider_failure_becomes_a_service_error_and_a_failed_audit_entry(manager, account, dns_provider):
    record = caps.DnsRecord(record_id="r1", type="A", name="boom", value="x")
    with pytest.raises(ServiceError) as exc:
        control.perform(manager, account, "dns", "update_dns_record", "example.com", record)
    assert exc.value.code == "adapter_error"
    assert AuditEvent.objects.filter(action="hosting.control_failed").exists()


def test_an_unsupported_capability_is_a_clear_answer_not_a_crash(manager, account):
    with pytest.raises(ServiceError) as exc:
        control.perform(manager, account, "files", "list_files", "/")
    assert exc.value.code == "capability_not_supported" and exc.value.status_code == 501


def test_only_allowlisted_operations_can_be_reached(manager, account, dns_provider):
    for operation in ("create_account", "terminate_account", "__class__", "_call", "list_files"):
        with pytest.raises(ServiceError) as exc:
            control.perform(manager, account, "dns", operation)
        assert exc.value.code == "operation_not_allowed"
    with pytest.raises(ValueError):
        control.perform(manager, account, "shell", "run")  # not even a capability


def test_a_terminated_account_cannot_be_controlled(manager, account, dns_provider):
    account.status = HostingStatus.TERMINATED
    account.save(update_fields=["status"])
    with pytest.raises(ServiceError) as exc:
        control.perform(manager, account, "dns", "list_dns_records", "example.com")
    assert exc.value.code == "account_not_live"


# --- Who may ----------------------------------------------------------------------------------------------------

def test_the_owner_may_read_and_change_their_own_account(owner, account, dns_provider):
    record = caps.DnsRecord(record_id="r1", type="A", name="www", value="203.0.113.5")
    control.perform(owner, account, "dns", "add_dns_record", "example.com", record)
    assert len(control.perform(owner, account, "dns", "list_dns_records", "example.com")) == 1


def test_a_stranger_cannot_read_or_change_anything(customer, account, dns_provider):
    record = caps.DnsRecord(record_id="r9", type="A", name="x", value="y")
    for operation, args in (("list_dns_records", ("example.com",)), ("add_dns_record", ("example.com", record))):
        with pytest.raises(ServiceError) as exc:
            control.perform(customer, account, "dns", operation, *args)
        assert exc.value.code == "permission_denied"
    assert FakeDnsProvider.zone == {}


# --- The API ----------------------------------------------------------------------------------------------------

def test_the_capabilities_endpoint_is_provider_neutral_and_scoped(api, manager, customer, account, dns_provider):
    api.force_authenticate(manager)
    response = api.get(f"/api/v1/hosting-accounts/{account.pk}/capabilities/")
    assert response.status_code == 200 and response.json() == {"capabilities": ["account", "dns", "usage"]}
    body = response.content.decode().lower()
    assert "whm" not in body and "cpanel" not in body
    api.force_authenticate(customer)
    assert api.get(f"/api/v1/hosting-accounts/{account.pk}/capabilities/").status_code == 404  # not theirs
