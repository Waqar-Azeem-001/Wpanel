import pytest

from apps.accounts.roles import Role
from apps.clients import services as client_services
from apps.hosting import services
from apps.products import services as product_services
from apps.products.models import ProductType

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
    product_services.set_product_status(manager, p, "active")
    return p


@pytest.fixture
def client_obj(manager):
    return client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test"})


@pytest.fixture
def owner(client_obj):
    return client_obj.contacts.get().user


def test_customer_pages_require_login(client):
    assert client.get("/account/hosting/").status_code == 302
    assert client.get("/account/hosting/1/").status_code == 302


def test_the_request_without_paying_page_is_gone(client, owner, product):
    from apps.hosting.models import HostingAccount

    client.force_login(owner)
    assert client.get("/account/hosting/request/").status_code == 404
    assert client.post("/account/hosting/request/", {"product": product.pk, "domain": "example.com"}).status_code == 404
    assert not HostingAccount.objects.exists()
    assert b"/account/hosting/request/" not in client.get("/account/hosting/").content


def test_customer_list_and_detail(client, owner, manager, client_obj, product):
    services.request_hosting(manager, client_obj, product, "example.com")
    client.force_login(owner)
    listing = client.get("/account/hosting/")
    assert b"example.com" in listing.content
    account = client_obj.hosting_accounts.get()
    detail = client.get(f"/account/hosting/{account.pk}/")
    assert detail.status_code == 200 and b"example.com" in detail.content


def test_customer_cannot_view_other_clients_account(client, customer, manager, client_obj, product):
    account = services.request_hosting(manager, client_obj, product, "example.com")
    client.force_login(customer)
    assert client.get(f"/account/hosting/{account.pk}/").status_code == 404


def test_staff_pages_require_permission(client, customer, manager, client_obj, product):
    account = services.request_hosting(manager, client_obj, product, "example.com")
    assert client.get("/staff/hosting/").status_code == 302
    client.force_login(customer)
    assert client.get("/staff/hosting/").status_code == 403
    assert client.get(f"/staff/hosting/{account.pk}/").status_code == 403


def test_support_agent_can_view_but_not_complete(client, staff, manager, client_obj, product):
    account = services.request_hosting(manager, client_obj, product, "example.com")
    client.force_login(staff(Role.SUPPORT_AGENT))
    assert client.get(f"/staff/hosting/{account.pk}/").status_code == 200
    assert client.post(f"/staff/hosting/{account.pk}/complete/").status_code == 403


def test_staff_full_lifecycle_via_web(client, manager, client_obj, product):
    client.force_login(manager)
    account = services.request_hosting(manager, client_obj, product, "example.com")

    client.post(f"/staff/hosting/{account.pk}/complete/")
    account.refresh_from_db()
    assert account.status == "active"

    client.post(f"/staff/hosting/{account.pk}/suspend/", {"reason": "non-payment"})
    account.refresh_from_db()
    assert account.status == "suspended"

    client.post(f"/staff/hosting/{account.pk}/unsuspend/")
    account.refresh_from_db()
    assert account.status == "active"

    client.post(f"/staff/hosting/{account.pk}/sync-status/")
    client.post(f"/staff/hosting/{account.pk}/sync-usage/")

    new_product = product_services.create_product(manager, {"name": "Pro", "type": ProductType.SHARED_HOSTING,
                                                            "whm_package_name": "pro_pkg"})
    client.post(f"/staff/hosting/{account.pk}/change-package/", {"product": new_product.pk})
    account.refresh_from_db()
    assert account.package_name == "pro_pkg"

    client.post(f"/staff/hosting/{account.pk}/terminate/", {})
    account.refresh_from_db()
    assert account.status == "terminated"

    listing = client.get("/staff/hosting/?q=example")
    assert b"example.com" in listing.content


def test_staff_cancel_pending_request(client, manager, client_obj, product):
    account = services.request_hosting(manager, client_obj, product, "example.com")
    client.force_login(manager)
    client.post(f"/staff/hosting/{account.pk}/cancel/", {"reason": "duplicate"})
    account.refresh_from_db()
    assert account.status == "cancelled"


def test_staff_assign_server_when_multiple_mapped(client, manager, client_obj, product):
    other_server = product_services.create_server(manager, {"name": "srv2", "hostname": "srv2.example.com"})
    product_services.set_product_servers(manager, product, [product.servers.first().pk, other_server.pk])
    account = services.request_hosting(manager, client_obj, product, "example.com")
    assert account.server is None

    client.force_login(manager)
    detail = client.get(f"/staff/hosting/{account.pk}/")
    assert b"Assign a server" in detail.content
    client.post(f"/staff/hosting/{account.pk}/assign-server/", {"server": other_server.pk})
    account.refresh_from_db()
    assert account.server == other_server


def test_nav_shows_hosting_links_by_role(client, manager, customer):
    client.force_login(manager)
    assert b"/staff/hosting/" in client.get("/account/profile/").content
    client_services.create_client_for_registration(customer)
    client.force_login(customer)
    content = client.get("/account/profile/").content
    assert b"/staff/hosting/" not in content and b"/account/hosting/" in content
