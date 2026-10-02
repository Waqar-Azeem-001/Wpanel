import pytest
from django.contrib.auth.hashers import check_password

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.core.exceptions import ServiceError
from apps.products import services as product_services
from apps.products.models import BillingCycle, CatalogStatus, ProductType
from apps.storefronts import services
from apps.storefronts.models import ExternalStorefront, StorefrontProduct, StorefrontStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin(staff):
    return staff(Role.ADMIN)


@pytest.fixture
def manager(staff):
    """Has manage_products but not view_providers/manage_providers - the storefront/credential half of this app
    must refuse this role even though the product-publishing half allows it."""
    return staff(Role.MANAGER)


@pytest.fixture
def storefront(admin):
    row, _secret = services.create_storefront(admin, {"name": "Web Host Era UK", "site_url": "https://example.co.uk"})
    return row


@pytest.fixture
def published_product(admin):
    product = product_services.create_product(admin, {"name": "Business Hosting", "type": ProductType.SHARED_HOSTING,
                                                      "publish_externally": True})
    product_services.set_price(admin, product, billing_cycle=BillingCycle.MONTHLY, price="9.99")
    product_services.set_product_status(admin, product, CatalogStatus.ACTIVE)
    return product


# --- Creating a storefront and its credential ---------------------------------------------------------------------

def test_creating_a_storefront_returns_the_raw_secret_once_and_never_stores_it(admin):
    storefront, raw_secret = services.create_storefront(admin, {"name": "Web Host Era KSA", "site_url": "https://example.sa"})
    assert len(raw_secret) >= 32  # a real random secret, not a placeholder
    assert raw_secret not in storefront.secret_hash
    assert check_password(raw_secret, storefront.secret_hash)  # the one-way hash verifies it
    assert storefront.public_id and storefront.public_id != raw_secret
    assert AuditEvent.objects.filter(action="storefront.created", target_id=str(storefront.pk)).exists()


def test_creating_a_storefront_needs_manage_providers(manager):
    with pytest.raises(ServiceError) as caught:
        services.create_storefront(manager, {"name": "x", "site_url": "https://x.test"})
    assert caught.value.code == "permission_denied"


def test_two_storefronts_never_share_a_public_id_or_a_secret(admin):
    one, secret_one = services.create_storefront(admin, {"name": "A", "site_url": "https://a.test"})
    two, secret_two = services.create_storefront(admin, {"name": "B", "site_url": "https://b.test"})
    assert one.public_id != two.public_id and secret_one != secret_two


# --- Authenticating -------------------------------------------------------------------------------------------

def test_authenticate_storefront_accepts_the_right_credential_and_records_last_used(admin):
    storefront, secret = services.create_storefront(admin, {"name": "x", "site_url": "https://x.test"})
    assert storefront.last_accessed_at is None
    found = services.authenticate_storefront(storefront.public_id, secret)
    assert found is not None and found.pk == storefront.pk
    storefront.refresh_from_db()
    assert storefront.last_accessed_at is not None


def test_authenticate_storefront_rejects_a_wrong_secret_an_unknown_id_and_blanks(admin):
    storefront, secret = services.create_storefront(admin, {"name": "x", "site_url": "https://x.test"})
    assert services.authenticate_storefront(storefront.public_id, "wrong-secret") is None
    assert services.authenticate_storefront("unknown-id", secret) is None
    assert services.authenticate_storefront("", "") is None
    assert services.authenticate_storefront(storefront.public_id, "") is None


def test_disabling_a_storefront_immediately_stops_its_credential_working(admin):
    storefront, secret = services.create_storefront(admin, {"name": "x", "site_url": "https://x.test"})
    assert services.authenticate_storefront(storefront.public_id, secret) is not None
    services.set_storefront_status(admin, storefront, StorefrontStatus.DISABLED)
    assert services.authenticate_storefront(storefront.public_id, secret) is None
    assert AuditEvent.objects.filter(action="storefront.status_changed", target_id=str(storefront.pk)).exists()


def test_rotating_the_secret_invalidates_the_old_one_immediately(admin):
    storefront, old_secret = services.create_storefront(admin, {"name": "x", "site_url": "https://x.test"})
    new_secret = services.rotate_secret(admin, storefront)
    assert new_secret != old_secret
    assert services.authenticate_storefront(storefront.public_id, old_secret) is None
    assert services.authenticate_storefront(storefront.public_id, new_secret) is not None
    assert AuditEvent.objects.filter(action="storefront.credential_rotated", target_id=str(storefront.pk)).exists()


def test_rotating_or_disabling_needs_manage_providers(manager, storefront):
    with pytest.raises(ServiceError):
        services.rotate_secret(manager, storefront)
    with pytest.raises(ServiceError):
        services.set_storefront_status(manager, storefront, StorefrontStatus.DISABLED)


def test_no_audit_event_ever_carries_the_raw_secret(admin):
    storefront, raw_secret = services.create_storefront(admin, {"name": "x", "site_url": "https://x.test"})
    services.rotate_secret(admin, storefront)
    for event in AuditEvent.objects.filter(target_id=str(storefront.pk)):
        assert raw_secret not in str(event.metadata)


# --- Publishing a plan to a storefront --------------------------------------------------------------------------

def test_publishing_and_unpublishing_a_product_is_audited(admin, storefront, published_product):
    services.publish_product(admin, published_product, storefront)
    assert StorefrontProduct.objects.filter(storefront=storefront, product=published_product).exists()
    assert AuditEvent.objects.filter(action="storefront.product_published").exists()
    services.unpublish_product(admin, published_product, storefront)
    assert not StorefrontProduct.objects.filter(storefront=storefront, product=published_product).exists()
    assert AuditEvent.objects.filter(action="storefront.product_unpublished").exists()


def test_republishing_with_the_same_overrides_is_not_audited_again(admin, storefront, published_product):
    services.publish_product(admin, published_product, storefront, featured=True, sort_order=5)
    before = AuditEvent.objects.filter(action="storefront.product_updated").count()
    services.publish_product(admin, published_product, storefront, featured=True, sort_order=5)  # nothing changed
    assert AuditEvent.objects.filter(action="storefront.product_updated").count() == before


def test_set_product_storefronts_publishes_the_new_ones_and_unpublishes_the_dropped_ones(admin, published_product):
    one, _ = services.create_storefront(admin, {"name": "One", "site_url": "https://one.test"})
    two, _ = services.create_storefront(admin, {"name": "Two", "site_url": "https://two.test"})
    services.set_product_storefronts(admin, published_product, [one.pk, two.pk])
    assert set(StorefrontProduct.objects.filter(product=published_product).values_list("storefront_id", flat=True)) == {one.pk, two.pk}
    services.set_product_storefronts(admin, published_product, [one.pk])  # drop "two"
    assert set(StorefrontProduct.objects.filter(product=published_product).values_list("storefront_id", flat=True)) == {one.pk}


def test_products_for_only_returns_what_this_storefront_was_given(admin, published_product):
    one, _ = services.create_storefront(admin, {"name": "One", "site_url": "https://one.test"})
    two, _ = services.create_storefront(admin, {"name": "Two", "site_url": "https://two.test"})
    services.publish_product(admin, published_product, one)
    assert list(services.products_for(one)) == [published_product]
    assert list(services.products_for(two)) == []  # published to "one" only


def test_products_for_respects_the_global_publish_externally_switch(admin, storefront, published_product):
    services.publish_product(admin, published_product, storefront)
    assert list(services.products_for(storefront)) == [published_product]
    published_product.publish_externally = False
    published_product.save(update_fields=["publish_externally"])
    assert list(services.products_for(storefront)) == []  # linked, but the master switch is off


def test_products_for_respects_the_products_own_active_status(admin, storefront, published_product):
    services.publish_product(admin, published_product, storefront)
    product_services.set_product_status(admin, published_product, CatalogStatus.HIDDEN)
    assert list(services.products_for(storefront)) == []
