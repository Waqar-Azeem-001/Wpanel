"""Deleting the record of a finished hosting account (failed, cancelled, terminated): only WHPanel's record goes, never anything on a server."""
import pytest

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.core.exceptions import ServiceError
from apps.hosting import services
from apps.hosting.models import HostingAccount, HostingStatus
from apps.products import services as product_services
from apps.products.models import Product, ProductType, Server, ServerKind

pytestmark = pytest.mark.django_db


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def setup(manager):
    client = client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test"})
    product = Product.objects.get(pk=product_services.create_product(manager, {"name": "Starter", "type": ProductType.SHARED_HOSTING}).pk)
    server = Server.objects.create(name="cp", hostname="whm.test", kind=ServerKind.WHM_API)
    return client, product, server


def make(setup, status, domain="gone.com", username="gone"):
    client, product, server = setup
    return HostingAccount.objects.create(client=client, product=product, server=server, domain=domain, username=username, status=status)


@pytest.mark.parametrize("status", [HostingStatus.FAILED, HostingStatus.CANCELLED, HostingStatus.TERMINATED])
def test_a_finished_accounts_record_can_be_deleted_and_is_audited(manager, setup, status, monkeypatch):
    called = []
    monkeypatch.setattr("apps.hosting.services.get_adapter", lambda server: called.append(server))  # no server is ever contacted
    account = make(setup, status)
    services.delete_dead_record(manager, account)
    assert not HostingAccount.objects.filter(pk=account.pk).exists() and called == []
    event = AuditEvent.objects.get(action="hosting.record_deleted")
    assert event.actor == manager and event.metadata["domain"] == "gone.com" and event.metadata["status"] == status and event.metadata["server"] == "cp"


@pytest.mark.parametrize("status", [HostingStatus.PENDING, HostingStatus.ACTIVE, HostingStatus.SUSPENDED])
def test_a_live_or_pending_account_is_never_deleted(manager, setup, status):
    account = make(setup, status)
    with pytest.raises(ServiceError) as refused:
        services.delete_dead_record(manager, account)
    assert refused.value.code == "invalid_status" and HostingAccount.objects.filter(pk=account.pk).exists()


def test_a_record_that_an_order_still_points_at_is_kept(manager, setup):
    from apps.orders.models import Order, OrderItem

    client, product, _ = setup
    account = make(setup, HostingStatus.FAILED)
    order = Order.objects.create(client=client, currency="USD", subtotal=10, total=10)
    OrderItem.objects.create(order=order, kind="hosting", description="Hosting", product=product, hosting_account=account, unit_price=10, line_total=10)
    with pytest.raises(ServiceError) as refused:
        services.delete_dead_record(manager, account)
    assert refused.value.code == "record_in_use" and HostingAccount.objects.filter(pk=account.pk).exists()


def test_only_staff_who_manage_hosting_may_delete(staff, setup):
    account = make(setup, HostingStatus.FAILED)
    with pytest.raises(ServiceError):
        services.delete_dead_record(staff(Role.SUPPORT_AGENT), account)
    assert HostingAccount.objects.filter(pk=account.pk).exists()


def test_the_staff_page_shows_the_delete_button_only_for_finished_accounts(client, manager, setup):
    dead, live = make(setup, HostingStatus.FAILED), make(setup, HostingStatus.ACTIVE, "live.com", "live")
    client.force_login(manager)
    assert "Delete record" in client.get(f"/staff/hosting/{dead.pk}/").content.decode()
    assert "Delete this record" not in client.get(f"/staff/hosting/{live.pk}/").content.decode()
    response = client.post(f"/staff/hosting/{dead.pk}/delete-record/", follow=True)
    assert "Nothing was changed on the server" in response.content.decode() and not HostingAccount.objects.filter(pk=dead.pk).exists()
    refused = client.post(f"/staff/hosting/{live.pk}/delete-record/", follow=True).content.decode()
    assert "Cancel or terminate it first" in refused and HostingAccount.objects.filter(pk=live.pk).exists()


def test_the_bulk_button_deletes_the_finished_ones_and_leaves_the_rest(client, manager, setup):
    a = make(setup, HostingStatus.FAILED, "a.com", "a1")
    b = make(setup, HostingStatus.CANCELLED, "b.com", "b1")
    c = make(setup, HostingStatus.ACTIVE, "c.com", "c1")
    client.force_login(manager)
    response = client.post("/staff/hosting/bulk/", {"do": "delete", "ids": [a.pk, b.pk, c.pk]}, follow=True)
    text = response.content.decode()
    assert "2 record(s) deleted" in text and "c.com" in text
    assert list(HostingAccount.objects.values_list("domain", flat=True)) == ["c.com"]
    assert "Delete record" in client.get("/staff/hosting/").content.decode()


def test_customers_cannot_reach_it(client, customer, setup):
    account = make(setup, HostingStatus.FAILED)
    client.force_login(customer)
    assert client.post(f"/staff/hosting/{account.pk}/delete-record/").status_code in (302, 403, 404)
    assert HostingAccount.objects.filter(pk=account.pk).exists()
