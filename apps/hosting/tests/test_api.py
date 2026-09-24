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
    return p


@pytest.fixture
def client_obj(manager):
    return client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test"})


@pytest.fixture
def owner(client_obj):
    return client_obj.contacts.get().user


def test_endpoints_require_authentication(api):
    assert api.get("/api/v1/hosting-accounts/").status_code == 401


def test_customer_sees_only_own_accounts(api, manager, client_obj, owner, product):
    mine = services.request_hosting(manager, client_obj, product, "mine.com")
    other = client_services.create_client(manager, {"first_name": "B", "email": "b@x.test"})
    services.request_hosting(manager, other, product, "other.com")

    api.force_authenticate(owner)
    body = api.get("/api/v1/hosting-accounts/").json()
    assert [a["id"] for a in body["results"]] == [mine.pk]
    assert api.get(f"/api/v1/hosting-accounts/{mine.pk}/").status_code == 200


def test_staff_sees_all_accounts(api, manager, client_obj, product):
    services.request_hosting(manager, client_obj, product, "example.com")
    api.force_authenticate(manager)
    assert api.get("/api/v1/hosting-accounts/").json()["count"] == 1


def test_stranger_gets_404(api, customer, manager, client_obj, product, make_user):
    account = services.request_hosting(manager, client_obj, product, "example.com")
    stranger = make_user("stranger@example.com")
    api.force_authenticate(stranger)
    assert api.get(f"/api/v1/hosting-accounts/{account.pk}/").status_code == 404


def test_request_account_by_owner_and_staff(api, manager, owner, client_obj, product):
    api.force_authenticate(owner)
    response = api.post("/api/v1/hosting-accounts/request_account/", {
        "client_id": client_obj.pk, "product_id": product.pk, "domain": "example.com",
    })
    assert response.status_code == 201 and response.json()["status"] == "pending"

    other = client_services.create_client(manager, {"first_name": "B", "email": "b@x.test"})
    api.force_authenticate(manager)
    response = api.post("/api/v1/hosting-accounts/request_account/", {
        "client_id": other.pk, "product_id": product.pk, "domain": "second.com",
    })
    assert response.status_code == 201


def test_request_denied_for_non_contact(api, customer, client_obj, product):
    api.force_authenticate(customer)
    response = api.post("/api/v1/hosting-accounts/request_account/", {
        "client_id": client_obj.pk, "product_id": product.pk, "domain": "example.com",
    })
    assert response.status_code == 403


def test_complete_requires_manage_hosting(api, owner, manager, client_obj, product):
    account = services.request_hosting(manager, client_obj, product, "example.com")
    api.force_authenticate(owner)
    assert api.post(f"/api/v1/hosting-accounts/{account.pk}/complete/").status_code == 403
    api.force_authenticate(manager)
    response = api.post(f"/api/v1/hosting-accounts/{account.pk}/complete/")
    assert response.status_code == 200 and response.json()["status"] == "active"


def test_support_agent_can_view_but_not_manage(api, staff, manager, client_obj, product):
    account = services.request_hosting(manager, client_obj, product, "example.com")
    api.force_authenticate(staff(Role.SUPPORT_AGENT))
    assert api.get(f"/api/v1/hosting-accounts/{account.pk}/").status_code == 200
    assert api.post(f"/api/v1/hosting-accounts/{account.pk}/complete/").status_code == 403


def test_suspend_unsuspend_terminate_via_api(api, manager, client_obj, product):
    account = services.complete_provisioning(
        manager, services.request_hosting(manager, client_obj, product, "example.com"))
    api.force_authenticate(manager)
    suspended = api.post(f"/api/v1/hosting-accounts/{account.pk}/suspend/", {"reason": "test"})
    assert suspended.status_code == 200 and suspended.json()["status"] == "suspended"
    unsuspended = api.post(f"/api/v1/hosting-accounts/{account.pk}/unsuspend/")
    assert unsuspended.status_code == 200 and unsuspended.json()["status"] == "active"
    terminated = api.post(f"/api/v1/hosting-accounts/{account.pk}/terminate/", {"keep_dns": True})
    assert terminated.status_code == 200 and terminated.json()["status"] == "terminated"


def test_change_package_via_api(api, manager, client_obj, product):
    account = services.complete_provisioning(
        manager, services.request_hosting(manager, client_obj, product, "example.com"))
    new_product = product_services.create_product(manager, {"name": "Pro", "type": ProductType.SHARED_HOSTING,
                                                            "whm_package_name": "pro_pkg"})
    api.force_authenticate(manager)
    response = api.post(f"/api/v1/hosting-accounts/{account.pk}/change-package/", {"product_id": new_product.pk})
    assert response.status_code == 200 and response.json()["package_name"] == "pro_pkg"


def test_assign_server_via_api(api, manager, client_obj, product):
    other_server = product_services.create_server(manager, {"name": "srv2", "hostname": "srv2.example.com"})
    product_services.set_product_servers(manager, product, [product.servers.first().pk, other_server.pk])
    account = services.request_hosting(manager, client_obj, product, "example.com")
    assert account.server is None

    api.force_authenticate(manager)
    response = api.post(f"/api/v1/hosting-accounts/{account.pk}/assign-server/", {"server_id": other_server.pk})
    assert response.status_code == 200 and response.json()["server"] == other_server.pk


def test_sync_status_and_usage_via_api(api, manager, client_obj, product):
    account = services.complete_provisioning(
        manager, services.request_hosting(manager, client_obj, product, "example.com"))
    api.force_authenticate(manager)
    assert api.post(f"/api/v1/hosting-accounts/{account.pk}/sync-status/").status_code == 200
    assert api.post(f"/api/v1/hosting-accounts/{account.pk}/sync-usage/").status_code == 200


def test_cancel_via_api(api, manager, client_obj, product):
    account = services.request_hosting(manager, client_obj, product, "example.com")
    api.force_authenticate(manager)
    response = api.post(f"/api/v1/hosting-accounts/{account.pk}/cancel/", {"reason": "duplicate"})
    assert response.status_code == 200 and response.json()["status"] == "cancelled"


def test_server_api_accepts_whm_fields_and_never_echoes_token(api, manager):
    api.force_authenticate(manager)
    response = api.post("/api/v1/servers/", {
        "name": "srv-whm", "hostname": "whm.example.com", "kind": "whm_api", "api_username": "root",
        "api_token": "super-secret",
    })
    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "whm_api" and "api_token" not in body and "super-secret" not in str(body)

    from apps.products.models import Server

    server = Server.objects.get(pk=body["id"])
    assert server.get_api_token() == "super-secret"
