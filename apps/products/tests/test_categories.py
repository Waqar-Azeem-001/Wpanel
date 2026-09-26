"""Store categories staff manage, the sectioned product and add-on forms, first prices, and add-on prices shown to staff and shoppers."""
from decimal import Decimal

import pytest
from django.test import Client

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.core.exceptions import ServiceError
from apps.products import categories
from apps.products import services as product_services
from apps.products.models import Addon, AddonKind, BillingCycle, Product, ProductType, StoreCategory

pytestmark = pytest.mark.django_db


def browser(user=None):
    client = Client(HTTP_HOST="localhost", raise_request_exception=False)
    if user is not None:
        client.force_login(user)
    return client


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


def plan(manager, name="Plan", ptype=ProductType.SHARED_HOSTING, **extra):
    p = product_services.create_product(manager, {"name": name, "type": ptype, "whm_package_name": "pkg", **extra})
    product_services.set_price(manager, p, billing_cycle=BillingCycle.MONTHLY, price="10.00")
    product_services.set_product_status(manager, p, "active")
    return p


def test_the_standard_categories_exist_and_new_products_are_filed_by_their_type(manager):
    assert [c.slug for c in categories.active()] == ["wordpress", "shared", "business", "ecommerce", "reseller", "vps", "dedicated",
                                                     "ssl", "whois", "domains"]
    assert plan(manager, "S", ProductType.SHARED_HOSTING).category.slug == "shared"
    assert plan(manager, "V", ProductType.VPS).category.slug == "vps"
    assert product_services.create_addon(manager, {"name": "Lock", "kind": AddonKind.SSL}).category.slug == "ssl"
    assert product_services.create_addon(manager, {"name": "Extra"}).category is None


def test_staff_add_a_category_and_put_a_plan_in_it(manager):
    b = browser(manager)
    made = b.post("/staff/categories/new/", {"name": "Game servers", "short_name": "Games", "blurb": "Low-latency game hosting",
                                             "group": "Servers", "kind": "plans", "icon": "bi-rocket", "sort_order": "65", "is_active": "on"})
    assert made.status_code == 302
    cat = StoreCategory.objects.get(slug="game-servers")
    assert cat.label == "Games" and cat.group == "Servers" and AuditEvent.objects.filter(action="store_category.created").exists()
    p = plan(manager, "Minecraft 4G", ProductType.VPS, category=cat)
    assert p.category == cat
    visitor = browser()
    store = visitor.get("/products/").content.decode()
    assert "Game servers" in store and "Minecraft 4G" in store
    only = visitor.get("/products/?category=game-servers").content.decode()
    assert "Minecraft 4G" in only and "Low-latency" in only
    assert visitor.get("/").content.decode().count("cat-tile") == 11  # the home page gets its tile too


def test_a_category_can_be_renamed_reordered_and_hidden(manager):
    cat = StoreCategory.objects.get(slug="vps")
    plan(manager, "V1", ProductType.VPS)
    b = browser(manager)
    b.post(f"/staff/categories/{cat.pk}/", {"name": "Cloud VPS", "short_name": "", "blurb": "", "group": "Servers", "kind": "plans",
                                            "icon": "bi-cloud", "sort_order": "1", "is_active": ""})
    cat.refresh_from_db()
    assert cat.name == "Cloud VPS" and not cat.is_active and cat.sort_order == 1
    store = browser().get("/products/").content.decode()
    assert "Cloud VPS" not in store and "V1" in store and "More plans" in store  # hidden: its plan is not lost
    cat.is_active = True
    cat.save()
    assert store.index("V1") > 0 and [c.slug for c in categories.active()][0] == "vps"


def test_a_category_holding_things_cannot_be_deleted_but_an_empty_one_can(manager):
    plan(manager, "V1", ProductType.VPS)
    with pytest.raises(ServiceError) as exc:
        product_services.delete_category(manager, StoreCategory.objects.get(slug="vps"))
    assert exc.value.code == "category_in_use"
    empty = product_services.save_category(manager, None, {"name": "Temp", "icon": "bi-star", "group": "Hosting", "kind": "plans",
                                                          "sort_order": 5, "is_active": True})
    browser(manager).post(f"/staff/categories/{empty.pk}/delete/")
    assert not StoreCategory.objects.filter(pk=empty.pk).exists() and AuditEvent.objects.filter(action="store_category.deleted").exists()


def test_only_people_who_manage_products_change_categories(staff):
    agent = staff(Role.SUPPORT_AGENT)
    b = browser(agent)
    assert b.get("/staff/categories/").status_code in (200, 403)
    assert b.get("/staff/categories/new/").status_code == 403
    assert b.post("/staff/categories/new/", {"name": "X", "group": "G", "kind": "plans", "icon": "bi-star", "sort_order": "1"}).status_code == 403
    assert browser().get("/staff/categories/").status_code == 302
    with pytest.raises(ServiceError):
        product_services.save_category(agent, None, {"name": "X", "icon": "bi-star", "group": "G", "kind": "plans", "sort_order": 1})


def test_a_product_with_no_category_still_shows_under_more_plans(manager):
    p = plan(manager, "Odd One", ProductType.VPS)
    Product.objects.filter(pk=p.pk).update(category=None)
    store = browser().get("/products/").content.decode()
    assert "Odd One" in store and "More plans" in store
    assert "Odd One" in browser().get("/products/?category=more").content.decode()


def test_the_categories_page_and_menu_entry(manager):
    page = browser(manager).get("/staff/categories/").content.decode()
    assert "Store categories" in page and "WordPress hosting" in page and "Add a category" in page
    assert "/staff/categories/" in browser(manager).get("/staff/").content.decode()


# --- The sectioned forms --------------------------------------------------------------------------------------------------

def test_the_product_form_is_in_sections_with_structured_limits_and_a_first_price(manager):
    page = browser(manager).get("/staff/products/new/").content.decode()
    for heading in ("Basics", "First price", "Provisioning", "Resource limits", "Selling more"):
        assert heading in page
    assert "Disk space (MB)" in page and "Resource limits (JSON)" not in page
    made = browser(manager).post("/staff/products/new/", {
        "name": "Fast VPS", "type": "vps", "description": "2 vCPU\n4 GB RAM", "whm_package_name": "fast", "auto_setup": "on",
        "limit_disk_mb": "80000", "limit_databases": "", "billing_cycle": "monthly", "price": "2500", "setup_fee": "100"})
    assert made.status_code == 302
    p = Product.objects.get(name="Fast VPS")
    assert p.resource_limits == {"disk_mb": 80000} and p.category.slug == "vps"
    row = p.prices.get()
    assert (row.billing_cycle, row.price, row.setup_fee) == ("monthly", Decimal("2500.00"), Decimal("100.00"))


def test_a_price_needs_its_cycle_and_a_custom_cycle_needs_months(manager):
    b = browser(manager)
    no_cycle = b.post("/staff/products/new/", {"name": "P1", "type": "vps", "price": "10", "billing_cycle": ""})
    assert no_cycle.status_code == 200 and b"Choose how often" in no_cycle.content and not Product.objects.filter(name="P1").exists()
    custom = b.post("/staff/products/new/", {"name": "P2", "type": "vps", "price": "10", "billing_cycle": "custom"})
    assert custom.status_code == 200 and b"number of months" in custom.content


def test_editing_a_product_keeps_limits_the_form_does_not_show(manager):
    p = plan(manager, "Keeper", resource_limits={"disk_mb": 5000, "custom_note": 7})
    b = browser(manager)
    page = b.get(f"/staff/products/{p.slug}/edit/").content.decode()
    assert 'value="5000"' in page
    b.post(f"/staff/products/{p.slug}/edit/", {"name": "Keeper", "type": "shared_hosting", "limit_disk_mb": "9000", "whm_package_name": "pkg"})
    p.refresh_from_db()
    assert p.resource_limits == {"disk_mb": 9000, "custom_note": 7} and p.prices.count() == 1  # a price box is only on the create form


def test_an_addon_is_created_with_its_price_and_shows_it(manager):
    b = browser(manager)
    made = b.post("/staff/addons/new/", {"name": "Site Backups", "kind": "general", "description": "Daily",
                                         "billing_cycle": "annual", "price": "1200", "setup_fee": ""})
    assert made.status_code == 302
    addon = Addon.objects.get(name="Site Backups")
    assert addon.prices.get().price == Decimal("1200.00") and addon.price_summary.endswith("1200.00 annually")
    listing = b.get("/staff/addons/").content.decode()
    assert "Site Backups" in listing and "1200.00" in listing
    assert "Price" in b.get("/staff/addons/new/").content.decode()
    product_services.set_addon_status(manager, addon, "active")
    p = plan(manager, "Host")
    product_services.update_product(manager, p, {"recommended_addons": [addon]})
    assert "1200.00" in browser().get(f"/products/{p.slug}/").content.decode()  # the shopper sees what it costs
    assert "1200.00" in b.get(f"/staff/products/{p.slug}/edit/").content.decode()  # and so does staff choosing it


def test_an_addon_can_be_placed_in_an_addons_category(manager):
    addon = product_services.create_addon(manager, {"name": "Trust Seal", "kind": "ssl", "category": StoreCategory.objects.get(slug="whois")})
    assert addon.category.slug == "whois"
