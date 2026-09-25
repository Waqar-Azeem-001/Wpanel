import pytest
from django.core.exceptions import ValidationError

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.core.exceptions import ServiceError
from apps.products import services
from apps.products.models import Addon, BillingCycle, CatalogStatus, Product, ProductType, Server

pytestmark = pytest.mark.django_db


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def product(manager):
    return services.create_product(manager, {"name": "Starter", "type": ProductType.SHARED_HOSTING})


def test_create_product_generates_unique_slug_and_defaults_hidden(manager):
    a = services.create_product(manager, {"name": "Pro Hosting", "type": ProductType.SHARED_HOSTING})
    b = services.create_product(manager, {"name": "Pro Hosting", "type": ProductType.SHARED_HOSTING})
    assert (a.slug, b.slug) == ("pro-hosting", "pro-hosting-2")
    assert a.status == CatalogStatus.HIDDEN
    assert AuditEvent.objects.filter(action="product.created", target_id=str(a.pk)).exists()


def test_create_product_requires_manage_permission(customer):
    with pytest.raises(ServiceError) as exc:
        services.create_product(customer, {"name": "X", "type": ProductType.VPS})
    assert exc.value.code == "permission_denied"
    assert not Product.objects.exists()


def test_resource_limits_validated(manager):
    with pytest.raises(ValidationError):
        services.create_product(manager, {
            "name": "Bad", "type": ProductType.VPS, "resource_limits": {"disk_mb": -1},
        })
    with pytest.raises(ValidationError):
        services.create_product(manager, {"name": "Bad2", "type": ProductType.VPS, "resource_limits": "nope"})
    ok = services.create_product(manager, {
        "name": "Good", "type": ProductType.VPS, "resource_limits": {"disk_mb": None, "unknown_key": "anything"},
    })
    assert ok.resource_limits == {"disk_mb": None, "unknown_key": "anything"}


def test_update_product_audits_changed_fields_only(manager, product):
    services.update_product(manager, product, {"name": "Starter", "whm_package_name": "starter_pkg"}, request=None)
    event = AuditEvent.objects.get(action="product.updated")
    assert event.metadata["fields"] == ["whm_package_name"]


def test_update_product_rename_keeps_slug(manager, product):
    services.update_product(manager, product, {"name": "Renamed Plan"})
    product.refresh_from_db()
    assert product.slug == "starter" and product.name == "Renamed Plan"


def test_set_product_status_transitions_and_audits(manager, product):
    services.set_product_status(manager, product, CatalogStatus.ACTIVE)
    product.refresh_from_db()
    assert product.status == CatalogStatus.ACTIVE
    event = AuditEvent.objects.get(action="product.status_changed")
    assert event.metadata == {"from": "hidden", "to": "active"}
    # No-op transition doesn't create a second event.
    services.set_product_status(manager, product, CatalogStatus.ACTIVE)
    assert AuditEvent.objects.filter(action="product.status_changed").count() == 1


def test_set_product_status_rejects_unknown_value(manager, product):
    with pytest.raises(ServiceError) as exc:
        services.set_product_status(manager, product, "deleted")
    assert exc.value.code == "invalid_status"


def test_set_product_servers(manager, product):
    s1 = Server.objects.create(name="s1", hostname="s1.example.com")
    s2 = Server.objects.create(name="s2", hostname="s2.example.com")
    services.set_product_servers(manager, product, [s1.pk, s2.pk])
    assert set(product.servers.values_list("name", flat=True)) == {"s1", "s2"}
    services.set_product_servers(manager, product, [s1.pk])
    assert list(product.servers.values_list("name", flat=True)) == ["s1"]


# --- Pricing ------------------------------------------------------------------------------

def test_set_price_creates_then_updates_same_row(manager, product):
    entry = services.set_price(manager, product, billing_cycle=BillingCycle.MONTHLY, price="9.99")
    assert product.prices.count() == 1 and entry.setup_fee == 0
    updated = services.set_price(manager, product, billing_cycle=BillingCycle.MONTHLY, price="12.00", setup_fee="5")
    assert product.prices.count() == 1
    assert updated.pk == entry.pk and str(updated.price) == "12.00"
    assert AuditEvent.objects.filter(action="product.price_added").count() == 1
    assert AuditEvent.objects.filter(action="product.price_changed").count() == 1


def test_custom_cycle_requires_months(manager, product):
    with pytest.raises(ValidationError):
        services.set_price(manager, product, billing_cycle=BillingCycle.CUSTOM, price="1")
    with pytest.raises(ValidationError):
        services.set_price(manager, product, billing_cycle=BillingCycle.MONTHLY, custom_months=4, price="1")
    entry = services.set_price(manager, product, billing_cycle=BillingCycle.CUSTOM, custom_months=4, price="30")
    assert entry.months == 4


def test_one_time_cycle_rejected_for_products(manager, product):
    with pytest.raises(ValidationError):
        services.set_price(manager, product, billing_cycle=BillingCycle.ONE_TIME, price="10")


def test_one_time_cycle_allowed_for_addons(manager):
    addon = services.create_addon(manager, {"name": "Migration"})
    entry = services.set_price(manager, addon, billing_cycle=BillingCycle.ONE_TIME, price="49")
    assert entry.months is None


def test_negative_price_rejected(manager, product):
    with pytest.raises(ValidationError):
        services.set_price(manager, product, billing_cycle=BillingCycle.MONTHLY, price="-1")


def test_get_effective_price_ignores_inactive(manager, product):
    entry = services.set_price(manager, product, billing_cycle=BillingCycle.MONTHLY, price="9.99")
    found = services.get_effective_price(product, BillingCycle.MONTHLY)
    assert found.pk == entry.pk
    services.set_price_active(manager, product, entry, False)
    with pytest.raises(ServiceError) as exc:
        services.get_effective_price(product, BillingCycle.MONTHLY)
    assert exc.value.code == "price_not_found"


def test_remove_price_audits_and_deletes(manager, product):
    entry = services.set_price(manager, product, billing_cycle=BillingCycle.ANNUAL, price="99")
    services.remove_price(manager, product, entry)
    assert product.prices.count() == 0
    assert AuditEvent.objects.filter(action="product.price_removed").exists()


# --- Addons -------------------------------------------------------------------------------

def test_create_and_update_addon(manager):
    addon = services.create_addon(manager, {"name": "SSL Certificate", "description": "Wildcard SSL"})
    assert addon.slug == "ssl-certificate" and addon.status == CatalogStatus.HIDDEN
    services.update_addon(manager, addon, {"name": "SSL Certificate", "description": "Updated"})
    addon.refresh_from_db()
    assert addon.description == "Updated"
    services.set_addon_status(manager, addon, CatalogStatus.ACTIVE)
    addon.refresh_from_db()
    assert addon.status == CatalogStatus.ACTIVE


def test_addon_requires_manage_permission(customer):
    with pytest.raises(ServiceError):
        services.create_addon(customer, {"name": "X"})


# --- Servers (manage_hosting, not manage_products) -----------------------------------------

def test_server_uses_hosting_permission_not_products(staff):
    support = staff(Role.SUPPORT_AGENT)  # has view_hosting but neither manage_hosting nor manage_products
    with pytest.raises(ServiceError):
        services.create_server(support, {"name": "s1", "hostname": "s1.example.com"})


def test_server_permission_is_manage_hosting_specifically_not_manage_products(make_user):
    from django.contrib.auth.models import Permission

    products_only = make_user("po@example.com")
    products_only.user_permissions.add(
        Permission.objects.get(codename="manage_products", content_type__app_label="accounts"))
    with pytest.raises(ServiceError):
        services.create_server(products_only, {"name": "s1", "hostname": "s1.example.com"})

    hosting_only = make_user("ho@example.com")
    hosting_only.user_permissions.add(
        Permission.objects.get(codename="manage_hosting", content_type__app_label="accounts"))
    server = services.create_server(hosting_only, {"name": "s2", "hostname": "s2.example.com"})
    assert server.status == "active"


def test_set_server_status(manager):
    server = services.create_server(manager, {"name": "s1", "hostname": "s1.example.com"})
    services.set_server_status(manager, server, "maintenance")
    server.refresh_from_db()
    assert server.status == "maintenance"
    with pytest.raises(ServiceError):
        services.set_server_status(manager, server, "bogus")


# --- Visibility ---------------------------------------------------------------------------

def test_visible_products_for_user_staff_vs_public(manager, customer, product):
    services.set_price(manager, product, billing_cycle=BillingCycle.MONTHLY, price="9.99")
    assert product in services.visible_products_for_user(manager)
    assert product not in services.visible_products_for_user(customer)  # still HIDDEN
    assert product not in services.visible_products_for_user(None)
    services.set_product_status(manager, product, CatalogStatus.ACTIVE)
    assert product in services.visible_products_for_user(customer)
    assert product in services.visible_products_for_user(None)


def test_search_catalog_matches_name_description_slug(manager):
    services.create_product(manager, {"name": "Business VPS", "type": ProductType.VPS, "description": "Fast NVMe"})
    services.create_product(manager, {"name": "Basic Shared", "type": ProductType.SHARED_HOSTING})
    assert services.search_catalog(Product.objects.all(), "nvme").count() == 1
    assert services.search_catalog(Product.objects.all(), "business").count() == 1
    assert services.search_catalog(Product.objects.all(), "").count() == 2


def test_set_price_with_a_malformed_cycle_is_a_validation_error_not_a_database_error(manager, product):
    """Regression: validate before writing - PostgreSQL rejects an over-long value at INSERT with a raw DB error."""
    with pytest.raises(ValidationError):
        services.set_price(manager, product, billing_cycle="x" * 40, price="1.00")
    assert product.prices.count() == 0
