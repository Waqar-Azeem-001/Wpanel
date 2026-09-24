import pytest

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.products import services
from apps.products.models import BillingCycle, CatalogStatus, ProductType, Server

pytestmark = pytest.mark.django_db


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def active_product(manager):
    product = services.create_product(manager, {"name": "Starter", "type": ProductType.SHARED_HOSTING})
    services.set_price(manager, product, billing_cycle=BillingCycle.MONTHLY, price="9.99")
    services.set_product_status(manager, product, CatalogStatus.ACTIVE)
    return product


# --- Public visibility --------------------------------------------------------------------

def test_anonymous_sees_only_active_products_with_public_shape(api, manager, active_product):
    services.create_product(manager, {"name": "Draft Plan", "type": ProductType.VPS})  # stays hidden
    response = api.get("/api/v1/products/")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    item = body["results"][0]
    assert item["name"] == "Starter"
    assert "whm_package_name" not in item and "resource_limits" in item
    assert item["prices"][0]["price"] == "9.99"


def test_anonymous_cannot_fetch_hidden_product_by_slug(api, manager):
    hidden = services.create_product(manager, {"name": "Hidden Plan", "type": ProductType.VPS})
    assert api.get(f"/api/v1/products/{hidden.slug}/").status_code == 404


def test_customer_gets_public_view_staff_gets_full_view(api, customer, manager, active_product):
    api.force_authenticate(customer)
    customer_view = api.get(f"/api/v1/products/{active_product.slug}/").json()
    assert "whm_package_name" not in customer_view

    api.force_authenticate(manager)
    staff_view = api.get(f"/api/v1/products/{active_product.slug}/").json()
    assert "whm_package_name" in staff_view and "servers" in staff_view


def test_anonymous_and_customer_cannot_write(api, customer, active_product):
    for auth_user in (None, customer):
        api.force_authenticate(auth_user)
        assert api.post("/api/v1/products/", {"name": "x", "type": "vps"}).status_code in (401, 403)
        assert api.patch(f"/api/v1/products/{active_product.slug}/", {"name": "hacked"}).status_code in (401, 403)
    active_product.refresh_from_db()
    assert active_product.name == "Starter"


# --- Staff CRUD ---------------------------------------------------------------------------

def test_manager_creates_product_with_servers(api, manager):
    server = Server.objects.create(name="srv1", hostname="srv1.example.com")
    api.force_authenticate(manager)
    response = api.post("/api/v1/products/", {
        "name": "Reseller Pro", "type": "reseller_hosting", "server_ids": [server.pk],
        "resource_limits": {"disk_mb": 20000},
    })
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["slug"] == "reseller-pro" and body["status"] == "hidden"
    assert body["servers"] == ["srv1"]


def test_create_rejects_invalid_type_and_bad_resource_limits(api, manager):
    api.force_authenticate(manager)
    assert api.post("/api/v1/products/", {"name": "x", "type": "bogus"}).status_code == 400
    response = api.post("/api/v1/products/", {"name": "x", "type": "vps", "resource_limits": {"disk_mb": -5}})
    assert response.status_code == 400


def test_support_agent_can_read_staff_shape_but_not_write(api, staff, active_product):
    api.force_authenticate(staff(Role.SUPPORT_AGENT))
    assert api.get("/api/v1/products/").status_code == 200
    # Support agents lack view_products in the role matrix, so they get the public shape, not full staff fields.
    body = api.get(f"/api/v1/products/{active_product.slug}/").json()
    assert "whm_package_name" not in body
    assert api.post("/api/v1/products/", {"name": "x", "type": "vps"}).status_code == 403


def test_update_and_status_change_are_audited(api, manager, active_product):
    api.force_authenticate(manager)
    response = api.patch(f"/api/v1/products/{active_product.slug}/", {"whm_package_name": "starter_v2"})
    assert response.status_code == 200 and response.json()["whm_package_name"] == "starter_v2"
    assert AuditEvent.objects.get(action="product.updated").metadata["fields"] == ["whm_package_name"]

    response = api.post(f"/api/v1/products/{active_product.slug}/status/", {"status": "retired"})
    assert response.status_code == 200 and response.json()["status"] == "retired"


def test_products_cannot_be_deleted(api, manager, active_product):
    api.force_authenticate(manager)  # even with full manage_products, there's no delete action
    assert api.delete(f"/api/v1/products/{active_product.slug}/").status_code == 405


# --- Pricing sub-resource -------------------------------------------------------------------

def test_price_list_hides_inactive_from_public(api, manager, active_product):
    inactive = services.set_price(manager, active_product, billing_cycle=BillingCycle.ANNUAL, price="99")
    services.set_price_active(manager, active_product, inactive, False)

    public = api.get(f"/api/v1/products/{active_product.slug}/prices/").json()
    assert len(public) == 1 and public[0]["billing_cycle"] == "monthly"

    api.force_authenticate(manager)
    staff_view = api.get(f"/api/v1/products/{active_product.slug}/prices/").json()
    assert len(staff_view) == 2


def test_add_and_remove_price_via_api(api, manager, active_product):
    api.force_authenticate(manager)
    added = api.post(f"/api/v1/products/{active_product.slug}/prices/", {"billing_cycle": "annual", "price": "99"})
    assert added.status_code == 201
    price_id = added.json()["id"]
    toggled = api.patch(f"/api/v1/products/{active_product.slug}/prices/{price_id}/", {"is_active": False})
    assert toggled.status_code == 200 and toggled.json()["is_active"] is False
    assert api.delete(f"/api/v1/products/{active_product.slug}/prices/{price_id}/").status_code == 204
    assert active_product.prices.filter(pk=price_id).count() == 0


def test_customer_cannot_add_price(api, customer, active_product):
    api.force_authenticate(customer)
    response = api.post(f"/api/v1/products/{active_product.slug}/prices/", {"billing_cycle": "annual", "price": "99"})
    assert response.status_code == 403


def test_one_time_price_rejected_for_product_via_api(api, manager, active_product):
    api.force_authenticate(manager)
    response = api.post(f"/api/v1/products/{active_product.slug}/prices/", {"billing_cycle": "one_time", "price": "5"})
    assert response.status_code == 400


# --- Addons ------------------------------------------------------------------------------

def test_addon_public_and_staff_crud(api, manager):
    addon = services.create_addon(manager, {"name": "Backups"})
    services.set_price(manager, addon, billing_cycle=BillingCycle.MONTHLY, price="2.99")
    services.set_addon_status(manager, addon, CatalogStatus.ACTIVE)

    assert api.get("/api/v1/addons/").json()["count"] == 1

    api.force_authenticate(manager)
    created = api.post("/api/v1/addons/", {"name": "Migration", "description": "One-off"})
    assert created.status_code == 201
    slug = created.json()["slug"]
    priced = api.post(f"/api/v1/addons/{slug}/prices/", {"billing_cycle": "one_time", "price": "49"})
    assert priced.status_code == 201 and priced.json()["months"] is None


def test_addon_status_change(api, manager):
    addon = services.create_addon(manager, {"name": "Extra Storage"})
    api.force_authenticate(manager)
    response = api.post(f"/api/v1/addons/{addon.slug}/status/", {"status": "active"})
    assert response.status_code == 200 and response.json()["status"] == "active"


# --- Servers: staff-only, no public visibility ----------------------------------------------

def test_servers_require_hosting_permission_and_are_never_public(api, customer, manager):
    assert api.get("/api/v1/servers/").status_code in (401, 403)
    api.force_authenticate(customer)
    assert api.get("/api/v1/servers/").status_code == 403
    api.force_authenticate(manager)
    response = api.post("/api/v1/servers/", {"name": "srv1", "hostname": "srv1.example.com"})
    assert response.status_code == 201 and response.json()["status"] == "active"


def test_support_agent_can_view_servers_but_not_manage(api, staff):
    server = Server.objects.create(name="srv1", hostname="h")
    api.force_authenticate(staff(Role.SUPPORT_AGENT))
    assert api.get("/api/v1/servers/").status_code == 200
    assert api.post(f"/api/v1/servers/{server.pk}/status/", {"status": "offline"}).status_code == 403


def test_server_status_change_audited(api, manager):
    server = Server.objects.create(name="srv1", hostname="h")
    api.force_authenticate(manager)
    response = api.post(f"/api/v1/servers/{server.pk}/status/", {"status": "maintenance"})
    assert response.status_code == 200
    assert AuditEvent.objects.filter(action="server.status_changed").exists()
