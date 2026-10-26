"""Bad or odd input to the cart, the coupon box, guest checkout and staff Add Order: each is answered in plain words and changes nothing."""
import pytest
from django.contrib.auth import get_user_model

from apps.orders import services
from apps.orders.models import CartItem, Order

pytestmark = pytest.mark.django_db
User = get_user_model()


def said(response, text):
    assert text.encode() in response.content, response.content.decode()[:400]


# --- the customer's cart ----------------------------------------------------------------------------------------------------------

def test_cart_forms_with_nothing_filled_in_say_what_is_wrong_and_leave_the_cart_alone(client, owner, shop):
    client.force_login(owner)
    client.post("/cart/add/hosting/", {"product": shop["product"].pk, "cycle": "annual", "domain": "keep.example"})
    item = CartItem.objects.get(domain_name="keep.example")
    for path in ("/cart/add/addon/", "/cart/addons/toggle/", f"/cart/items/{item.pk}/period/"):
        reply = client.post(path, {}, follow=True)
        assert reply.status_code == 200 and b"alert" in reply.content, path
    assert CartItem.objects.filter(domain_name="keep.example").count() == 1 and CartItem.objects.count() == 1


def test_an_unknown_billing_period_is_refused_and_the_old_one_stays(client, owner, shop):
    client.force_login(owner)
    client.post("/cart/add/hosting/", {"product": shop["product"].pk, "cycle": "annual", "domain": "period.example"})
    item = CartItem.objects.get(domain_name="period.example")
    client.post(f"/cart/items/{item.pk}/period/", {"option": "fortnightly"}, follow=True)
    item.refresh_from_db()
    assert item.billing_cycle == "annual"


def test_nobody_can_remove_or_change_someone_elses_cart_line(client, owner, shop, manager):
    from apps.clients import services as client_services

    other = client_services.create_client(manager, {"first_name": "Bo", "email": "bo@else.test"})
    services.add_hosting(other.contacts.get().user, other, shop["product"], "theirs.example", "annual")
    line = CartItem.objects.get(domain_name="theirs.example")
    client.force_login(owner)
    assert client.post(f"/cart/items/{line.pk}/remove/").status_code == 404
    assert client.post(f"/cart/items/{line.pk}/period/", {"option": "monthly"}).status_code == 404
    assert CartItem.objects.filter(pk=line.pk).exists()


# --- the coupon box -----------------------------------------------------------------------------------------------------------------

def test_a_coupon_needs_a_code_and_a_cart(client, owner, shop):
    client.force_login(owner)
    said(client.post("/cart/coupon/", {"code": "SAVE10"}, follow=True), "cart is empty")  # nothing to apply it to
    client.post("/cart/add/hosting/", {"product": shop["product"].pk, "cycle": "annual", "domain": "code.example"})
    reply = client.post("/cart/coupon/", {"code": ""}, follow=True)
    assert reply.status_code == 200 and b"alert" in reply.content
    reply = client.post("/cart/coupon/", {"code": "NOSUCHCODE"}, follow=True)
    assert reply.status_code == 200 and b"alert" in reply.content


def test_a_visitor_with_an_empty_cart_and_a_user_without_an_account_are_told_politely(client, make_user):
    said(client.post("/cart/coupon/", {"code": "X"}, follow=True), "cart is empty")
    client.force_login(make_user("no-account@example.com"))  # signed in, but belongs to no client account
    reply = client.post("/cart/coupon/", {"code": "X"}, follow=True)
    assert reply.status_code == 200 and (b"customer accounts" in reply.content or b"alert" in reply.content)


# --- checkout by a visitor who gets an account on the way -----------------------------------------------------------------

def test_if_the_order_fails_after_the_account_is_made_the_cart_is_kept_and_the_person_is_told(client, shop, monkeypatch):
    client.post("/cart/add/hosting/", {"product": shop["product"].pk, "cycle": "annual", "domain": "guest.example"})
    from apps.core.exceptions import ServiceError

    def broken(*args, **kwargs):
        raise ServiceError("The bank option is not available right now.", code="x")

    monkeypatch.setattr(services, "checkout", broken)
    reply = client.post("/checkout/", {"first_name": "Zed", "email": "zed@visitor.test", "country": "PK", "password": "Str0ng-Passw0rd!x",
                                       "password_confirm": "Str0ng-Passw0rd!x", "payment_method": shop["bank"].code}, follow=True)
    said(reply, "Your account was created, but the order was not placed")
    assert User.objects.filter(email="zed@visitor.test").exists() and Order.objects.count() == 0
    assert CartItem.objects.filter(domain_name="guest.example").exists()  # the cart came across with the new account


# --- staff Add Order ---------------------------------------------------------------------------------------------------------------------

def test_staff_can_build_an_order_with_a_domain_a_transfer_and_a_coupon_and_take_things_out(client, manager, client_obj, shop):
    from apps.billing import services as billing

    billing.save_coupon(manager, "STAFF5", {"discount_type": "percent", "value": 5})
    client.force_login(manager)
    here = f"/staff/orders/new/?client={client_obj.pk}"
    client.post(here, {"action": "add_hosting", "product": shop["product"].pk, "cycle": "annual", "domain": "built.example"})
    client.post(here, {"action": "add_domain", "domain": "built-domain.com", "years": 2})
    client.post(here, {"action": "add_transfer", "domain": "moving-in.com", "auth_code": "EPP-1"})
    kinds = sorted(CartItem.objects.filter(cart__client=client_obj).values_list("kind", flat=True))
    assert kinds == ["domain_register", "domain_transfer", "hosting"]
    client.post(here, {"action": "coupon", "code": "STAFF5"})
    assert services.get_open_cart(manager, client_obj).coupon.code == "STAFF5"
    client.post(here, {"action": "remove_coupon"})
    assert services.get_open_cart(manager, client_obj).coupon_id is None
    transfer = CartItem.objects.get(domain_name="moving-in.com")
    client.post(here, {"action": "remove_item", "item": transfer.pk})
    assert not CartItem.objects.filter(domain_name="moving-in.com").exists()


def test_staff_add_order_refuses_blank_domain_transfer_coupon_and_checkout_forms(client, manager, client_obj, shop):
    client.force_login(manager)
    here = f"/staff/orders/new/?client={client_obj.pk}"
    client.post(here, {"action": "add_hosting", "product": shop["product"].pk, "cycle": "annual", "domain": "ready.example"})
    for action in ("add_domain", "add_transfer", "coupon", "checkout"):
        reply = client.post(here, {"action": action}, follow=True)
        assert reply.status_code == 200 and b"alert" in reply.content, action
    assert Order.objects.count() == 0 and CartItem.objects.filter(cart__client=client_obj).count() == 1


def test_the_bulk_box_on_the_order_list_only_does_what_it_offers(client, manager):
    client.force_login(manager)
    said(client.post("/staff/orders/bulk/", {"do": "explode"}, follow=True), "Choose what to do with the selected orders")
