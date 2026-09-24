import pytest

from apps.accounts.roles import Role
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


# --- Public catalog (no login required) ------------------------------------------------

def test_public_list_shows_active_only(client, manager, active_product):
    services.create_product(manager, {"name": "Draft Plan", "type": ProductType.VPS})
    response = client.get("/products/")
    assert response.status_code == 200
    assert b"Starter" in response.content and b"Draft Plan" not in response.content


def test_public_detail_shows_pricing_and_hides_hidden_product(client, manager, active_product):
    response = client.get(f"/products/{active_product.slug}/")
    assert response.status_code == 200 and b"9.99" in response.content

    hidden = services.create_product(manager, {"name": "Hidden Plan", "type": ProductType.VPS})
    assert client.get(f"/products/{hidden.slug}/").status_code == 404


# --- Staff pages require login + permission ---------------------------------------------

def test_staff_pages_require_login_and_permission(client, customer, active_product):
    assert client.get("/staff/products/").status_code == 302
    client.force_login(customer)
    assert client.get("/staff/products/").status_code == 403
    assert client.get(f"/staff/products/{active_product.slug}/").status_code == 403
    assert client.get("/staff/products/new/").status_code == 403


def test_support_agent_denied_products_area(client, staff, active_product):
    # Support agents have neither view_products nor manage_products in the role matrix.
    client.force_login(staff(Role.SUPPORT_AGENT))
    assert client.get("/staff/products/").status_code == 403


def test_manager_can_view_but_not_create_without_manage_products(client, manager, active_product):
    # Manager holds manage_products, so both view and create should work.
    client.force_login(manager)
    assert client.get("/staff/products/").status_code == 200
    assert client.get(f"/staff/products/{active_product.slug}/").status_code == 200
    assert client.get("/staff/products/new/").status_code == 200


# --- Create / edit / status / pricing via web ---------------------------------------------

def test_create_edit_and_status_via_web(client, manager):
    client.force_login(manager)
    response = client.post("/staff/products/new/", {
        "name": "Web VPS", "type": "vps", "resource_limits": "", "auto_setup": "on", "default_auto_renew": "on",
    })
    from apps.products.models import Product

    product = Product.objects.get(name="Web VPS")
    assert response.status_code == 302 and response["Location"] == f"/staff/products/{product.slug}/"
    assert product.status == "hidden"

    client.post(f"/staff/products/{product.slug}/edit/", {
        "name": "Web VPS", "type": "vps", "whm_package_name": "vps_pkg",
        "resource_limits": '{"disk_mb": 10000}', "auto_setup": "on", "default_auto_renew": "on",
    })
    product.refresh_from_db()
    assert product.whm_package_name == "vps_pkg" and product.resource_limits == {"disk_mb": 10000}

    client.post(f"/staff/products/{product.slug}/status/", {"status": "active"})
    product.refresh_from_db()
    assert product.status == "active"


def test_invalid_resource_limits_json_shows_form_error(client, manager):
    client.force_login(manager)
    response = client.post("/staff/products/new/", {
        "name": "Bad JSON", "type": "vps", "resource_limits": "{not json}",
    })
    assert response.status_code == 200 and b"valid JSON" in response.content


def test_price_add_toggle_remove_via_web(client, manager, active_product):
    client.force_login(manager)
    client.post(f"/staff/products/{active_product.slug}/prices/", {
        "billing_cycle": "annual", "custom_months": "", "price": "99", "setup_fee": "",
    })
    entry = active_product.prices.get(billing_cycle="annual")
    client.post(f"/staff/products/{active_product.slug}/prices/{entry.pk}/toggle/")
    entry.refresh_from_db()
    assert entry.is_active is False
    client.post(f"/staff/products/{active_product.slug}/prices/{entry.pk}/remove/")
    assert not active_product.prices.filter(pk=entry.pk).exists()


def test_product_servers_mapping_via_web(client, manager, active_product):
    server = Server.objects.create(name="srv1", hostname="srv1.example.com")
    client.force_login(manager)
    client.post(f"/staff/products/{active_product.slug}/servers/", {"servers": [server.pk]})
    assert list(active_product.servers.values_list("name", flat=True)) == ["srv1"]


# --- Addons via web --------------------------------------------------------------------------

def test_addon_web_crud(client, manager):
    client.force_login(manager)
    response = client.post("/staff/addons/new/", {"name": "Web SSL", "description": "Wildcard"})
    from apps.products.models import Addon

    addon = Addon.objects.get(name="Web SSL")
    assert response.status_code == 302 and response["Location"] == f"/staff/addons/{addon.slug}/"

    client.post(f"/staff/addons/{addon.slug}/status/", {"status": "active"})
    addon.refresh_from_db()
    assert addon.status == "active"

    client.post(f"/staff/addons/{addon.slug}/prices/", {
        "billing_cycle": "one_time", "custom_months": "", "price": "49", "setup_fee": "",
    })
    assert addon.prices.get().billing_cycle == "one_time"


# --- Servers via web ---------------------------------------------------------------------------

def test_server_web_requires_manage_hosting(client, staff, manager):
    support = staff(Role.SUPPORT_AGENT)
    client.force_login(support)
    assert client.get("/staff/servers/").status_code == 200  # has view_hosting
    assert client.get("/staff/servers/new/").status_code == 403  # lacks manage_hosting

    client.force_login(manager)
    response = client.post("/staff/servers/new/", {"name": "srv2", "hostname": "srv2.example.com"})
    server = Server.objects.get(name="srv2")
    assert response.status_code == 302 and response["Location"] == "/staff/servers/"

    client.post(f"/staff/servers/{server.pk}/status/", {"status": "maintenance"})
    server.refresh_from_db()
    assert server.status == "maintenance"


def test_nav_shows_products_link_only_with_permission(client, manager, customer):
    client.force_login(manager)
    assert b"/staff/products/" in client.get("/account/profile/").content
    client.force_login(customer)
    assert b"/staff/products/" not in client.get("/account/profile/").content
    assert b"/products/" in client.get("/account/profile/").content  # public plans link always shown
