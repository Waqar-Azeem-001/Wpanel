"""What the domain services do when the registrar says no, is slow, or the person asking is not allowed: nothing is half-changed and the reason is plain."""
import pytest
from django.core.cache import cache

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.core.exceptions import ServiceError
from apps.domains import services
from apps.domains.adapters.base import RegistrarError
from apps.domains.adapters.manual import ManualAdapter
from apps.domains.models import Domain, DomainStatus, RegistrarProvider, TldPricing

pytestmark = pytest.mark.django_db


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def registrar():
    return RegistrarProvider.objects.create(name="Test", kind="manual", is_active=True)


@pytest.fixture
def pricing(manager, registrar):
    return services.set_tld_pricing(manager, ".com", register_price="10", renew_price="12", transfer_price="9")


@pytest.fixture
def client_obj(manager):
    return client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test"})


@pytest.fixture
def owner(client_obj):
    user = client_obj.contacts.get().user
    user.set_password("Secret-pass-1")
    user.save()
    return user


@pytest.fixture
def active(manager, client_obj, registrar, pricing):
    return services.complete_registration(manager, services.request_registration(manager, client_obj, "example.com", 1))


def refused(code, call, *args, **kwargs):
    with pytest.raises(ServiceError) as caught:
        call(*args, **kwargs)
    assert caught.value.code == code, (caught.value.code, caught.value.message)
    return caught.value


def registrar_says_no(monkeypatch, *names):
    def no(self, *args, **kwargs):
        raise RegistrarError("registrar said no")

    for name in names:
        monkeypatch.setattr(ManualAdapter, name, no)


# --- transferring in --------------------------------------------------------------------------------------------------------------------

def test_a_transfer_in_needs_a_code_and_a_sensible_term(manager, client_obj, registrar, pricing):
    refused("auth_code_required", services.request_transfer_in, manager, client_obj, "moving.com", "")
    refused("invalid_term", services.request_transfer_in, manager, client_obj, "moving.com", "EPP", years=99)


def test_only_a_pending_or_failed_transfer_can_be_completed(manager, client_obj, registrar, pricing):
    pending_registration = services.request_registration(manager, client_obj, "fresh.com", 1)
    refused("invalid_status", services.complete_transfer, manager, pending_registration)


def test_a_registrar_that_refuses_the_transfer_marks_it_failed_and_a_retry_is_allowed(manager, client_obj, registrar, pricing, monkeypatch):
    incoming = services.request_transfer_in(manager, client_obj, "moving.com", "EPP-1")
    registrar_says_no(monkeypatch, "transfer_domain")
    refused("registrar_error", services.complete_transfer, manager, incoming)
    incoming.refresh_from_db()
    assert incoming.status == DomainStatus.FAILED and "registrar said no" in incoming.last_error
    assert AuditEvent.objects.filter(action="domain.transfer_failed").exists()
    monkeypatch.undo()
    assert services.complete_transfer(manager, incoming).status == DomainStatus.ACTIVE  # the code is still kept, so a retry works


def test_a_transfer_that_is_still_under_way_says_where_it_is_in_the_registrars_words(manager, client_obj, registrar, pricing, monkeypatch):
    incoming = services.request_transfer_in(manager, client_obj, "moving.com", "EPP-1")
    Domain.objects.filter(pk=incoming.pk).update(provider_ref="moving.com")
    incoming.refresh_from_db()
    registrar_says_no(monkeypatch, "get_domain")
    monkeypatch.setattr(ManualAdapter, "transfer_status", lambda self, name, code: "waiting for the old registrar", raising=False)
    result = services.complete_transfer(manager, incoming)  # a second click must ask, not send again
    assert result.status == DomainStatus.PENDING_TRANSFER_IN and "waiting for the old registrar" in result.last_error
    monkeypatch.setattr(ManualAdapter, "transfer_status", lambda self, name, code: (_ for _ in ()).throw(RegistrarError("no status")), raising=False)
    assert "registrar said no" in services.poll_transfer(manager, incoming).last_error  # no status either: the registrar's own refusal is shown instead


# --- changes that must reach the registrar --------------------------------------------------------------------------------------

def test_nameservers_do_not_change_here_when_the_registrar_refuses(owner, active, monkeypatch):
    before = list(active.nameservers)
    registrar_says_no(monkeypatch, "update_nameservers")
    refused("registrar_error", services.update_nameservers, owner, active, ["ns1.new.test", "ns2.new.test"])
    active.refresh_from_db()
    assert active.nameservers == before


def test_the_transfer_lock_does_not_change_here_when_the_registrar_refuses(owner, active, monkeypatch):
    was = active.is_locked
    registrar_says_no(monkeypatch, "lock_domain", "unlock_domain")
    refused("registrar_error", services.lock_domain if not was else services.unlock_domain, owner, active)
    active.refresh_from_db()
    assert active.is_locked == was


def test_a_failed_sync_and_a_failed_dns_push_are_explained(manager, owner, active, monkeypatch):
    registrar_says_no(monkeypatch, "get_domain")
    refused("registrar_error", services.sync_domain, manager, active)
    monkeypatch.undo()
    registrar_says_no(monkeypatch, "update_dns")
    refused("registrar_error", services.add_dns_record, owner, active, {"record_type": "A", "name": "www", "content": "203.0.113.9", "ttl": 3600})


def test_a_duplicate_dns_record_is_refused_and_a_different_one_is_kept(owner, active):
    record = {"record_type": "A", "name": "www", "content": "203.0.113.9", "ttl": 3600}
    services.add_dns_record(owner, active, record)
    refused("duplicate_record", services.add_dns_record, owner, active, record)
    services.add_dns_record(owner, active, {**record, "content": "203.0.113.10"})
    assert active.dns_records.count() == 2


# --- prices and the registrar connection ---------------------------------------------------------------------------------------

def test_switching_an_extension_to_the_state_it_is_in_changes_nothing(manager, pricing):
    before = AuditEvent.objects.count()
    assert services.set_tld_pricing_active(manager, pricing, pricing.is_active) == pricing
    assert AuditEvent.objects.count() == before


def test_the_registrar_connection_is_only_for_those_who_may_manage_providers(customer, staff, registrar):
    refused("permission_denied", services.save_registrar_provider, customer, None, {"name": "X", "kind": "manual"})
    boss = staff(Role.SUPER_ADMIN)
    refused("invalid_credentials", services.save_registrar_provider, boss, None, {"name": "X", "kind": "onlinenic"}, credentials=["not", "a", "dict"])
    refused("permission_denied", services.delete_registrar_provider, customer, registrar)
    refused("provider_active", services.delete_registrar_provider, boss, registrar)  # the one in use cannot be deleted from under the shop
    spare = services.save_registrar_provider(boss, None, {"name": "Spare", "kind": "onlinenic", "sandbox": False}, credentials={"user": "1", "password": "p", "apikey": "k"})
    assert spare.get_credentials()["user"] == "1" and "password" not in spare.credentials_encrypted  # stored encrypted, not readable
    services.delete_registrar_provider(boss, spare)
    assert not RegistrarProvider.objects.filter(pk=spare.pk).exists()


# --- the transfer code (moving a domain away) ----------------------------------------------------------------------------------

@pytest.fixture
def onlinenic(registrar):
    provider = RegistrarProvider(name="OnlineNIC", kind="onlinenic", sandbox=False)
    provider.set_credentials({"user": "1", "password": "p", "apikey": "k"})
    provider.save()
    return provider


def test_a_transfer_code_is_only_given_for_an_active_domain_with_a_connected_registrar_and_the_right_password(owner, active, manager, client_obj, registrar, pricing):
    refused("no_transfer_code_provider", services.get_transfer_code, owner, active, "Secret-pass-1")  # only the manual registrar is saved
    RegistrarProvider.objects.create(name="OnlineNIC", kind="onlinenic", sandbox=False)
    refused("wrong_password", services.get_transfer_code, owner, active, "nope")
    assert AuditEvent.objects.filter(action="domain.transfer_code_refused").exists()
    waiting = services.request_registration(manager, client_obj, "later.com", 1)
    refused("domain_not_active", services.get_transfer_code, owner, waiting, "Secret-pass-1")


def test_a_locked_domain_and_a_registrar_that_refuses_are_explained_and_the_limit_counts(owner, active, onlinenic, monkeypatch):
    cache.clear()
    Domain.objects.filter(pk=active.pk).update(is_locked=True)
    active.refresh_from_db()
    refused("domain_locked", services.get_transfer_code, owner, active, "Secret-pass-1")
    Domain.objects.filter(pk=active.pk).update(is_locked=False)
    active.refresh_from_db()
    from apps.domains.adapters.onlinenic import OnlineNicAdapter

    def no(self, *args, **kwargs):
        raise RegistrarError("IP not allowed")

    monkeypatch.setattr(OnlineNicAdapter, "unlock_domain", lambda self, *a, **k: None)
    monkeypatch.setattr(OnlineNicAdapter, "get_auth_code", no)
    refused("registrar_error", services.get_transfer_code, owner, active, "Secret-pass-1")
    assert AuditEvent.objects.filter(action="domain.transfer_code_failed").exists()
    for _ in range(services.TRANSFER_CODE_PER_HOUR):
        with pytest.raises(ServiceError):
            services.get_transfer_code(owner, active, "Secret-pass-1")
    refused("rate_limited", services.get_transfer_code, owner, active, "Secret-pass-1")


def test_a_failing_alert_email_never_stops_the_owner_getting_the_code(owner, active, onlinenic, monkeypatch):
    from apps.domains.adapters.onlinenic import OnlineNicAdapter

    cache.clear()
    Domain.objects.filter(pk=active.pk).update(is_locked=False)  # the owner has unlocked it, as the page asks
    active.refresh_from_db()
    monkeypatch.setattr(OnlineNicAdapter, "unlock_domain", lambda self, *a, **k: None)
    monkeypatch.setattr(OnlineNicAdapter, "get_auth_code", lambda self, name: "EPP-OK")

    def broken(*args, **kwargs):
        raise RuntimeError("mail system down")

    monkeypatch.setattr("apps.notifications.services.dispatch_client", broken)
    assert services.get_transfer_code(owner, active, "Secret-pass-1") == "EPP-OK"
