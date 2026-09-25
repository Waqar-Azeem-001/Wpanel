import pytest
from django.core.exceptions import ValidationError

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.clients.models import ContactRole
from apps.core.exceptions import ServiceError
from apps.hosting import services
from apps.hosting.models import HostingAccount, HostingStatus
from apps.products import services as product_services
from apps.products.models import ProductType, ServerStatus

pytestmark = pytest.mark.django_db


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
    return client_obj.contacts.get().user


# --- Username generation --------------------------------------------------------------------

def test_generate_username_from_domain():
    assert services._generate_username("example.com") == "example"
    assert services._generate_username("my-cool-site.co") == "mycoolsite"


def test_generate_username_ensures_letter_start_and_uniqueness(manager, client_obj, product):
    services.request_hosting(manager, client_obj, product, "example.com")
    second = services.request_hosting(manager, client_obj, product, "example.net")
    assert second.username == "example2"


# --- Request & provisioning ----------------------------------------------------------------

def test_request_hosting_auto_assigns_single_mapped_server(manager, client_obj, product, server):
    account = services.request_hosting(manager, client_obj, product, "example.com")
    assert account.server == server and account.status == HostingStatus.PENDING
    assert account.package_name == "starter_pkg"


def test_request_hosting_requires_contact_or_manage_permission(customer, client_obj, product):
    with pytest.raises(ServiceError) as exc:
        services.request_hosting(customer, client_obj, product, "example.com")
    assert exc.value.code == "permission_denied"


def test_customers_cannot_create_hosting_outside_checkout(manager, client_obj, product, make_user):
    tech = make_user("tech@example.com")
    client_services.add_contact(manager, client_obj, email="tech@example.com", role=ContactRole.TECHNICAL)
    for actor in (tech, client_obj.contacts.get(role="owner").user):
        with pytest.raises(ServiceError) as exc:
            services.request_hosting(actor, client_obj, product, "example.com")
        assert exc.value.code == "permission_denied"


def test_the_system_actor_may_create_hosting_for_a_paid_order(client_obj, product):
    from apps.core.system import SYSTEM

    account = services.request_hosting(SYSTEM, client_obj, product, "example.com")
    assert account.status == HostingStatus.PENDING


def test_request_hosting_requires_whm_package(manager, client_obj, server):
    bare_product = product_services.create_product(manager, {"name": "NoPkg", "type": ProductType.VPS})
    with pytest.raises(ServiceError) as exc:
        services.request_hosting(manager, client_obj, bare_product, "example.com")
    assert exc.value.code == "no_package_configured"


def test_request_hosting_rejects_unmapped_explicit_server(manager, client_obj, product):
    other_server = product_services.create_server(manager, {"name": "srv2", "hostname": "srv2.example.com"})
    with pytest.raises(ServiceError) as exc:
        services.request_hosting(manager, client_obj, product, "example.com", server=other_server)
    assert exc.value.code == "server_not_mapped"


def test_request_hosting_no_server_when_multiple_mapped(manager, client_obj, product):
    other_server = product_services.create_server(manager, {"name": "srv2", "hostname": "srv2.example.com"})
    product_services.set_product_servers(manager, product, [product.servers.first().pk, other_server.pk])
    account = services.request_hosting(manager, client_obj, product, "example.com")
    assert account.server is None


def test_assign_server_then_complete(manager, client_obj, product):
    other_server = product_services.create_server(manager, {"name": "srv2", "hostname": "srv2.example.com"})
    product_services.set_product_servers(manager, product, [product.servers.first().pk, other_server.pk])
    account = services.request_hosting(manager, client_obj, product, "example.com")
    assert account.server is None
    services.assign_server(manager, account, other_server)
    account.refresh_from_db()
    assert account.server == other_server
    account = services.complete_provisioning(manager, account)
    assert account.status == HostingStatus.ACTIVE


def test_complete_provisioning_requires_server_assigned(manager, client_obj, product):
    other_server = product_services.create_server(manager, {"name": "srv2", "hostname": "srv2.example.com"})
    product_services.set_product_servers(manager, product, [product.servers.first().pk, other_server.pk])
    account = services.request_hosting(manager, client_obj, product, "example.com")
    with pytest.raises(ServiceError) as exc:
        services.complete_provisioning(manager, account)
    assert exc.value.code == "no_server_assigned"


def test_complete_provisioning_requires_active_server(manager, client_obj, product, server):
    product_services.set_server_status(manager, server, ServerStatus.MAINTENANCE)
    account = services.request_hosting(manager, client_obj, product, "example.com")
    with pytest.raises(ServiceError) as exc:
        services.complete_provisioning(manager, account)
    assert exc.value.code == "server_not_active"


def test_complete_provisioning_is_idempotent(manager, client_obj, product):
    account = services.request_hosting(manager, client_obj, product, "example.com")
    first = services.complete_provisioning(manager, account)
    second = services.complete_provisioning(manager, first)
    assert second.status == HostingStatus.ACTIVE
    assert AuditEvent.objects.filter(action="hosting.provisioned").count() == 1


def test_complete_provisioning_sends_one_time_welcome_email(manager, client_obj, product):
    from django.core import mail

    mail.outbox.clear()  # client_obj's fixture setup already sent a contact-welcome email
    account = services.request_hosting(manager, client_obj, product, "example.com")
    services.complete_provisioning(manager, account)
    assert len(mail.outbox) == 1 and "hosting account" in mail.outbox[0].subject.lower()
    assert account.username in mail.outbox[0].body


def test_provisioning_failure_sets_failed_and_allows_retry(manager, client_obj, product, monkeypatch):
    from apps.hosting.adapters.base import HostingError
    from apps.hosting.adapters.manual import ManualAdapter

    account = services.request_hosting(manager, client_obj, product, "example.com")

    def boom(self, *a, **k):
        raise HostingError("server unreachable")

    monkeypatch.setattr(ManualAdapter, "create_account", boom)
    with pytest.raises(ServiceError) as exc:
        services.complete_provisioning(manager, account)
    assert exc.value.code == "adapter_error"
    account.refresh_from_db()
    assert account.status == HostingStatus.FAILED and "server unreachable" in account.last_error
    assert AuditEvent.objects.filter(action="hosting.provisioning_failed").exists()

    monkeypatch.undo()
    retried = services.complete_provisioning(manager, account)
    assert retried.status == HostingStatus.ACTIVE


def test_cancel_only_from_pending_or_failed(manager, client_obj, product):
    account = services.complete_provisioning(manager, services.request_hosting(manager, client_obj, product,
                                                                               "example.com"))
    with pytest.raises(ServiceError) as exc:
        services.cancel_request(manager, account)
    assert exc.value.code == "invalid_status"


def test_unique_live_username_allows_reuse_after_cancellation(manager, client_obj, product):
    account = services.request_hosting(manager, client_obj, product, "example.com")
    services.cancel_request(manager, account)
    again = services.request_hosting(manager, client_obj, product, "example.org")  # same base "example"
    assert again.username == "example"  # freed up since the cancelled row no longer holds it


# --- Lifecycle: suspend, unsuspend, terminate, change package -------------------------------

def test_suspend_requires_active(manager, client_obj, product):
    account = services.request_hosting(manager, client_obj, product, "example.com")
    with pytest.raises(ServiceError) as exc:
        services.suspend_account(manager, account, reason="test")
    assert exc.value.code == "invalid_status"


def test_suspend_unsuspend_round_trip_and_audit(manager, client_obj, product):
    account = services.complete_provisioning(manager, services.request_hosting(manager, client_obj, product,
                                                                               "example.com"))
    account = services.suspend_account(manager, account, reason="non-payment")
    assert account.status == HostingStatus.SUSPENDED and account.suspend_reason == "non-payment"
    account = services.unsuspend_account(manager, account)
    assert account.status == HostingStatus.ACTIVE and account.suspend_reason == ""
    actions = set(AuditEvent.objects.filter(target_id=str(account.pk)).values_list("action", flat=True))
    assert {"hosting.suspended", "hosting.unsuspended"} <= actions


def test_suspend_failure_persists_error_and_audit(manager, client_obj, product, monkeypatch):
    from apps.hosting.adapters.base import HostingError
    from apps.hosting.adapters.manual import ManualAdapter

    account = services.complete_provisioning(manager, services.request_hosting(manager, client_obj, product,
                                                                               "example.com"))
    monkeypatch.setattr(ManualAdapter, "suspend_account",
                        lambda self, *a, **k: (_ for _ in ()).throw(HostingError("timeout")))
    with pytest.raises(ServiceError):
        services.suspend_account(manager, account, reason="test")
    account.refresh_from_db()
    assert account.status == HostingStatus.ACTIVE  # unchanged - suspend never completed
    assert "timeout" in account.last_error
    assert AuditEvent.objects.filter(action="hosting.suspend_failed").exists()


def test_terminate_from_active_or_suspended(manager, client_obj, product):
    account = services.complete_provisioning(manager, services.request_hosting(manager, client_obj, product,
                                                                               "example.com"))
    account = services.terminate_account(manager, account)
    assert account.status == HostingStatus.TERMINATED


def test_terminate_rejected_from_pending(manager, client_obj, product):
    account = services.request_hosting(manager, client_obj, product, "example.com")
    with pytest.raises(ServiceError) as exc:
        services.terminate_account(manager, account)
    assert exc.value.code == "invalid_status"


def test_change_package_updates_snapshot_and_audits(manager, client_obj, product, server):
    account = services.complete_provisioning(manager, services.request_hosting(manager, client_obj, product,
                                                                               "example.com"))
    new_product = product_services.create_product(manager, {"name": "Pro", "type": ProductType.SHARED_HOSTING,
                                                            "whm_package_name": "pro_pkg"})
    account = services.change_package(manager, account, new_product)
    assert account.product == new_product and account.package_name == "pro_pkg"
    event = AuditEvent.objects.get(action="hosting.package_changed")
    assert event.metadata == {"from": "starter_pkg", "to": "pro_pkg"}


def test_change_package_requires_target_package_configured(manager, client_obj, product):
    account = services.complete_provisioning(manager, services.request_hosting(manager, client_obj, product,
                                                                               "example.com"))
    bare = product_services.create_product(manager, {"name": "Bare", "type": ProductType.VPS})
    with pytest.raises(ServiceError) as exc:
        services.change_package(manager, account, bare)
    assert exc.value.code == "no_package_configured"


# --- Sync ---------------------------------------------------------------------------------

def test_sync_status_and_usage(manager, client_obj, product):
    account = services.complete_provisioning(manager, services.request_hosting(manager, client_obj, product,
                                                                               "example.com"))
    synced = services.sync_status(manager, account)
    assert synced.status == HostingStatus.ACTIVE and synced.last_synced_at is not None
    usage = services.sync_usage(manager, account)
    assert usage.disk_used_mb is None  # Manual adapter reports "unknown", not zero


def test_sync_requires_manage_hosting(customer, manager, client_obj, product):
    account = services.complete_provisioning(manager, services.request_hosting(manager, client_obj, product,
                                                                               "example.com"))
    with pytest.raises(ServiceError):
        services.sync_status(customer, account)


# --- Visibility ---------------------------------------------------------------------------

def test_visible_hosting_accounts_for_user_scoping(manager, customer, client_obj, product, owner):
    account = services.request_hosting(manager, client_obj, product, "example.com")
    assert account in services.visible_hosting_accounts_for_user(manager)
    assert account not in services.visible_hosting_accounts_for_user(customer)
    assert services.visible_hosting_accounts_for_user(None).count() == 0
    assert account in services.visible_hosting_accounts_for_user(owner)


def test_search_hosting_accounts(manager, client_obj, product):
    services.request_hosting(manager, client_obj, product, "acme-example.com")
    other = client_services.create_client(manager, {"first_name": "B", "company_name": "Beta Corp",
                                                     "email": "b@beta.test"})
    services.request_hosting(manager, other, product, "other-site.net")
    assert services.search_hosting_accounts(HostingAccount.objects.all(), "acme").count() == 1
    assert services.search_hosting_accounts(HostingAccount.objects.all(), "beta").count() == 1
    assert services.search_hosting_accounts(HostingAccount.objects.all(), "").count() == 2
