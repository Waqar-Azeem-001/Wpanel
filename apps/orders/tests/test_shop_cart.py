"""The store's categories, the cart's billing period and add-on switches, and WHOIS privacy on a domain."""
from decimal import Decimal

import pytest
from django.test import Client

from apps.core.exceptions import ServiceError
from apps.orders import pricing, services
from apps.orders.models import Cart, CartItem, Order
from apps.products import categories
from apps.products import services as product_services
from apps.products.models import Addon, AddonKind, BillingCycle, ProductType

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ng-Passw0rd!x"


def visitor():
    return Client(HTTP_HOST="localhost", raise_request_exception=False)


def add_host(browser, shop, domain="example.com", cycle="annual"):
    return browser.post("/cart/add/hosting/", {"product": shop["product"].pk, "cycle": cycle, "domain": domain})


@pytest.fixture
def whois(manager):
    a = product_services.create_addon(manager, {"name": "WHOIS Privacy", "kind": AddonKind.WHOIS_PRIVACY})
    product_services.set_price(manager, a, billing_cycle=BillingCycle.ANNUAL, price="4.00")
    product_services.set_addon_status(manager, a, "active")
    return a


@pytest.fixture
def ssl(manager):
    a = product_services.create_addon(manager, {"name": "SSL Certificate", "kind": AddonKind.SSL})
    product_services.set_price(manager, a, billing_cycle=BillingCycle.MONTHLY, price="3.00")
    product_services.set_price(manager, a, billing_cycle=BillingCycle.ANNUAL, price="30.00")
    product_services.set_addon_status(manager, a, "active")
    return a


# --- Categories on the store ----------------------------------------------------------------------------------

def test_the_store_lists_every_category_in_its_group(shop):
    keys = [c.key for c in categories.CATEGORIES]
    assert keys == ["wordpress", "shared", "business", "ecommerce", "reseller", "vps", "dedicated", "ssl", "whois", "domains"]
    page = visitor().get("/products/").content.decode()
    for label in ("WordPress hosting", "Shared hosting", "Business hosting", "E-commerce hosting", "VPS servers",
                  "Dedicated servers", "SSL certificates", "WHOIS privacy", "Domains"):
        assert label in page
    assert "Starter" in page and "Coming soon" in page  # Starter is shared hosting; the other headings are empty


def test_a_plan_appears_only_in_its_own_category(shop, manager):
    biz = product_services.create_product(manager, {"name": "Biz Pro", "type": ProductType.BUSINESS_HOSTING,
                                                    "whm_package_name": "biz"})
    product_services.set_price(manager, biz, billing_cycle=BillingCycle.MONTHLY, price="20.00")
    product_services.set_product_status(manager, biz, "active")
    business = visitor().get("/products/?category=business").content.decode()
    assert "Biz Pro" in business and "Starter" not in business
    shared = visitor().get("/products/?category=shared").content.decode()
    assert "Starter" in shared and "Biz Pro" not in shared
    assert visitor().get("/products/?category=nonsense").status_code == 200  # an unknown one shows everything
    assert visitor().get("/products/?category=domains")["Location"] == "/domains/"


def test_ssl_and_whois_are_listed_from_addon_kinds(shop, ssl, whois):
    ssl_page = visitor().get("/products/?category=ssl").content.decode()
    assert "SSL Certificate" in ssl_page and "WHOIS Privacy" not in ssl_page
    whois_page = visitor().get("/products/?category=whois").content.decode()
    assert "WHOIS Privacy" in whois_page and "SSL Certificate" not in whois_page
    assert "Backups" not in ssl_page + whois_page  # a general add-on has no category of its own


def test_the_home_page_has_a_tile_for_each_category(shop, ssl):
    page = visitor().get("/").content.decode()
    assert page.count("cat-tile") >= 10 and "SSL certificates" in page and "Search now" in page


def test_staff_can_choose_an_addon_kind(manager):
    browser = visitor()
    browser.force_login(manager)
    made = browser.post("/staff/addons/new/", {"name": "Site Lock", "kind": "ssl"})
    assert made.status_code == 302
    assert Addon.objects.get(name="Site Lock").kind == "ssl"
    assert browser.post("/staff/addons/new/", {"name": "Plain"}).status_code == 302
    assert Addon.objects.get(name="Plain").kind == "general"


# --- The billing period ---------------------------------------------------------------------------------------------

def test_the_period_menu_lists_the_plans_prices_with_real_savings(shop):
    browser = visitor()
    add_host(browser, shop)
    item = CartItem.objects.get()
    by = {o["value"]: o for o in services.period_options(item)}
    assert list(by) == ["monthly", "custom:3", "annual"] and by["monthly"]["saving"] is None
    assert by["annual"]["saving"] == 17 and by["annual"]["per_month"] == Decimal("8.33")  # 100 against 12 x 10
    assert by["custom:3"]["saving"] == 7 and by["annual"]["selected"]
    page = browser.get("/cart/").content.decode()
    assert 'name="option"' in page and "save 17%" in page and "Renews at" in page


def test_changing_the_period_reprices_and_moves_recurring_addons(shop, ssl):
    browser = visitor()
    add_host(browser, shop)
    host = CartItem.objects.get()
    browser.post("/cart/addons/toggle/", {"parent_item": host.pk, "addon": ssl.pk, "option": "annual"})
    assert CartItem.objects.get(kind="addon").billing_cycle == "annual"
    browser.post(f"/cart/items/{host.pk}/period/", {"option": "monthly"})
    host.refresh_from_db()
    assert host.billing_cycle == "monthly"
    assert CartItem.objects.get(kind="addon").billing_cycle == "monthly"  # the SSL follows the plan
    assert "13.00" in browser.get("/cart/").content.decode()  # 10 plan + 3 SSL


def test_a_recurring_addon_with_no_price_for_the_new_period_is_dropped(shop, ssl):
    browser = visitor()
    add_host(browser, shop)
    host = CartItem.objects.get()
    browser.post("/cart/addons/toggle/", {"parent_item": host.pk, "addon": ssl.pk, "option": "annual"})
    browser.post(f"/cart/items/{host.pk}/period/", {"option": "custom:3"})  # the SSL has no 3-month price
    assert not CartItem.objects.filter(kind="addon").exists() and CartItem.objects.get().custom_months == 3


def test_a_period_the_plan_is_not_priced_for_is_refused_and_others_cannot_change_it(shop):
    mine, other = visitor(), visitor()
    add_host(mine, shop)
    host = CartItem.objects.get()
    mine.post(f"/cart/items/{host.pk}/period/", {"option": "biennial"})
    host.refresh_from_db()
    assert host.billing_cycle == "annual"
    assert other.post(f"/cart/items/{host.pk}/period/", {"option": "monthly"}).status_code == 404


# --- Add-ons as switches -------------------------------------------------------------------------------------------

def test_an_addon_switch_turns_on_and_off(shop, addon):
    browser = visitor()
    add_host(browser, shop)
    host = CartItem.objects.get()
    body = {"parent_item": host.pk, "addon": addon.pk, "option": "annual"}
    browser.post("/cart/addons/toggle/", body)
    assert CartItem.objects.filter(kind="addon").count() == 1
    assert "Add Backups" in browser.get("/cart/").content.decode()
    browser.post("/cart/addons/toggle/", body)
    assert not CartItem.objects.filter(kind="addon").exists()


def test_a_switch_cannot_touch_another_visitors_line(shop, addon):
    mine, other = visitor(), visitor()
    add_host(mine, shop)
    host = CartItem.objects.get()
    assert other.post("/cart/addons/toggle/", {"parent_item": host.pk, "addon": addon.pk,
                                               "option": "annual"}).status_code == 404
    assert not CartItem.objects.filter(kind="addon").exists()


# --- WHOIS privacy goes with a domain ------------------------------------------------------------------------------

def test_whois_privacy_is_offered_on_a_domain_and_charged_per_year(shop, whois, ssl, addon):
    browser = visitor()
    browser.post("/cart/add/domain/", {"domain": "fresh.com", "years": 2})
    domain = CartItem.objects.get()
    page = browser.get("/cart/").content.decode()
    assert "Add WHOIS Privacy" in page and "Add SSL Certificate" not in page and "Add Backups" not in page
    browser.post("/cart/addons/toggle/", {"parent_item": domain.pk, "addon": whois.pk, "option": "annual"})
    line = CartItem.objects.get(kind="addon")
    assert line.parent == domain
    priced = pricing.price_cart(line.cart)
    assert [row.total for row in priced.lines if row.item.kind == "addon"] == [Decimal("8.00")]  # 4.00 a year, 2 years


def test_hosting_addons_are_not_offered_on_domains_and_whois_not_on_plans(shop, whois, ssl):
    browser = visitor()
    add_host(browser, shop)
    page = browser.get("/cart/").content.decode()
    assert "Add SSL Certificate" in page and "Add WHOIS Privacy" not in page
    host = CartItem.objects.get()
    with pytest.raises(ServiceError):
        services.add_addon(None, host, whois)


def test_a_whois_addon_on_a_domain_becomes_an_order_line(shop, whois):
    browser = visitor()
    browser.post("/cart/add/domain/", {"domain": "fresh.com", "years": 1})
    domain = CartItem.objects.get()
    browser.post("/cart/addons/toggle/", {"parent_item": domain.pk, "addon": whois.pk, "option": "annual"})
    response = browser.post("/checkout/", {
        "first_name": "Zed", "email": "zed@visitor.test", "country": "PK", "password": PASSWORD,
        "password_confirm": PASSWORD, "payment_method": shop["bank"].code})
    assert response.status_code == 302
    order = Order.objects.get()
    assert sorted(order.items.values_list("kind", flat=True)) == ["addon", "domain_register"]
    assert order.subtotal == Decimal("16.00")  # 12 for the domain + 4 for the privacy
    assert not Cart.objects.filter(status="open").exists()
