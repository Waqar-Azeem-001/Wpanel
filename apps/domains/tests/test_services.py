import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.clients.models import ContactRole
from apps.core.exceptions import ServiceError
from apps.domains import services
from apps.domains.adapters.manual import ManualAdapter
from apps.domains.models import Domain, DnsRecord, DomainStatus, RegistrarProvider, TldPricing, domain_tld

pytestmark = pytest.mark.django_db


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def registrar():
    return RegistrarProvider.objects.create(name="Test", kind="manual", is_active=True)


@pytest.fixture
def com_pricing(manager, registrar):
    return services.set_tld_pricing(manager, ".com", register_price="10", renew_price="12", transfer_price="9")


@pytest.fixture
def client_obj(manager):
    return client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test"})


# --- Model validation ---------------------------------------------------------------------

def test_domain_tld_extraction():
    assert domain_tld("example.com") == ".com"
    assert domain_tld("sub.example.io") == ".io"


def test_domain_name_validator_rejects_bad_names():
    from apps.domains.models import DOMAIN_NAME_VALIDATOR

    for bad in ("not a domain", "-example.com", "example", "ex ample.com"):
        with pytest.raises(ValidationError):
            DOMAIN_NAME_VALIDATOR(bad)
    DOMAIN_NAME_VALIDATOR("example.com")  # does not raise


def test_request_registration_rejects_unsupported_tld(client_obj, registrar, manager):
    # No TldPricing exists for ".zzz" - checked before the name is otherwise validated.
    with pytest.raises(ServiceError) as exc:
        services.request_registration(manager, client_obj, "example.zzz", 1)
    assert exc.value.code == "tld_not_supported"


def test_nameservers_must_be_2_to_13(client_obj, registrar, com_pricing, manager):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    with pytest.raises(ValidationError):
        services.update_nameservers(manager, domain, ["only-one.example.com"])
    with pytest.raises(ValidationError):
        services.update_nameservers(manager, domain, [f"ns{i}.example.com" for i in range(14)])
    services.update_nameservers(manager, domain, ["ns1.example.com", "ns2.example.com"])
    domain.refresh_from_db()
    assert len(domain.nameservers) == 2


def test_dns_record_priority_required_for_mx_and_srv(manager, client_obj, registrar, com_pricing):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    with pytest.raises(ValidationError):
        services.add_dns_record(manager, domain, {"record_type": "MX", "content": "mail.example.com"})
    record = services.add_dns_record(manager, domain, {"record_type": "MX", "content": "mail.example.com",
                                                       "priority": 10})
    assert record.priority == 10
    with pytest.raises(ValidationError):
        services.add_dns_record(manager, domain, {"record_type": "A", "content": "1.2.3.4", "priority": 5})


def test_tld_pricing_requires_leading_dot_and_valid_year_range(manager, registrar):
    with pytest.raises(ValidationError):
        services.set_tld_pricing(manager, "com", register_price="1", renew_price="1", transfer_price="1")
    with pytest.raises(ValidationError):
        services.set_tld_pricing(manager, ".com", register_price="1", renew_price="1", transfer_price="1",
                                 min_years=5, max_years=1)


def test_unique_live_domain_name_allows_reuse_after_cancellation(manager, client_obj, registrar, com_pricing):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    services.cancel_domain_request(manager, domain, reason="changed mind")
    # The name is free again once the prior request is cancelled.
    again = services.request_registration(manager, client_obj, "example.com", 1)
    assert again.pk != domain.pk


def test_duplicate_live_domain_name_rejected_at_db_level(manager, client_obj, registrar, com_pricing):
    services.request_registration(manager, client_obj, "example.com", 1)
    # Bypass the availability check (which already blocks this) to prove the DB constraint too.
    with pytest.raises(Exception):
        Domain.objects.create(client=client_obj, name="example.com", status=DomainStatus.PENDING_REGISTRATION)


def test_registrar_provider_single_active_and_credentials_round_trip():
    a = RegistrarProvider.objects.create(name="A", kind="manual", is_active=True)
    a.set_credentials({"api_key": "secret123"})
    a.save()
    assert "secret123" not in a.credentials_encrypted
    assert a.get_credentials() == {"api_key": "secret123"}

    from django.db import IntegrityError
    with pytest.raises(IntegrityError):
        RegistrarProvider.objects.create(name="B", kind="manual", is_active=True)


# --- Manual adapter -------------------------------------------------------------------------

def test_manual_adapter_availability_reflects_db_state(manager, client_obj, registrar, com_pricing):
    adapter = ManualAdapter({})
    assert adapter.check_availability("fresh.com") is True
    services.request_registration(manager, client_obj, "fresh.com", 1)
    assert adapter.check_availability("fresh.com") is False


def test_manual_adapter_renew_extends_from_current_expiry_if_active(manager, client_obj, registrar, com_pricing):
    domain = services.complete_registration(
        manager, services.request_registration(manager, client_obj, "example.com", 1))
    first_expiry = domain.expires_at
    domain = services.renew_domain(manager, domain, 1)
    assert domain.expires_at > first_expiry
    # Roughly a year later, not "now + 1 year" (which would lose the remaining time).
    assert (domain.expires_at - first_expiry).days >= 360


# --- Availability / pricing --------------------------------------------------------------

def test_check_availability_rejects_unsupported_tld(registrar):
    with pytest.raises(ServiceError) as exc:
        services.check_availability("example.zzz")
    assert exc.value.code == "tld_not_supported"


def test_check_availability_requires_active_registrar(com_pricing):
    RegistrarProvider.objects.update(is_active=False)
    with pytest.raises(ServiceError) as exc:
        services.check_availability("example.com")
    assert exc.value.code == "no_registrar_configured"


def test_inactive_tld_pricing_blocks_new_availability_checks(manager, com_pricing):
    services.set_tld_pricing_active(manager, com_pricing, False)
    with pytest.raises(ServiceError):
        services.check_availability("example.com")


# --- Registration ---------------------------------------------------------------------------

def test_request_registration_requires_contact_or_manage_permission(customer, client_obj, registrar, com_pricing):
    with pytest.raises(ServiceError) as exc:
        services.request_registration(customer, client_obj, "example.com", 1)
    assert exc.value.code == "permission_denied"


def test_customers_cannot_create_a_registration_outside_checkout(manager, client_obj, registrar, com_pricing, make_user):
    tech = make_user("tech@example.com")
    client_services.add_contact(manager, client_obj, email="tech@example.com", role=ContactRole.TECHNICAL)
    for actor in (tech, client_obj.contacts.get(role="owner").user):
        with pytest.raises(ServiceError) as exc:
            services.request_registration(actor, client_obj, "example.com", 1)
        assert exc.value.code == "permission_denied"
        with pytest.raises(ServiceError):
            services.request_transfer_in(actor, client_obj, "example.com", "epp", 1)


def test_the_system_actor_may_register_for_a_paid_order(client_obj, registrar, com_pricing):
    from apps.core.system import SYSTEM

    domain = services.request_registration(SYSTEM, client_obj, "example.com", 1)
    assert domain.status == DomainStatus.PENDING_REGISTRATION


def test_request_registration_rejects_unavailable_domain(manager, client_obj, registrar, com_pricing):
    services.request_registration(manager, client_obj, "example.com", 1)
    other_client = client_services.create_client(manager, {"first_name": "B", "email": "b@x.test"})
    with pytest.raises(ServiceError) as exc:
        services.request_registration(manager, other_client, "example.com", 1)
    assert exc.value.code == "domain_unavailable"


def test_request_registration_validates_year_range(manager, client_obj, registrar, com_pricing):
    with pytest.raises(ServiceError) as exc:
        services.request_registration(manager, client_obj, "example.com", 99)
    assert exc.value.code == "invalid_term"


def test_complete_registration_is_idempotent(manager, client_obj, registrar, com_pricing):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    first = services.complete_registration(manager, domain)
    assert first.status == DomainStatus.ACTIVE
    second = services.complete_registration(manager, first)
    assert second.provider_ref == first.provider_ref  # not re-registered
    assert AuditEvent.objects.filter(action="domain.registered").count() == 1


def test_complete_registration_requires_manage_domains(customer, manager, client_obj, registrar, com_pricing):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    with pytest.raises(ServiceError):
        services.complete_registration(customer, domain)


def test_complete_registration_from_wrong_status_rejected(manager, client_obj, registrar, com_pricing):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    services.cancel_domain_request(manager, domain)
    with pytest.raises(ServiceError) as exc:
        services.complete_registration(manager, domain)
    assert exc.value.code == "invalid_status"


def test_registration_failure_sets_failed_and_allows_retry(manager, client_obj, registrar, com_pricing, monkeypatch):
    from apps.domains.adapters.base import RegistrarError

    domain = services.request_registration(manager, client_obj, "example.com", 1)

    def boom(self, *a, **k):
        raise RegistrarError("registry unreachable")

    monkeypatch.setattr(ManualAdapter, "register_domain", boom)
    with pytest.raises(ServiceError) as exc:
        services.complete_registration(manager, domain)
    assert exc.value.code == "registrar_error"
    domain.refresh_from_db()
    assert domain.status == DomainStatus.FAILED and "registry unreachable" in domain.last_error

    monkeypatch.undo()
    completed = services.complete_registration(manager, domain)  # retry succeeds
    assert completed.status == DomainStatus.ACTIVE


# --- Transfer ------------------------------------------------------------------------------

def test_transfer_requires_auth_code(manager, client_obj, registrar, com_pricing):
    with pytest.raises(ServiceError) as exc:
        services.request_transfer_in(manager, client_obj, "example.com", "", 1)
    assert exc.value.code == "auth_code_required"


def test_transfer_flow_and_auth_code_cleared_on_completion(manager, client_obj, registrar, com_pricing):
    domain = services.request_transfer_in(manager, client_obj, "example.com", "epp-secret", 1)
    assert domain.get_auth_code() == "epp-secret"
    domain = services.complete_transfer(manager, domain)
    assert domain.status == DomainStatus.ACTIVE and domain.get_auth_code() == ""


def test_transfer_completion_is_idempotent(manager, client_obj, registrar, com_pricing):
    domain = services.request_transfer_in(manager, client_obj, "example.com", "epp-secret", 1)
    first = services.complete_transfer(manager, domain)
    second = services.complete_transfer(manager, first)
    assert second.provider_ref == first.provider_ref


def test_cancel_only_from_pending_or_failed(manager, client_obj, registrar, com_pricing):
    domain = services.complete_registration(
        manager, services.request_registration(manager, client_obj, "example.com", 1))
    with pytest.raises(ServiceError) as exc:
        services.cancel_domain_request(manager, domain)
    assert exc.value.code == "invalid_status"


# --- Renewal ------------------------------------------------------------------------------

def test_renew_requires_active_status(manager, client_obj, registrar, com_pricing):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    with pytest.raises(ServiceError) as exc:
        services.renew_domain(manager, domain, 1)
    assert exc.value.code == "invalid_status"


def test_renew_respects_max_years_and_is_audited(manager, client_obj, registrar, com_pricing):
    domain = services.complete_registration(
        manager, services.request_registration(manager, client_obj, "example.com", 1))
    with pytest.raises(ServiceError):
        services.renew_domain(manager, domain, 99)
    services.renew_domain(manager, domain, 2)
    event = AuditEvent.objects.get(action="domain.renewed")
    assert event.metadata["years"] == 2


# --- Self-service: auto-renew, nameservers, lock, DNS ---------------------------------------

def test_self_service_denied_for_non_contact_non_staff(customer, manager, client_obj, registrar, com_pricing):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    with pytest.raises(ServiceError):
        services.set_auto_renew(customer, domain, False)
    with pytest.raises(ServiceError):
        services.update_nameservers(customer, domain, ["ns1.example.com", "ns2.example.com"])
    with pytest.raises(ServiceError):
        services.lock_domain(customer, domain)


def test_self_service_allowed_for_any_client_contact(manager, client_obj, registrar, com_pricing):
    owner = client_obj.contacts.get().user
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    services.set_auto_renew(owner, domain, False)
    domain.refresh_from_db()
    assert domain.auto_renew is False


def test_lock_unlock_toggle_and_audit(manager, client_obj, registrar, com_pricing):
    domain = services.complete_registration(
        manager, services.request_registration(manager, client_obj, "example.com", 1))
    assert domain.is_locked is True
    services.unlock_domain(manager, domain)
    domain.refresh_from_db()
    assert domain.is_locked is False
    assert AuditEvent.objects.filter(action="domain.lock_changed", metadata__is_locked=False).exists()
    # No-op re-unlock doesn't double-audit.
    services.unlock_domain(manager, domain)
    assert AuditEvent.objects.filter(action="domain.lock_changed").count() == 1


def test_sync_domain_requires_provider_ref(manager, client_obj, registrar, com_pricing):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    with pytest.raises(ServiceError) as exc:
        services.sync_domain(manager, domain)
    assert exc.value.code == "not_registered"


def test_sync_domain_pulls_registrar_truth(manager, client_obj, registrar, com_pricing):
    domain = services.complete_registration(
        manager, services.request_registration(manager, client_obj, "example.com", 1))
    Domain.objects.filter(pk=domain.pk).update(expires_at=domain.expires_at, nameservers=["ns1.x.com", "ns2.x.com"])
    domain.refresh_from_db()
    services.update_nameservers(manager, domain, ["ns1.x.com", "ns2.x.com"])
    synced = services.sync_domain(manager, domain)
    assert synced.nameservers == ["ns1.x.com", "ns2.x.com"]


def test_dns_crud_and_permission(manager, customer, client_obj, registrar, com_pricing):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    with pytest.raises(ServiceError):
        services.add_dns_record(customer, domain, {"record_type": "A", "content": "1.2.3.4"})

    record = services.add_dns_record(manager, domain, {"record_type": "A", "content": "1.2.3.4"})
    updated = services.update_dns_record(manager, domain, record, {"content": "5.6.7.8"})
    assert updated.content == "5.6.7.8"
    services.delete_dns_record(manager, domain, record)
    assert not DnsRecord.objects.filter(pk=record.pk).exists()
    actions = set(AuditEvent.objects.filter(action__startswith="domain.dns_record").values_list("action", flat=True))
    assert actions == {"domain.dns_record_added", "domain.dns_record_updated", "domain.dns_record_removed"}


# --- Visibility ---------------------------------------------------------------------------

def test_visible_domains_for_user_scoping(manager, customer, client_obj, registrar, com_pricing):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    assert domain in services.visible_domains_for_user(manager)
    assert domain not in services.visible_domains_for_user(customer)
    assert services.visible_domains_for_user(None).count() == 0

    owner = client_obj.contacts.get().user
    assert domain in services.visible_domains_for_user(owner)


def test_search_domains_matches_name_and_client(manager, client_obj, registrar, com_pricing):
    services.request_registration(manager, client_obj, "acme-example.com", 1)
    other = client_services.create_client(manager, {"first_name": "B", "company_name": "Beta Corp",
                                                     "email": "b@beta.test"})
    services.request_registration(manager, other, "other.com", 1)
    assert services.search_domains(Domain.objects.all(), "acme").count() == 1
    assert services.search_domains(Domain.objects.all(), "beta").count() == 1
    assert services.search_domains(Domain.objects.all(), "").count() == 2


# --- TLD pricing (staff) -------------------------------------------------------------------

def test_set_tld_pricing_upserts_same_row(manager, registrar):
    first = services.set_tld_pricing(manager, ".io", register_price="20.00", renew_price="22.00", transfer_price="18.00")
    second = services.set_tld_pricing(manager, ".io", register_price="25.00", renew_price="22.00", transfer_price="18.00")
    assert first.pk == second.pk and str(second.register_price) == "25.00"
    assert TldPricing.objects.filter(tld=".io").count() == 1
    assert AuditEvent.objects.filter(action="tld_pricing.added").count() == 1
    assert AuditEvent.objects.filter(action="tld_pricing.changed").count() == 1


def test_tld_pricing_requires_manage_domains(customer, registrar):
    with pytest.raises(ServiceError):
        services.set_tld_pricing(customer, ".io", register_price="1", renew_price="1", transfer_price="1")


def test_visible_tlds_for_user(manager, customer, registrar):
    active = services.set_tld_pricing(manager, ".com", register_price="1", renew_price="1", transfer_price="1")
    hidden = services.set_tld_pricing(manager, ".xyz", register_price="1", renew_price="1", transfer_price="1")
    services.set_tld_pricing_active(manager, hidden, False)
    assert set(services.visible_tlds_for_user(manager)) == {active, hidden}
    assert set(services.visible_tlds_for_user(customer)) == {active}


def test_set_tld_pricing_with_an_overlong_tld_is_a_validation_error_not_a_database_error(manager, registrar):
    """Regression: validate before writing - PostgreSQL rejects an over-long value at INSERT with a raw DB error."""
    with pytest.raises(ValidationError):
        services.set_tld_pricing(manager, "." + "x" * 40, register_price="1", renew_price="1", transfer_price="1")
    assert TldPricing.objects.count() == 0
