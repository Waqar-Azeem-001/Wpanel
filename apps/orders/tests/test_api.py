"""Cart, checkout and orders over the API (the same service layer the web pages use)."""
from decimal import Decimal

import pytest

from apps.accounts.roles import Role
from apps.billing import services as billing
from apps.clients import services as client_services
from apps.clients.models import ContactRole
from apps.orders import services
from apps.orders.models import CartItem, Order

pytestmark = pytest.mark.django_db

D = Decimal
HOST = {"kind": "hosting", "domain": "example.com", "billing_cycle": "annual"}


def _hosting(shop, **extra):
    return {**HOST, "product_id": shop["product"].pk, **extra}


# --- Authentication -------------------------------------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("get", "/api/v1/cart/"), ("post", "/api/v1/cart/items/"), ("delete", "/api/v1/cart/items/1/"),
    ("post", "/api/v1/cart/coupon/"), ("post", "/api/v1/cart/checkout/"), ("get", "/api/v1/orders/"),
])
def test_everything_requires_authentication(api, method, path):
    assert getattr(api, method)(path).status_code == 401


# --- The cart ------------------------------------------------------------------------------------------

def test_a_new_cart_is_empty(api, owner, client_obj):
    api.force_authenticate(owner)
    body = api.get("/api/v1/cart/").json()
    assert body["items"] == [] and body["total"] == "0.00" and body["client_id"] == client_obj.pk
    assert body["is_valid"] is False  # nothing to check out yet


def test_add_hosting_addon_and_domains_then_read_the_priced_cart(api, owner, shop):
    api.force_authenticate(owner)
    added = api.post("/api/v1/cart/items/", _hosting(shop))
    assert added.status_code == 201
    host_id = added.json()["items"][0]["id"]
    api.post("/api/v1/cart/items/", {"kind": "addon", "addon_id": shop["addon"].pk, "parent_item_id": host_id})
    api.post("/api/v1/cart/items/", {"kind": "domain_register", "domain": "example.com", "years": 2})
    api.post("/api/v1/cart/items/", {"kind": "domain_transfer", "domain": "moving.com", "auth_code": "epp-1"})

    body = api.get("/api/v1/cart/").json()
    assert [i["kind"] for i in body["items"]] == ["hosting", "addon", "domain_register", "domain_transfer"]
    assert [i["total"] for i in body["items"]] == ["110.00", "20.00", "24.00", "9.00"]
    assert body["items"][1]["parent_id"] == host_id
    assert (body["subtotal"], body["total"], body["currency"], body["is_valid"]) == ("163.00", "163.00", "USD", True)


def test_the_api_never_returns_a_transfer_auth_code(api, owner, shop):
    api.force_authenticate(owner)
    response = api.post("/api/v1/cart/items/", {"kind": "domain_transfer", "domain": "moving.com",
                                                "auth_code": "super-secret-epp"})
    assert "super-secret-epp" not in response.content.decode()
    assert "super-secret-epp" not in api.get("/api/v1/cart/").content.decode()


def test_prices_sent_by_the_client_are_ignored(api, owner, shop):
    """Roadmap section 23: never trust price, total, discount, tax, duration or credit from the browser."""
    api.force_authenticate(owner)
    response = api.post("/api/v1/cart/items/", _hosting(
        shop, price="0.01", unit_price="0.01", setup_fee="0", total="0.01", discount="99", tax="0",
        subtotal="0.01", expires_at="2099-01-01"))
    assert response.status_code == 201
    body = api.get("/api/v1/cart/").json()
    assert body["items"][0]["unit_price"] == "100.00" and body["items"][0]["setup_fee"] == "10.00"
    assert body["total"] == "110.00"

    order = api.post("/api/v1/cart/checkout/", {"payment_method": shop["bank"].code, "total": "0.01",
                                                "subtotal": "0.01", "status": "paid"}).json()
    assert order["total"] == "110.00" and order["status"] == "pending_payment"


@pytest.mark.parametrize("payload,missing", [
    ({"kind": "hosting", "domain": "example.com", "billing_cycle": "annual"}, "product"),
    ({"kind": "hosting", "billing_cycle": "annual"}, "domain"),
    ({"kind": "domain_register"}, "domain"),
    ({"kind": "domain_transfer", "domain": "moving.com"}, "auth_code"),
    ({"kind": "addon"}, "addon"),
])
def test_each_kind_requires_its_own_fields(api, owner, shop, payload, missing):
    api.force_authenticate(owner)
    response = api.post("/api/v1/cart/items/", payload)
    assert response.status_code == 400 and missing in response.json()["error"]["details"]


def test_invalid_selections_are_rejected_with_a_reason(api, owner, shop):
    api.force_authenticate(owner)
    bad_cycle = api.post("/api/v1/cart/items/", _hosting(shop, billing_cycle="biennial"))
    assert bad_cycle.status_code == 400 and bad_cycle.json()["error"]["code"] == "price_not_found"
    bad_domain = api.post("/api/v1/cart/items/", {"kind": "domain_register", "domain": "example.zzz"})
    assert bad_domain.json()["error"]["code"] == "tld_not_supported"
    assert api.post("/api/v1/cart/items/", _hosting(shop, product_id=99999)).status_code == 400


def test_remove_an_item(api, owner, shop):
    api.force_authenticate(owner)
    item_id = api.post("/api/v1/cart/items/", _hosting(shop)).json()["items"][0]["id"]
    assert api.delete(f"/api/v1/cart/items/{item_id}/").status_code == 204
    assert api.get("/api/v1/cart/").json()["items"] == []


def test_another_users_cart_item_looks_like_it_does_not_exist(api, owner, shop, customer):
    api.force_authenticate(owner)
    item_id = api.post("/api/v1/cart/items/", _hosting(shop)).json()["items"][0]["id"]
    api.force_authenticate(customer)
    assert api.delete(f"/api/v1/cart/items/{item_id}/").status_code == 404
    assert CartItem.objects.filter(pk=item_id).exists()
    response = api.post("/api/v1/cart/items/", {"kind": "addon", "addon_id": shop["addon"].pk,
                                                "parent_item_id": item_id})
    assert response.status_code == 404


def test_multi_client_users_must_say_which_client(api, owner, manager, client_obj, shop):
    second = client_services.create_client(manager, {"first_name": "B", "email": "b@x.test"})
    client_services.add_contact(manager, second, email=owner.email, role=ContactRole.BILLING)
    api.force_authenticate(owner)
    ambiguous = api.get("/api/v1/cart/")
    assert ambiguous.status_code == 400 and ambiguous.json()["error"]["code"] == "client_required"
    assert api.get(f"/api/v1/cart/?client_id={second.pk}").json()["client_id"] == second.pk
    added = api.post("/api/v1/cart/items/", _hosting(shop, client_id=second.pk))
    assert added.status_code == 201 and added.json()["client_id"] == second.pk
    assert api.get(f"/api/v1/cart/?client_id={client_obj.pk}").json()["items"] == []  # separate carts


def test_a_stranger_cannot_use_someone_elses_client_id(api, customer, client_obj, shop):
    api.force_authenticate(customer)
    assert api.get(f"/api/v1/cart/?client_id={client_obj.pk}").status_code == 404
    assert api.post("/api/v1/cart/items/", _hosting(shop, client_id=client_obj.pk)).status_code == 404


def test_staff_can_build_a_cart_on_a_clients_behalf(api, manager, client_obj, shop):
    api.force_authenticate(manager)
    assert api.post("/api/v1/cart/items/", _hosting(shop, client_id=client_obj.pk)).status_code == 201


# --- Coupons -----------------------------------------------------------------------------------------------

def test_apply_and_remove_a_coupon(api, owner, shop, coupon):
    api.force_authenticate(owner)
    api.post("/api/v1/cart/items/", _hosting(shop))
    applied = api.post("/api/v1/cart/coupon/", {"code": "save10"})
    assert applied.status_code == 200
    body = applied.json()
    assert body["coupon"] == {"code": "SAVE10", "discount": "11.00"} and body["total"] == "99.00"
    removed = api.delete("/api/v1/cart/coupon/").json()
    assert removed["coupon"] is None and removed["total"] == "110.00"


def test_a_bad_coupon_is_rejected(api, owner, shop):
    api.force_authenticate(owner)
    api.post("/api/v1/cart/items/", _hosting(shop))
    response = api.post("/api/v1/cart/coupon/", {"code": "NOPE"})
    assert response.status_code == 400 and response.json()["error"]["code"] == "coupon_invalid"


# --- Checkout and orders -------------------------------------------------------------------------------------

def test_checkout_returns_the_order_and_empties_the_cart(api, owner, shop, coupon, manager):
    billing.save_tax_rule(manager, "US", name="Sales tax", rate=D("10"))
    api.force_authenticate(owner)
    api.post("/api/v1/cart/items/", _hosting(shop))
    api.post("/api/v1/cart/coupon/", {"code": "SAVE10"})
    response = api.post("/api/v1/cart/checkout/", {"payment_method": shop["bank"].code, "notes": "hello"})
    assert response.status_code == 201
    order = response.json()
    assert order["reference"].startswith("O") and order["status"] == "pending_payment"
    assert (order["subtotal"], order["discount_total"], order["tax_total"], order["total"]) == (
        "110.00", "11.00", "9.90", "108.90")
    assert order["coupon_code"] == "SAVE10" and order["payment_method_name"] == "Bank transfer"
    assert order["billing_company"] == "Acme Ltd" and order["notes"] == "hello"
    assert order["items"][0]["description"] == "Starter (Annually) - example.com"
    assert api.get("/api/v1/cart/").json()["items"] == []


def test_checkout_errors(api, owner, shop):
    api.force_authenticate(owner)
    empty = api.post("/api/v1/cart/checkout/", {"payment_method": shop["bank"].code})
    assert empty.status_code == 400 and empty.json()["error"]["code"] == "cart_empty"
    api.post("/api/v1/cart/items/", _hosting(shop))
    bad_method = api.post("/api/v1/cart/checkout/", {"payment_method": "cash-in-a-bag"})
    assert bad_method.json()["error"]["code"] == "payment_method_invalid"
    assert api.post("/api/v1/cart/checkout/", {}).status_code == 400  # payment_method is required
    assert not Order.objects.exists()


def _order(api, owner, shop):
    api.force_authenticate(owner)
    api.post("/api/v1/cart/items/", _hosting(shop))
    return api.post("/api/v1/cart/checkout/", {"payment_method": shop["bank"].code}).json()


def test_orders_list_and_detail_are_scoped_to_the_customer(api, owner, shop, customer, manager):
    order = _order(api, owner, shop)
    assert [o["id"] for o in api.get("/api/v1/orders/").json()["results"]] == [order["id"]]
    assert api.get(f"/api/v1/orders/{order['id']}/").status_code == 200

    api.force_authenticate(customer)
    assert api.get("/api/v1/orders/").json()["count"] == 0
    assert api.get(f"/api/v1/orders/{order['id']}/").status_code == 404

    api.force_authenticate(manager)
    assert api.get("/api/v1/orders/").json()["count"] == 1
    assert api.get(f"/api/v1/orders/?search={order['reference']}").json()["count"] == 1
    assert api.get("/api/v1/orders/?status=paid").json()["count"] == 0


def test_the_order_representation_hides_the_auth_code(api, owner, shop):
    api.force_authenticate(owner)
    api.post("/api/v1/cart/items/", {"kind": "domain_transfer", "domain": "moving.com", "auth_code": "epp-hidden"})
    order = api.post("/api/v1/cart/checkout/", {"payment_method": shop["bank"].code})
    assert order.status_code == 201 and "epp-hidden" not in order.content.decode()
    detail = api.get(f"/api/v1/orders/{order.json()['id']}/")
    assert "epp-hidden" not in detail.content.decode()


def test_orders_are_read_only_apart_from_cancelling(api, owner, shop):
    order = _order(api, owner, shop)
    assert api.patch(f"/api/v1/orders/{order['id']}/", {"total": "0.01"}).status_code == 405
    assert api.delete(f"/api/v1/orders/{order['id']}/").status_code == 405
    assert api.post("/api/v1/orders/", {"total": "1"}).status_code == 405


def test_cancel_an_order(api, owner, shop, customer):
    order = _order(api, owner, shop)
    api.force_authenticate(customer)
    assert api.post(f"/api/v1/orders/{order['id']}/cancel/").status_code == 404  # can't even see it
    api.force_authenticate(owner)
    cancelled = api.post(f"/api/v1/orders/{order['id']}/cancel/", {"reason": "changed my mind"})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled" and cancelled.json()["cancel_reason"] == "changed my mind"
    again = api.post(f"/api/v1/orders/{order['id']}/cancel/")
    assert again.status_code == 400 and again.json()["error"]["code"] == "invalid_status"


def test_support_agent_can_read_orders_but_not_cancel_them(api, owner, shop, staff):
    order = _order(api, owner, shop)
    api.force_authenticate(staff(Role.SUPPORT_AGENT))
    assert api.get(f"/api/v1/orders/{order['id']}/").status_code == 200
    assert api.post(f"/api/v1/orders/{order['id']}/cancel/").status_code == 403


def test_service_and_api_agree(api, owner, client_obj, shop):
    """The API is a thin layer: its numbers are the pricing engine's numbers."""
    api.force_authenticate(owner)
    api.post("/api/v1/cart/items/", _hosting(shop))
    api.post("/api/v1/cart/items/", {"kind": "domain_register", "domain": "example.com", "years": 3})
    from apps.orders import pricing

    priced = pricing.price_cart(services.get_open_cart(owner, client_obj))
    assert api.get("/api/v1/cart/").json()["total"] == str(priced.total) == "146.00"
