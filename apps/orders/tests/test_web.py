"""Cart, checkout and order pages (customer and staff)."""
import re

import pytest
from django.core import mail
from django.test import Client

from apps.accounts.roles import Role
from apps.clients import services as client_services
from apps.orders import services
from apps.orders.models import CartItem, Order

pytestmark = pytest.mark.django_db


def _add_host(client, shop, domain="example.com", cycle="annual"):
    return client.post("/cart/add/hosting/", {"product": shop["product"].pk, "cycle": cycle, "domain": domain})


def _place(client, shop, **extra):
    return client.post("/checkout/", {"payment_method": shop["bank"].code, **extra})


# --- Public catalogue pages lead into the cart --------------------------------------------------------

def test_plan_page_offers_ordering_only_to_signed_in_users(client, owner, shop):
    anonymous = client.get(f"/products/{shop['product'].slug}/")
    assert b"to order" in anonymous.content and b"cart/add/hosting" not in anonymous.content

    client.force_login(owner)
    page = client.get(f"/products/{shop['product'].slug}/")
    assert b'action="/cart/add/hosting/"' in page.content
    for value in (b'value="monthly"', b'value="annual"', b'value="custom:3"'):
        assert value in page.content  # every configured term, custom durations included


def test_domain_search_offers_add_to_cart(client, owner, shop):
    client.force_login(owner)
    page = client.get("/domains/?domain=example.com&years=2")
    assert b'action="/cart/add/domain/"' in page.content and b"Add to cart (2 years)" in page.content
    assert b"cart/add/domain" not in Client().get("/domains/?domain=example.com").content  # signed out


# --- Cart pages ------------------------------------------------------------------------------------------

def test_cart_pages_require_login(client):
    for path in ("/cart/", "/checkout/", "/account/orders/"):
        assert client.get(path).status_code == 302
    assert client.post("/cart/add/hosting/", {}).status_code == 302


def test_a_user_with_no_client_is_told_ordering_is_for_customers(client, manager):
    client.force_login(manager)  # staff are not client contacts
    page = client.get("/cart/")
    assert page.status_code == 200 and b"available to customer accounts" in page.content
    response = client.post("/cart/add/hosting/", {"product": 1, "cycle": "annual", "domain": "example.com"},
                           follow=True)
    assert b"available to customer accounts" in response.content and CartItem.objects.count() == 0


def test_add_hosting_shows_it_in_the_cart_with_server_side_prices(client, owner, shop):
    client.force_login(owner)
    response = _add_host(client, shop)
    assert response.status_code == 302 and response["Location"] == "/cart/"
    page = client.get("/cart/")
    assert b"Starter (Annually) - example.com" in page.content
    assert b"110.00" in page.content and b"Proceed to checkout" in page.content
    assert b"Prices are calculated by our servers" in page.content


def test_a_tampered_price_field_has_no_effect_on_the_page_flow(client, owner, shop):
    client.force_login(owner)
    client.post("/cart/add/hosting/", {"product": shop["product"].pk, "cycle": "annual", "domain": "example.com",
                                       "price": "0.01", "total": "0.01"})
    assert b"110.00" in client.get("/cart/").content


def test_invalid_selection_shows_a_message_and_adds_nothing(client, owner, shop):
    client.force_login(owner)
    response = client.post("/cart/add/hosting/", {"product": shop["product"].pk, "cycle": "custom:9",
                                                  "domain": "example.com"}, follow=True)
    assert b"not available" in response.content and CartItem.objects.count() == 0
    bad_cycle = client.post("/cart/add/hosting/", {"product": shop["product"].pk, "cycle": "weekly",
                                                   "domain": "example.com"}, follow=True)
    assert b"valid billing cycle" in bad_cycle.content
    missing = client.post("/cart/add/hosting/", {"product": 9999, "cycle": "annual", "domain": "example.com"},
                          follow=True)
    assert b"no longer available" in missing.content


def test_addons_are_offered_for_the_hosting_cycle_and_can_be_added(client, owner, shop):
    client.force_login(owner)
    _add_host(client, shop)
    cart = client.get("/cart/")
    assert b"Add to this plan" in cart.content and b"Backups" in cart.content
    assert b'value="annual"' in cart.content and b'value="one_time"' in cart.content
    assert b'value="monthly"' not in cart.content.split(b"Add to this plan")[1]  # wrong cycle: not offered

    host = CartItem.objects.get()
    client.post("/cart/add/addon/", {"parent_item": host.pk, "addon": shop["addon"].pk, "option": "one_time"})
    page = client.get("/cart/")
    assert b"Backups (one-time)" in page.content
    assert b"Add to this plan" not in page.content  # nothing left to offer


def test_cannot_attach_an_addon_to_someone_elses_cart_item(client, owner, shop, customer):
    client.force_login(owner)
    _add_host(client, shop)
    host = CartItem.objects.get()
    client.force_login(customer)
    response = client.post("/cart/add/addon/", {"parent_item": host.pk, "addon": shop["addon"].pk, "option": ""})
    assert response.status_code == 404


def test_add_domain_registration_and_transfer(client, owner, shop):
    client.force_login(owner)
    client.post("/cart/add/domain/", {"domain": "example.com", "years": 2})
    client.post("/cart/add/transfer/", {"domain": "moving.com", "auth_code": "epp-1"})
    page = client.get("/cart/")
    assert b"Domain registration: example.com (2 years)" in page.content
    assert b"Domain transfer: moving.com" in page.content and b"epp-1" not in page.content
    assert b"9.00" in page.content and b"24.00" in page.content


def test_remove_an_item_and_an_empty_cart_message(client, owner, shop):
    client.force_login(owner)
    _add_host(client, shop)
    item = CartItem.objects.get()
    client.post(f"/cart/items/{item.pk}/remove/")
    assert b"Your cart is empty" in client.get("/cart/").content


def test_cannot_remove_another_customers_item(client, owner, shop, customer):
    client.force_login(owner)
    _add_host(client, shop)
    item = CartItem.objects.get()
    client.force_login(customer)
    assert client.post(f"/cart/items/{item.pk}/remove/").status_code == 404
    assert CartItem.objects.filter(pk=item.pk).exists()


def test_coupon_can_be_applied_and_removed(client, owner, shop, coupon):
    client.force_login(owner)
    _add_host(client, shop)
    client.post("/cart/coupon/", {"code": "save10"})
    page = client.get("/cart/")
    assert b"SAVE10" in page.content and b"99.00" in page.content
    client.post("/cart/coupon/remove/")
    assert b"110.00" in client.get("/cart/").content and b"99.00" not in client.get("/cart/").content


def test_bad_coupon_message(client, owner, shop):
    client.force_login(owner)
    _add_host(client, shop)
    response = client.post("/cart/coupon/", {"code": "NOPE"}, follow=True)
    assert b"not valid" in response.content


def test_a_coupon_that_lapses_is_flagged_on_the_cart(client, owner, shop, coupon, manager):
    from apps.billing import services as billing

    client.force_login(owner)
    _add_host(client, shop)
    client.post("/cart/coupon/", {"code": "SAVE10"})
    billing.set_coupon_active(manager, coupon, False)
    page = client.get("/cart/")
    assert b"not valid" in page.content and b"Proceed to checkout" not in page.content


def test_the_nav_shows_the_cart_count(client, owner, shop):
    client.force_login(owner)
    empty = client.get("/account/profile/").content
    assert b"icon-badge" not in re.search(rb'title="Cart".*?</a>', empty, re.S).group(0)
    _add_host(client, shop)
    client.post("/cart/add/domain/", {"domain": "example.com", "years": 1})
    assert re.search(rb'title="Cart".*?<span class="icon-badge">2<', client.get("/account/profile/").content, re.S)


# --- Checkout ---------------------------------------------------------------------------------------------

def test_checkout_with_an_empty_cart_goes_back_to_the_cart(client, owner, shop):
    client.force_login(owner)
    response = client.get("/checkout/")
    assert response.status_code == 302 and response["Location"] == "/cart/"


def test_checkout_page_shows_billing_details_methods_and_summary(client, owner, shop):
    client.force_login(owner)
    _add_host(client, shop)
    page = client.get("/checkout/")
    assert page.status_code == 200
    for expected in (b"Ada Lovelace", b"Acme Ltd", b"ada@acme.test", b"Bank transfer", b"Pay to IBAN PK00 TEST 0001.",
                     b"Starter (Annually) - example.com", b"110.00", b"Edit billing details"):
        assert expected in page.content


def test_placing_an_order_redirects_to_it_and_sends_the_email(client, owner, shop):
    client.force_login(owner)
    _add_host(client, shop)
    mail.outbox.clear()
    response = _place(client, shop, notes="thanks")
    order = Order.objects.get()
    assert response.status_code == 302 and response["Location"] == f"/account/orders/{order.pk}/"
    assert order.notes == "thanks" and len(mail.outbox) == 1
    detail = client.get(response["Location"])
    assert order.reference.encode() in detail.content
    assert b"Awaiting payment" in detail.content and b"Pay to IBAN PK00 TEST 0001." in detail.content
    assert b"Cart" in detail.content and b"Cart (" not in detail.content  # cart emptied


def test_checkout_requires_choosing_a_payment_method(client, owner, shop):
    client.force_login(owner)
    _add_host(client, shop)
    response = client.post("/checkout/", {})
    assert response.status_code == 200 and b"required" in response.content and not Order.objects.exists()


def test_checkout_surfaces_a_problem_that_appeared_since_adding(client, owner, shop, manager):
    from apps.products import services as product_services

    client.force_login(owner)
    _add_host(client, shop)
    product_services.set_product_status(manager, shop["product"], "retired")
    response = _place(client, shop)
    assert response.status_code == 200 and b"no longer available" in response.content
    assert not Order.objects.exists()


def test_a_technical_contact_can_place_an_order(client, manager, client_obj, shop, make_user):
    tech = make_user("tech@example.com")
    client_services.add_contact(manager, client_obj, email="tech@example.com", role="technical")
    client.force_login(tech)
    _add_host(client, shop)
    assert _place(client, shop).status_code == 302 and Order.objects.count() == 1


# --- Customer orders ----------------------------------------------------------------------------------------

def test_order_list_detail_and_cancel(client, owner, shop):
    client.force_login(owner)
    _add_host(client, shop)
    _place(client, shop)
    order = Order.objects.get()
    assert order.reference.encode() in client.get("/account/orders/").content

    client.post(f"/account/orders/{order.pk}/cancel/", {"reason": "oops"})
    order.refresh_from_db()
    assert order.status == "cancelled" and order.cancel_reason == "oops"
    detail = client.get(f"/account/orders/{order.pk}/")
    assert b"Cancelled" in detail.content and b"Reason: oops" in detail.content and b"Cancel order" not in detail.content


def test_customers_cannot_see_or_cancel_other_customers_orders(client, owner, shop, customer):
    client.force_login(owner)
    _add_host(client, shop)
    _place(client, shop)
    order = Order.objects.get()
    client.force_login(customer)
    assert client.get(f"/account/orders/{order.pk}/").status_code == 404
    assert client.post(f"/account/orders/{order.pk}/cancel/").status_code == 404
    assert f"/account/orders/{order.pk}/".encode() not in client.get("/account/orders/").content


# --- Staff ------------------------------------------------------------------------------------------------------

def _placed(client, owner, shop):
    client.force_login(owner)
    _add_host(client, shop)
    _place(client, shop)
    return Order.objects.get()


def test_staff_order_pages_need_permission(client, owner, shop, customer):
    order = _placed(client, owner, shop)
    client.logout()
    assert client.get("/staff/orders/").status_code == 302
    client.force_login(customer)
    assert client.get("/staff/orders/").status_code == 403
    assert client.get(f"/staff/orders/{order.pk}/").status_code == 403
    assert client.post(f"/staff/orders/{order.pk}/cancel/").status_code == 403


def test_staff_list_search_detail_and_cancel(client, owner, shop, manager):
    order = _placed(client, owner, shop)
    client.force_login(manager)
    listing = client.get("/staff/orders/")
    assert order.reference.encode() in listing.content and b"Acme Ltd" in listing.content
    assert order.reference.encode() in client.get(f"/staff/orders/?q={order.reference}").content
    # Look for the row's link, not the reference text: the search box's placeholder is an example reference (O000123),
    # which is this order's reference whenever its id happens to be 123 on a database whose sequence has advanced.
    row_link = f'href="/staff/orders/{order.pk}/"'.encode()
    assert row_link in listing.content
    assert row_link not in client.get("/staff/orders/?q=zzz-none").content
    assert row_link not in client.get("/staff/orders/?status=paid").content

    detail = client.get(f"/staff/orders/{order.pk}/")
    assert detail.status_code == 200 and b"Cancel order" in detail.content and b"Bank transfer" in detail.content
    client.post(f"/staff/orders/{order.pk}/cancel/", {"reason": "duplicate"})
    order.refresh_from_db()
    assert order.status == "cancelled" and order.cancel_reason == "duplicate"


def test_support_agent_can_view_orders_but_not_cancel(client, owner, shop, staff):
    order = _placed(client, owner, shop)
    client.force_login(staff(Role.SUPPORT_AGENT))
    detail = client.get(f"/staff/orders/{order.pk}/")
    assert detail.status_code == 200 and b"Cancel order" not in detail.content
    assert client.post(f"/staff/orders/{order.pk}/cancel/").status_code == 403


def test_staff_nav_links(client, manager, customer):
    client.force_login(manager)
    content = client.get("/account/profile/").content
    assert b"/staff/orders/" in content and b"/staff/billing/" in content
    client.force_login(customer)
    content = client.get("/account/profile/").content
    assert b"/staff/orders/" not in content and b"/staff/billing/" not in content


def test_cart_service_and_page_share_one_cart(client, owner, client_obj, shop):
    client.force_login(owner)
    _add_host(client, shop)
    assert services.get_open_cart(owner, client_obj).items.count() == 1
