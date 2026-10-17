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
    assert visitor.get("/hosting/").content.decode().count("cat-tile") == 11  # the hosting page gets its tile too


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
    for heading in ("Basics", "First price", "Limits", "More options"):
        assert heading in page
    assert 'value="Disk space (MB)"' in page and "Resource limits (JSON)" not in page and "Add a limit" in page
    made = browser(manager).post("/staff/products/new/", {
        "name": "Fast VPS", "type": "vps", "description": "2 vCPU\n4 GB RAM", "whm_package_name": "fast", "auto_setup": "on",
        "limit_title": ["Disk space (MB)", "Databases", "", "Free SSL"], "limit_value": ["80000", "", "5", "Included"], "billing_cycle": "monthly", "price": "2500", "setup_fee": "100"})
    assert made.status_code == 302
    p = Product.objects.get(name="Fast VPS")
    assert p.resource_limits == {"disk_mb": 80000, "databases": None, "Free SSL": "Included"} and p.category.slug == "vps"
    row = p.prices.get()
    assert (row.billing_cycle, row.price, row.setup_fee) == ("monthly", Decimal("2500.00"), Decimal("100.00"))


def test_a_price_needs_its_cycle_and_a_custom_cycle_needs_months(manager):
    b = browser(manager)
    no_cycle = b.post("/staff/products/new/", {"name": "P1", "type": "vps", "price": "10", "billing_cycle": ""})
    assert no_cycle.status_code == 200 and b"Choose how often" in no_cycle.content and not Product.objects.filter(name="P1").exists()
    custom = b.post("/staff/products/new/", {"name": "P2", "type": "vps", "price": "10", "billing_cycle": "custom"})
    assert custom.status_code == 200 and b"number of months" in custom.content


def test_editing_limits_adds_changes_and_removes_lines(manager):
    p = plan(manager, "Keeper", resource_limits={"disk_mb": 5000, "custom_note": 7, "old": 1})
    b = browser(manager)
    page = b.get(f"/staff/products/{p.slug}/edit/").content.decode()
    assert 'value="5000"' in page and 'value="custom_note"' in page
    b.post(f"/staff/products/{p.slug}/edit/", {"name": "Keeper", "type": "shared_hosting", "whm_package_name": "pkg",
                                              "limit_title": ["Disk space (MB)", "custom_note", "Backups"], "limit_value": ["9000", "7", "Daily"]})
    p.refresh_from_db()
    assert p.resource_limits == {"disk_mb": 9000, "custom_note": 7, "Backups": "Daily"}  # "old" had no row, so it is gone
    assert p.prices.count() == 1  # a price box is only on the create form
    assert p.limit_items == [("Disk space (MB)", 9000), ("custom_note", 7), ("Backups", "Daily")]  # in the order listed, on any database


def test_limits_keep_the_order_they_were_listed_in_even_when_the_database_reorders_keys(manager):
    # PostgreSQL's jsonb hands the keys back sorted (shorter first, then alphabetical); the order staff chose is stored beside them.
    p = plan(manager, "Ordered")
    b = browser(manager)
    b.post(f"/staff/products/{p.slug}/edit/", {"name": "Ordered", "type": "shared_hosting", "limit_title": ["Websites", "Free SSL", "Databases"],
                                              "limit_value": ["3", "Included", "5"]})
    p.refresh_from_db()
    assert p.limit_order == ["websites", "Free SSL", "databases"]
    p.resource_limits = dict(sorted(p.resource_limits.items(), key=lambda kv: (len(kv[0]), kv[0])))  # what jsonb does
    assert [key for key, _ in p.ordered_limits()] == ["websites", "Free SSL", "databases"]
    page = b.get(f"/staff/products/{p.slug}/edit/").content.decode()
    assert page.index('value="Websites"') < page.index('value="Free SSL"') < page.index('value="Databases"')


def test_a_limit_listed_twice_or_with_a_word_for_a_number_is_refused(manager):
    b = browser(manager)
    twice = b.post("/staff/products/new/", {"name": "Dup", "type": "vps", "limit_title": ["Databases", "databases"], "limit_value": ["1", "2"]})
    assert twice.status_code == 200 and b"listed twice" in twice.content and not Product.objects.filter(name="Dup").exists()
    word = b.post("/staff/products/new/", {"name": "Word", "type": "vps", "limit_title": ["Databases"], "limit_value": ["lots"]})
    assert word.status_code == 200 and b"whole number" in word.content
    assert b'value="lots"' in word.content  # what was typed is kept on the page


def test_the_limits_are_shown_on_the_public_plan_page(manager):
    p = plan(manager, "Shown", resource_limits={"email_accounts": 10, "databases": None})
    product_services.set_product_status(manager, p, "active")
    page = Client().get(f"/products/{p.slug}/").content.decode()
    assert "What is included" in page and "Email accounts" in page and "Unlimited" in page


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


@pytest.mark.django_db
def test_the_addon_catalogue_adds_switched_off_categories_and_hidden_unpriced_addons_and_is_repeatable():
    from django.core.management import call_command

    from apps.products.models import Addon, AddonKind, StoreCategory

    call_command("seed_addon_catalogue")
    call_command("seed_addon_catalogue")
    assert StoreCategory.objects.filter(kind="addons", is_active=False).count() == 6
    assert Addon.objects.count() == 21 and not Addon.objects.exclude(status="hidden").exists()
    assert not Addon.objects.filter(prices__isnull=False).exists()
    assert Addon.objects.get(name="Domain Protection").applies_to == "domain" and Addon.objects.get(name="Dedicated IP").applies_to == "hosting"
    assert Addon.objects.get(name="Domain Protection").kind == AddonKind.DOMAIN
