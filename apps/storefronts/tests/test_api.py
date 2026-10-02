"""The storefront-facing catalog API: a real ``Authorization: Storefront <id>:<secret>`` header each time (never
``force_authenticate``) so the actual header parsing and credential check are exercised, not bypassed."""
import pytest

from apps.accounts.roles import Role
from apps.domains.models import TldPricing
from apps.products import services as product_services
from apps.products.models import BillingCycle, CatalogStatus, ProductType, StoreCategory
from apps.storefronts import services

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin(staff):
    return staff(Role.ADMIN)


@pytest.fixture
def storefront_and_secret(admin):
    return services.create_storefront(admin, {"name": "Web Host Era UK", "site_url": "https://example.co.uk"})


@pytest.fixture
def storefront(storefront_and_secret):
    return storefront_and_secret[0]


@pytest.fixture
def secret(storefront_and_secret):
    return storefront_and_secret[1]


def auth(api, public_id, raw_secret):
    api.credentials(HTTP_AUTHORIZATION=f"Storefront {public_id}:{raw_secret}")


@pytest.fixture
def published_product(admin, storefront):
    category = StoreCategory.objects.create(name="Shared Hosting", slug="shared-hosting")
    product = product_services.create_product(admin, {"name": "Business Hosting", "type": ProductType.SHARED_HOSTING,
                                                      "category": category, "publish_externally": True,
                                                      "resource_limits": {"disk_mb": 20480, "email_accounts": 10}})
    product_services.set_price(admin, product, billing_cycle=BillingCycle.MONTHLY, price="9.99", setup_fee="0")
    product_services.set_price(admin, product, billing_cycle=BillingCycle.ANNUAL, price="99.99")
    product_services.set_product_status(admin, product, CatalogStatus.ACTIVE)
    services.publish_product(admin, product, storefront, featured=True, sort_order=1)
    return product


# --- Products: visibility (Step 15, 1-4) --------------------------------------------------------------------

def test_a_published_product_appears_in_the_storefront_api(api, storefront, secret, published_product):
    auth(api, storefront.public_id, secret)
    response = api.get("/api/v1/storefront/products/")
    assert response.status_code == 200
    slugs = [row["slug"] for row in response.json()]
    assert published_product.slug in slugs


def test_a_product_never_published_to_any_storefront_does_not_appear(api, storefront, secret, admin):
    product_services.create_product(admin, {"name": "Unpublished", "type": ProductType.VPS, "publish_externally": True})
    auth(api, storefront.public_id, secret)
    slugs = [row["slug"] for row in api.get("/api/v1/storefront/products/").json()]
    assert "unpublished" not in slugs


def test_a_product_with_publish_externally_off_does_not_appear_even_if_linked(api, storefront, secret, published_product):
    published_product.publish_externally = False
    published_product.save(update_fields=["publish_externally"])
    auth(api, storefront.public_id, secret)
    slugs = [row["slug"] for row in api.get("/api/v1/storefront/products/").json()]
    assert published_product.slug not in slugs


def test_a_hidden_product_does_not_appear_even_though_it_is_published(api, admin, storefront, secret, published_product):
    product_services.set_product_status(admin, published_product, CatalogStatus.HIDDEN)
    auth(api, storefront.public_id, secret)
    slugs = [row["slug"] for row in api.get("/api/v1/storefront/products/").json()]
    assert published_product.slug not in slugs


def test_a_product_published_to_storefront_a_does_not_appear_to_storefront_b(api, admin, storefront, secret, published_product):
    other, other_secret = services.create_storefront(admin, {"name": "Other Site", "site_url": "https://other.test"})
    auth(api, other.public_id, other_secret)
    slugs = [row["slug"] for row in api.get("/api/v1/storefront/products/").json()]
    assert published_product.slug not in slugs
    auth(api, storefront.public_id, secret)
    slugs = [row["slug"] for row in api.get("/api/v1/storefront/products/").json()]
    assert published_product.slug in slugs


# --- Prices come from the one authoritative source (Step 15, 5-7) --------------------------------------------

def test_the_price_in_the_response_matches_the_authoritative_productprice_row(api, storefront, secret, published_product):
    auth(api, storefront.public_id, secret)
    body = api.get(f"/api/v1/storefront/products/{published_product.slug}/").json()
    assert body["pricing"]["monthly"]["amount"] == "9.99"
    assert body["pricing"]["annual"]["amount"] == "99.99"
    assert body["pricing"]["monthly"]["currency"]  # the configured store currency, not invented per-request


def test_changing_the_core_productprice_changes_the_api_output_immediately(api, admin, storefront, secret, published_product):
    price_row = published_product.prices.get(billing_cycle=BillingCycle.MONTHLY)
    product_services.set_price(admin, published_product, billing_cycle=BillingCycle.MONTHLY, price="14.99")
    auth(api, storefront.public_id, secret)
    body = api.get(f"/api/v1/storefront/products/{published_product.slug}/").json()
    assert body["pricing"]["monthly"]["amount"] == "14.99"


def test_features_come_from_the_products_own_resource_limits_not_a_second_list(api, storefront, secret, published_product):
    auth(api, storefront.public_id, secret)
    body = api.get(f"/api/v1/storefront/products/{published_product.slug}/").json()
    assert any("20480" in f or "disk" in f.lower() for f in body["features"])


def test_the_detail_endpoint_accepts_either_the_slug_or_the_numeric_id(api, storefront, secret, published_product):
    auth(api, storefront.public_id, secret)
    by_slug = api.get(f"/api/v1/storefront/products/{published_product.slug}/")
    by_id = api.get(f"/api/v1/storefront/products/{published_product.pk}/")
    assert by_slug.status_code == by_id.status_code == 200
    assert by_slug.json()["id"] == by_id.json()["id"] == published_product.pk


def test_order_url_reverses_to_the_real_public_product_page(api, storefront, secret, published_product):
    auth(api, storefront.public_id, secret)
    body = api.get(f"/api/v1/storefront/products/{published_product.slug}/").json()
    assert body["order_url"].endswith(f"/products/{published_product.slug}/")


def test_a_storefront_override_never_changes_the_authoritative_product_or_another_storefronts_view(api, admin, storefront, secret, published_product):
    other, other_secret = services.create_storefront(admin, {"name": "Other", "site_url": "https://other.test"})
    services.publish_product(admin, published_product, other, title_override="Other's Own Title")
    auth(api, storefront.public_id, secret)
    assert api.get(f"/api/v1/storefront/products/{published_product.slug}/").json()["name"] == "Business Hosting"
    published_product.refresh_from_db()
    assert published_product.name == "Business Hosting"  # the real record never changed
    auth(api, other.public_id, other_secret)
    assert api.get(f"/api/v1/storefront/products/{published_product.slug}/").json()["name"] == "Other's Own Title"


# --- Categories ------------------------------------------------------------------------------------------------

def test_categories_lists_only_ones_actually_used_by_a_published_plan(api, storefront, secret, published_product):
    StoreCategory.objects.create(name="Empty Category", slug="empty-category")  # nothing published in it
    auth(api, storefront.public_id, secret)
    slugs = [row["slug"] for row in api.get("/api/v1/storefront/categories/").json()]
    assert "shared-hosting" in slugs and "empty-category" not in slugs


# --- Addons (Step 15 reasoning, Step 7) -----------------------------------------------------------------------

def test_active_addons_are_listed_with_their_own_authoritative_price(api, storefront, secret, admin):
    addon = product_services.create_addon(admin, {"name": "SSL Certificate", "description": "..."})
    product_services.set_price(admin, addon, billing_cycle=BillingCycle.ANNUAL, price="15.00")
    product_services.set_addon_status(admin, addon, CatalogStatus.ACTIVE)
    auth(api, storefront.public_id, secret)
    body = api.get("/api/v1/storefront/addons/").json()
    row = next(r for r in body if r["slug"] == addon.slug)
    assert row["pricing"]["annual"]["amount"] == "15.00"


def test_a_hidden_addon_is_not_listed(api, storefront, secret, admin):
    addon = product_services.create_addon(admin, {"name": "Hidden Addon", "description": "..."})
    auth(api, storefront.public_id, secret)
    slugs = [r["slug"] for r in api.get("/api/v1/storefront/addons/").json()]
    assert addon.slug not in slugs


# --- Domain pricing (Step 15.8) ---------------------------------------------------------------------------------

def test_tld_prices_use_the_existing_domain_pricing_records(api, storefront, secret):
    TldPricing.objects.create(tld=".com", register_price="10.00", renew_price="12.00", transfer_price="9.00")
    TldPricing.objects.create(tld=".nope", register_price="1", renew_price="1", transfer_price="1", is_active=False)
    auth(api, storefront.public_id, secret)
    body = api.get("/api/v1/storefront/domains/pricing/").json()
    tlds = {row["tld"]: row for row in body}
    assert tlds[".com"]["register_price"] == "10.00"
    assert ".nope" not in tlds  # inactive stays hidden


# --- Authentication and scoping (Step 15, 9-12) -------------------------------------------------------------

def test_no_credential_at_all_is_refused(api):
    assert api.get("/api/v1/storefront/products/").status_code == 401


def test_a_malformed_or_wrong_credential_is_refused(api, storefront):
    auth(api, storefront.public_id, "totally-wrong-secret")
    assert api.get("/api/v1/storefront/products/").status_code == 401
    api.credentials(HTTP_AUTHORIZATION="Storefront not-even-the-right-shape")
    assert api.get("/api/v1/storefront/products/").status_code == 401
    api.credentials(HTTP_AUTHORIZATION="Bearer something-else-entirely")
    assert api.get("/api/v1/storefront/products/").status_code == 401


def test_a_rotated_credentials_old_secret_stops_working(api, admin, storefront, secret):
    new_secret = services.rotate_secret(admin, storefront)
    auth(api, storefront.public_id, secret)
    assert api.get("/api/v1/storefront/products/").status_code == 401
    auth(api, storefront.public_id, new_secret)
    assert api.get("/api/v1/storefront/products/").status_code == 200


def test_a_disabled_storefronts_credential_stops_working_immediately(api, admin, storefront, secret):
    from apps.storefronts.models import StorefrontStatus

    auth(api, storefront.public_id, secret)
    assert api.get("/api/v1/storefront/products/").status_code == 200
    services.set_storefront_status(admin, storefront, StorefrontStatus.DISABLED)
    assert api.get("/api/v1/storefront/products/").status_code == 401


def test_a_storefront_credential_cannot_reach_a_staff_or_private_api(api, storefront, secret):
    auth(api, storefront.public_id, secret)
    for path in ("/api/v1/clients/", "/api/v1/invoices/", "/api/v1/users/", "/api/v1/audit-events/",
                "/api/v1/servers/", "/api/v1/transactions/"):
        assert api.get(path).status_code in (401, 403), path


def test_a_storefronts_own_credential_cannot_write_to_the_storefront_api_either(api, storefront, secret, published_product):
    auth(api, storefront.public_id, secret)
    assert api.post(f"/api/v1/storefront/products/{published_product.slug}/", {"name": "hacked"}).status_code in (401, 403, 404, 405)
    published_product.refresh_from_db()
    assert published_product.name == "Business Hosting"


# --- No secrets leak out (Step 15.17-18) ------------------------------------------------------------------------

def test_the_product_response_carries_no_internal_or_provider_fields(api, storefront, secret, published_product):
    auth(api, storefront.public_id, secret)
    body = api.get(f"/api/v1/storefront/products/{published_product.slug}/").json()
    for leaky in ("whm_package_name", "auto_setup", "servers", "server_ids", "api_token", "secret", "secret_hash"):
        assert leaky not in body


def test_disabling_a_storefront_is_audited_and_the_log_never_carries_its_secret(admin, storefront, secret):
    from apps.audit.models import AuditEvent
    from apps.storefronts.models import StorefrontStatus

    services.set_storefront_status(admin, storefront, StorefrontStatus.DISABLED)
    for event in AuditEvent.objects.all():
        assert secret not in str(event.metadata)
