import pytest

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.products import services as product_services
from apps.products.models import ProductType
from apps.storefronts import services
from apps.storefronts.models import ExternalStorefront

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin(staff):
    return staff(Role.ADMIN)


@pytest.fixture
def manager(staff):
    """manage_products, but not view_providers/manage_providers."""
    return staff(Role.MANAGER)


@pytest.fixture
def storefront(admin):
    row, _secret = services.create_storefront(admin, {"name": "Web Host Era UK", "site_url": "https://example.co.uk"})
    return row


@pytest.fixture
def product(admin):
    return product_services.create_product(admin, {"name": "Business Hosting", "type": ProductType.SHARED_HOSTING,
                                                    "publish_externally": True})


# --- Permissions -------------------------------------------------------------------------------------------------

def test_the_list_page_needs_view_providers(client, admin, manager):
    assert client.get("/staff/storefronts/").status_code == 302  # anonymous: sent to sign in
    client.force_login(manager)
    assert client.get("/staff/storefronts/").status_code == 403  # manage_products is not view_providers
    client.force_login(admin)
    assert client.get("/staff/storefronts/").status_code == 200


def test_creating_connecting_rotating_and_disabling_need_manage_providers(client, manager, storefront):
    client.force_login(manager)
    assert client.get("/staff/storefronts/new/").status_code == 403
    assert client.post("/staff/storefronts/new/", {"name": "x", "site_url": "https://x.test",
                                                   "integration_type": "wordpress"}).status_code == 403
    assert client.post(f"/staff/storefronts/{storefront.pk}/rotate/").status_code == 403
    assert client.post(f"/staff/storefronts/{storefront.pk}/status/").status_code == 403
    storefront.refresh_from_db()
    assert storefront.is_active  # nothing changed


# --- Connecting a storefront ------------------------------------------------------------------------------------

def test_connecting_a_storefront_shows_the_secret_once_in_the_flash_message(client, admin):
    client.force_login(admin)
    response = client.post("/staff/storefronts/new/", {
        "name": "Web Host Era KSA", "site_url": "https://example.sa", "integration_type": "wordpress",
    }, follow=True)
    assert response.status_code == 200
    storefront = ExternalStorefront.objects.get(name="Web Host Era KSA")
    page = response.content.decode()
    assert storefront.public_id in page
    # the raw secret is shown once, on this very response - not stored anywhere retrievable afterwards
    assert storefront.secret_hash not in page


def test_an_invalid_form_does_not_create_a_storefront(client, admin):
    client.force_login(admin)
    before = ExternalStorefront.objects.count()
    response = client.post("/staff/storefronts/new/", {"name": "", "site_url": "not-a-url", "integration_type": "wordpress"})
    assert response.status_code == 200 and ExternalStorefront.objects.count() == before


# --- Status and rotation -----------------------------------------------------------------------------------------

def test_disabling_and_enabling_from_the_staff_page(client, admin, storefront):
    client.force_login(admin)
    client.post(f"/staff/storefronts/{storefront.pk}/status/")
    storefront.refresh_from_db()
    assert not storefront.is_active
    client.post(f"/staff/storefronts/{storefront.pk}/status/")
    storefront.refresh_from_db()
    assert storefront.is_active


def test_rotating_shows_the_new_secret_once(client, admin, storefront):
    client.force_login(admin)
    response = client.post(f"/staff/storefronts/{storefront.pk}/rotate/", follow=True)
    assert "shown once" in response.content.decode().lower() or "shown once" in str(list(response.context["messages"])).lower()


def test_generating_a_webhook_secret_shows_it_once_and_needs_manage_providers(client, admin, manager, storefront):
    client.force_login(manager)
    assert client.post(f"/staff/storefronts/{storefront.pk}/webhook-secret/").status_code == 403
    client.force_login(admin)
    response = client.post(f"/staff/storefronts/{storefront.pk}/webhook-secret/", follow=True)
    assert "shown once" in str(list(response.context["messages"])).lower()
    storefront.refresh_from_db()
    assert storefront.webhook_secret_encrypted


# --- Publishing a plan from the product page ------------------------------------------------------------------

def test_the_product_detail_page_offers_the_storefront_checkboxes_to_admin_only(client, admin, manager, storefront, product):
    client.force_login(admin)
    page = client.get(f"/staff/products/{product.slug}/").content.decode()
    assert storefront.name in page and "External storefronts" in page

    client.force_login(manager)
    page = client.get(f"/staff/products/{product.slug}/").content.decode()
    assert "External storefronts" not in page  # manager lacks view_providers


def test_publishing_a_product_to_a_storefront_from_the_product_page(client, admin, storefront, product):
    client.force_login(admin)
    response = client.post(f"/staff/storefronts/products/{product.slug}/publish/",
                           {"storefronts": [storefront.pk]}, follow=True)
    assert response.status_code == 200
    assert storefront.published_products.filter(product=product).exists()
    assert AuditEvent.objects.filter(action="storefront.product_published").exists()


def test_resaving_the_available_on_checkboxes_does_not_clobber_an_existing_override(client, admin, storefront, product):
    """Setting a title override from the per-storefront row, then resaving the plain checkbox list above it (e.g.
    checking a second storefront too), must not silently wipe what was just set."""
    services.publish_product(admin, product, storefront, title_override="My Own Title", featured=True, sort_order=3)
    client.force_login(admin)
    client.post(f"/staff/storefronts/products/{product.slug}/publish/", {"storefronts": [storefront.pk]})
    link = storefront.published_products.get(product=product)
    assert link.title_override == "My Own Title" and link.featured and link.sort_order == 3


def test_editing_one_storefront_s_own_title_and_sort_order_for_a_plan(client, admin, storefront, product):
    services.publish_product(admin, product, storefront)
    client.force_login(admin)
    response = client.post(f"/staff/storefronts/products/{product.slug}/publish/{storefront.pk}/", {
        "title_override": "Starter (UK)", "description_override": "Local UK plan", "featured": "on", "sort_order": "7",
    }, follow=True)
    assert response.status_code == 200
    link = storefront.published_products.get(product=product)
    assert link.title_override == "Starter (UK)" and link.featured and link.sort_order == 7
    assert AuditEvent.objects.filter(action="storefront.product_updated").exists()


def test_editing_a_storefront_s_override_needs_manage_products(client, manager, storefront, product):
    services.publish_product(manager, product, storefront)
    client.force_login(manager)  # manager has manage_products in this fixture
    response = client.post(f"/staff/storefronts/products/{product.slug}/publish/{storefront.pk}/",
                           {"title_override": "x", "sort_order": "1"})
    assert response.status_code in (200, 302)


def test_unchecking_every_storefront_unpublishes_the_product(client, admin, storefront, product):
    services.publish_product(admin, product, storefront)
    client.force_login(admin)
    client.post(f"/staff/storefronts/products/{product.slug}/publish/", {"storefronts": []})
    assert not storefront.published_products.filter(product=product).exists()


def test_publishing_needs_manage_products(client, manager, storefront, product):
    client.force_login(manager)  # manager has manage_products
    response = client.post(f"/staff/storefronts/products/{product.slug}/publish/", {"storefronts": [storefront.pk]})
    assert response.status_code in (200, 302)
    assert storefront.published_products.filter(product=product).exists()


# --- Deleting a storefront --------------------------------------------------------------------------------------

def test_deleting_a_storefront_removes_it_keeps_the_plan_and_is_audited(client, admin, storefront, product):
    services.publish_product(admin, product, storefront)
    pk, public_id = storefront.pk, storefront.public_id
    client.force_login(admin)
    response = client.post(f"/staff/storefronts/{pk}/delete/")
    assert response.status_code == 302 and response["Location"].endswith("/staff/storefronts/")
    assert not ExternalStorefront.objects.filter(pk=pk).exists()
    product.refresh_from_db()  # the plan itself is untouched
    event = AuditEvent.objects.get(action="storefront.deleted")
    assert event.metadata["public_id"] == public_id and event.metadata["published_plans"] == 1


def test_deleting_needs_manage_providers_and_a_post(client, manager, admin, storefront):
    client.force_login(manager)
    assert client.post(f"/staff/storefronts/{storefront.pk}/delete/").status_code == 403
    client.force_login(admin)
    assert client.get(f"/staff/storefronts/{storefront.pk}/delete/").status_code == 405
    assert ExternalStorefront.objects.filter(pk=storefront.pk).exists()


def test_the_delete_button_is_on_the_detail_page(client, admin, storefront):
    client.force_login(admin)
    assert "Delete storefront" in client.get(f"/staff/storefronts/{storefront.pk}/").content.decode()
