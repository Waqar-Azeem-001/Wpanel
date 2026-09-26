"""
A visitor can shop without an account: guest cart, sign-in that keeps the cart, checkout that creates the account, and the
upsell / cross-sell suggestions in the cart.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.billing import services as billing
from apps.core.exceptions import ServiceError
from apps.orders import guest, services
from apps.orders.models import Cart, CartItem, Order
from apps.products import services as product_services
from apps.products.models import BillingCycle, ProductType

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ng-Passw0rd!x"


def visitor():
    return Client(HTTP_HOST="localhost", raise_request_exception=False)


def add_host(browser, shop, domain="example.com", cycle="annual"):
    return browser.post("/cart/add/hosting/", {"product": shop["product"].pk, "cycle": cycle, "domain": domain})


def details(shop, **extra):
    return {"first_name": "Zed", "last_name": "Visitor", "email": "zed@visitor.test", "country": "PK", "phone": "+92300",
            "password": PASSWORD, "password_confirm": PASSWORD, "payment_method": shop["bank"].code, "notes": "", **extra}


# --- Anyone can fill a cart ----------------------------------------------------------------------------------

def test_a_visitor_can_add_a_plan_and_see_it_in_the_cart(shop):
    browser = visitor()
    response = add_host(browser, shop)
    assert response.status_code == 302 and response["Location"] == "/cart/"
    cart = Cart.objects.get()
    assert cart.is_guest and cart.client_id is None and cart.items.count() == 1
    page = browser.get("/cart/")
    assert page.status_code == 200 and b"Starter" in page.content and b"example.com" in page.content
    assert b"Proceed to checkout" in page.content and b"create one at checkout" in page.content
    assert browser.get("/").context["cart_item_count"] == 1  # the header shows it


def test_one_visitors_cart_is_invisible_to_everyone_else(shop):
    mine, other = visitor(), visitor()
    add_host(mine, shop)
    item = CartItem.objects.get()
    assert b"Starter" not in other.get("/cart/").content
    assert other.post(f"/cart/items/{item.pk}/remove/").status_code == 404
    assert other.post(f"/cart/items/{item.pk}/upgrade/").status_code == 404
    assert CartItem.objects.filter(pk=item.pk).exists()
    assert mine.post(f"/cart/items/{item.pk}/remove/").status_code == 302 and not CartItem.objects.exists()


def test_a_visitor_can_add_an_addon_a_coupon_a_domain_and_a_transfer(shop, coupon):
    browser = visitor()
    add_host(browser, shop)
    parent = CartItem.objects.get()
    browser.post("/cart/add/addon/", {"parent_item": parent.pk, "addon": shop["addon"].pk, "option": "annual"})
    browser.post("/cart/add/domain/", {"domain": "fresh.com", "years": 1})
    browser.post("/cart/coupon/", {"code": "SAVE10"})
    cart = Cart.objects.get()
    assert cart.coupon_id and cart.items.count() == 3
    page = browser.get("/cart/")
    assert b"SAVE10" in page.content and b"fresh.com" in page.content and b"Backups" in page.content
    browser.post("/cart/coupon/remove/")
    cart.refresh_from_db()
    assert cart.coupon_id is None


def test_an_empty_visitor_cart_and_checkout_lead_back_to_shopping(shop):
    browser = visitor()
    assert b"Your cart is empty" in browser.get("/cart/").content
    assert browser.get("/checkout/")["Location"] == "/cart/"
    assert not Cart.objects.exists()  # merely looking creates nothing


def test_staff_still_cannot_shop(manager, shop):
    browser = visitor()
    browser.force_login(manager)
    assert b"available to customer accounts" in browser.get("/cart/").content
    browser.post("/cart/add/hosting/", {"product": shop["product"].pk, "cycle": "annual", "domain": "a.com"})
    assert not CartItem.objects.exists()


# --- Signing in keeps the cart -----------------------------------------------------------------------------------

def test_signing_in_moves_the_cart_into_the_account(shop, owner, client_obj):
    owner.set_password(PASSWORD)
    owner.save()
    browser = visitor()
    add_host(browser, shop)
    browser.post("/cart/add/domain/", {"domain": "fresh.com", "years": 1})
    assert Cart.objects.filter(user__isnull=True).exists()
    response = browser.post("/account/login/", {"email": owner.email, "password": PASSWORD})
    assert response.status_code == 302
    assert not Cart.objects.filter(user__isnull=True).exists()
    mine = Cart.objects.get(user=owner, client=client_obj)
    assert sorted(mine.items.values_list("kind", flat=True)) == ["domain_register", "hosting"]
    assert b"example.com" in browser.get("/cart/").content and guest.item_count(browser) == 0


def test_signing_in_merges_with_what_the_account_already_had(shop, owner, client_obj):
    owner.set_password(PASSWORD)
    owner.save()
    services.add_hosting(owner, client_obj, shop["product"], "example.com", "annual")
    browser = visitor()
    add_host(browser, shop)  # the same plan for the same domain
    add_host(browser, shop, domain="other.com")
    browser.post("/account/login/", {"email": owner.email, "password": PASSWORD})
    domains = sorted(CartItem.objects.filter(cart__user=owner).values_list("domain_name", flat=True))
    assert domains == ["example.com", "other.com"]


def test_add_ons_follow_their_plan_when_the_cart_moves(shop, owner, client_obj):
    owner.set_password(PASSWORD)
    owner.save()
    browser = visitor()
    add_host(browser, shop)
    parent = CartItem.objects.get()
    browser.post("/cart/add/addon/", {"parent_item": parent.pk, "addon": shop["addon"].pk, "option": "annual"})
    browser.post("/account/login/", {"email": owner.email, "password": PASSWORD})
    addon_item = CartItem.objects.get(cart__user=owner, kind="addon")
    assert addon_item.parent.kind == "hosting" and addon_item.parent.cart.user == owner


def test_staff_signing_in_does_not_take_a_guest_cart(shop, manager):
    browser = visitor()
    add_host(browser, shop)
    browser.force_login(manager)
    assert Cart.objects.filter(user__isnull=True).count() == 1 and not CartItem.objects.filter(cart__user=manager).exists()


# --- Checkout creates the account ------------------------------------------------------------------------------

def test_a_visitor_checks_out_and_gets_an_account_and_an_order(shop, manager):
    billing.save_tax_rule(manager, "PK", name="GST", rate=Decimal("10"))
    browser = visitor()
    add_host(browser, shop)
    page = browser.get("/checkout/")
    assert page.status_code == 200 and b"Your details" in page.content and b"worked out at checkout" in page.content
    response = browser.post("/checkout/", details(shop))
    order = Order.objects.get()
    assert response.status_code == 302 and response["Location"] == f"/account/orders/{order.pk}/"
    user = User.objects.get(email="zed@visitor.test")
    assert user.check_password(PASSWORD) and order.placed_by == user and order.client.country == "PK"
    assert order.tax_name == "GST" and order.tax_total == Decimal("11.00") and order.total == Decimal("121.00")
    assert not Cart.objects.filter(status="open").exists()  # nothing is left behind
    assert b"Welcome" in browser.get(response["Location"]).content  # signed in, and told
    assert AuditEvent.objects.filter(action="account.registered").exists() and AuditEvent.objects.filter(action="order.placed").exists()


def test_the_country_changes_the_tax_shown_without_creating_anything(shop, manager):
    billing.save_tax_rule(manager, "PK", name="GST", rate=Decimal("10"))
    browser = visitor()
    add_host(browser, shop)
    page = browser.post("/checkout/", {"action": "refresh", "country": "PK", "first_name": "Zed", "password": "x"})
    assert b"GST (10.00%)" in page.content and b'value="Zed"' in page.content and b'value="x"' not in page.content
    assert not User.objects.filter(email="zed@visitor.test").exists() and not Order.objects.exists()
    page = browser.get("/checkout/?country=US")
    assert b"worked out at checkout" not in page.content and b"GST" not in page.content


def test_an_existing_email_is_told_to_sign_in_and_nothing_is_created(shop, owner):
    browser = visitor()
    add_host(browser, shop)
    page = browser.post("/checkout/", details(shop, email=owner.email))
    assert page.status_code == 200 and b"already exists" in page.content and b"Sign in" in page.content
    assert not Order.objects.exists() and Cart.objects.filter(user__isnull=True).exists()


def test_a_weak_or_mismatched_password_creates_no_account(shop):
    browser = visitor()
    add_host(browser, shop)
    assert b"do not match" in browser.post("/checkout/", details(shop, password_confirm="different")).content
    weak = browser.post("/checkout/", details(shop, password="12345678", password_confirm="12345678"))
    assert weak.status_code == 200 and not User.objects.filter(email="zed@visitor.test").exists() and not Order.objects.exists()


def test_a_cart_that_cannot_be_ordered_creates_no_account(shop, manager):
    browser = visitor()
    add_host(browser, shop)
    product_services.set_product_status(manager, shop["product"], "hidden")
    response = browser.post("/checkout/", details(shop))
    assert response.status_code == 200 and not User.objects.filter(email="zed@visitor.test").exists()


def test_a_missing_country_or_payment_method_is_asked_for(shop):
    browser = visitor()
    add_host(browser, shop)
    page = browser.post("/checkout/", details(shop, country="", payment_method=""))
    assert page.status_code == 200 and not User.objects.filter(email="zed@visitor.test").exists()


def test_a_signed_in_customer_still_checks_out_as_before(shop, owner):
    browser = visitor()
    browser.force_login(owner)
    add_host(browser, shop)
    page = browser.get("/checkout/")
    assert b"Billing details" in page.content and b"Your details" not in page.content
    response = browser.post("/checkout/", {"payment_method": shop["bank"].code})
    assert response.status_code == 302 and Order.objects.get().placed_by == owner


# --- Upsell and cross-sell ---------------------------------------------------------------------------------------

@pytest.fixture
def business(manager, product):
    plan = product_services.create_product(
        manager, {"name": "Business", "type": ProductType.SHARED_HOSTING, "whm_package_name": "biz_pkg"})
    product_services.set_price(manager, plan, billing_cycle=BillingCycle.ANNUAL, price="180.00")
    product_services.set_price(manager, plan, billing_cycle=BillingCycle.MONTHLY, price="18.00")
    product_services.set_product_status(manager, plan, "active")
    product_services.update_product(manager, product, {"upsell_product": plan})
    return plan


def test_the_cart_suggests_the_bigger_plan_and_one_click_swaps_it(shop, business):
    browser = visitor()
    add_host(browser, shop)
    parent = CartItem.objects.get()
    browser.post("/cart/add/addon/", {"parent_item": parent.pk, "addon": shop["addon"].pk, "option": "annual"})
    page = browser.get("/cart/").content.decode()
    assert "Upgrade to Business" in page and "80.00" in page  # 180 - 100 more
    browser.post(f"/cart/items/{parent.pk}/upgrade/")
    parent.refresh_from_db()
    assert parent.product == business and parent.domain_name == "example.com" and parent.billing_cycle == "annual"
    assert CartItem.objects.filter(kind="addon", parent=parent).exists()  # the add-on stayed
    assert "Upgrade to" not in browser.get("/cart/").content.decode()  # Business suggests nothing further


def test_no_upgrade_is_offered_without_a_suggestion_or_when_it_is_hidden(shop, business, manager):
    browser = visitor()
    add_host(browser, shop)
    item = CartItem.objects.get()
    product_services.set_product_status(manager, business, "hidden")
    assert b"Upgrade to" not in browser.get("/cart/").content
    assert browser.post(f"/cart/items/{item.pk}/upgrade/", follow=True).status_code == 200
    item.refresh_from_db()
    assert item.product == shop["product"]
    with pytest.raises(ServiceError):
        services.upgrade_item(None, item)


def test_an_upgrade_that_would_duplicate_a_line_is_refused(shop, business):
    browser = visitor()
    add_host(browser, shop)
    browser.post("/cart/add/hosting/", {"product": business.pk, "cycle": "annual", "domain": "example.com"})
    starter = CartItem.objects.get(product=shop["product"])
    with pytest.raises(ServiceError) as exc:
        services.upgrade_item(None, starter)
    assert exc.value.code == "duplicate_item"


def test_recommended_addons_come_first_and_are_labelled(shop, manager):
    other = product_services.create_addon(manager, {"name": "Antivirus"})
    product_services.set_price(manager, other, billing_cycle=BillingCycle.ANNUAL, price="9.00")
    product_services.set_addon_status(manager, other, "active")
    product_services.update_product(manager, shop["product"], {"recommended_addons": [other]})
    browser = visitor()
    add_host(browser, shop)
    page = browser.get("/cart/").content.decode()
    assert page.index("Antivirus") < page.index("Backups") and "Recommended" in page
    detail = browser.get(f"/products/{shop['product'].slug}/").content.decode()
    assert "Works well with this plan" in detail and "Antivirus" in detail


def test_the_cart_offers_to_register_the_domain_of_a_plan(shop):
    browser = visitor()
    add_host(browser, shop)
    page = browser.get("/cart/").content.decode()
    assert "register your domain" in page and "example.com" in page and "12.00" in page
    browser.post("/cart/add/domain/", {"domain": "example.com", "years": 1})
    assert "register your domain" not in browser.get("/cart/").content.decode()


def test_the_plan_page_points_to_the_next_plan(shop, business):
    page = visitor().get(f"/products/{shop['product'].slug}/").content.decode()
    assert "Need more room?" in page and f"/products/{business.slug}/" in page


def test_staff_can_set_the_suggestions_and_a_plan_cannot_upgrade_to_itself(manager, shop, business):
    product_services.update_product(manager, business, {"upsell_product": shop["product"],
                                                        "recommended_addons": [shop["addon"]]})
    business.refresh_from_db()
    assert business.upsell_product == shop["product"] and list(business.recommended_addons.all()) == [shop["addon"]]
    assert AuditEvent.objects.filter(action="product.updated").latest("id").metadata["fields"] == [
        "recommended_addons", "upsell_product"]
    with pytest.raises(Exception):
        product_services.update_product(manager, business, {"upsell_product": business})
    browser = visitor()
    browser.force_login(manager)
    page = browser.get(f"/staff/products/{business.slug}/edit/")
    assert page.status_code == 200 and b"Suggest this bigger plan" in page.content and b"Recommended add-ons" in page.content


def test_old_guest_carts_are_cleared_and_recent_ones_kept(shop):
    old, recent = visitor(), visitor()
    add_host(old, shop)
    add_host(recent, shop, domain="two.com")
    Cart.objects.filter(items__domain_name="example.com").update(updated_at=timezone.now() - timedelta(days=40))
    assert services.purge_guest_carts() == 1
    assert Cart.objects.count() == 1 and CartItem.objects.get().domain_name == "two.com"


def test_clearing_guest_carts_never_touches_an_account_cart(shop, owner, client_obj):
    services.add_hosting(owner, client_obj, shop["product"], "kept.com", "annual")
    Cart.objects.filter(user=owner).update(updated_at=timezone.now() - timedelta(days=90))
    assert services.purge_guest_carts() == 0 and Cart.objects.filter(user=owner).exists()
