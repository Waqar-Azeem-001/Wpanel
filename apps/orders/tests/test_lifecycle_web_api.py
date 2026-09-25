"""The staff order screens (groups, actions, Add Order), the customer's progress view, and the lifecycle API."""
from decimal import Decimal

import pytest

from apps.accounts.roles import Role
from apps.billing import payments
from apps.hosting.models import HostingAccount
from apps.orders import services, staff_actions
from apps.orders.models import Order, OrderStatus
from apps.products import services as product_services

pytestmark = pytest.mark.django_db

D = Decimal


@pytest.fixture(autouse=True)
def auto_fulfil(settings):
    settings.ORDER_AUTO_FULFIL = True


@pytest.fixture
def server(manager, product):
    srv = product_services.create_server(manager, {"name": "srv1", "hostname": "srv1.example.com"})
    product_services.set_product_servers(manager, product, [srv.pk])
    return srv


def place(owner, client_obj, shop, name):
    host = services.add_hosting(owner, client_obj, shop["product"], name, "annual")
    services.add_addon(owner, host, shop["addon"])
    services.add_domain_registration(owner, client_obj, name, 1)
    return services.checkout(owner, services.get_open_cart(owner, client_obj), payment_method_code="bank-transfer")


def pay(manager, order):
    invoice = order.invoices.get()
    payments.record_payment(manager, invoice, amount=str(invoice.total))
    order.refresh_from_db()
    return order


def reload(obj):
    obj.refresh_from_db()
    return obj


# --- Staff screens: groups ---------------------------------------------------------------------------

def test_the_groups_and_their_counts(client, manager, owner, client_obj, shop, server):
    pending = place(owner, client_obj, shop, "pending.com")
    active = pay(manager, place(owner, client_obj, shop, "active.com"))
    fraud = place(owner, client_obj, shop, "fraud.com")
    staff_actions.mark_fraud(manager, fraud, reason="Odd")
    cancelled = place(owner, client_obj, shop, "cancelled.com")
    services.cancel_order(owner, cancelled)
    client.force_login(manager)

    page = client.get("/staff/orders/").content
    for label in (b"All <span", b"Pending <span", b"Active <span", b"Fraud <span", b"Cancelled <span"):
        assert label in page
    assert b'Active <span class="count">1</span>' in page and b'Fraud <span class="count">1</span>' in page
    assert b'All <span class="count">4</span>' in page

    def refs(group):
        content = client.get(f"/staff/orders/?group={group}").content.decode()
        return {o.reference for o in (pending, active, fraud, cancelled) if o.reference in content}

    assert refs("pending") == {pending.reference} and refs("active") == {active.reference}
    assert refs("fraud") == {fraud.reference} and refs("cancelled") == {cancelled.reference}
    assert refs("nonsense") == {pending.reference, active.reference, fraud.reference, cancelled.reference}
    assert active.reference.encode() in client.get("/staff/orders/?group=active&q=active.com").content


def test_add_order_is_offered_only_to_those_who_can_manage_orders(client, staff, manager):
    client.force_login(manager)
    assert b"Add order" in client.get("/staff/orders/").content
    client.force_login(staff(Role.SUPPORT_AGENT))
    assert b"Add order" not in client.get("/staff/orders/").content
    assert client.get("/staff/orders/new/").status_code == 403


# --- Staff screens: an order --------------------------------------------------------------------------

def test_the_order_page_shows_fulfilment_history_and_the_right_buttons(client, manager, owner, client_obj, shop, server):
    order = pay(manager, place(owner, client_obj, shop, "shown.com"))
    client.force_login(manager)
    page = client.get(f"/staff/orders/{order.pk}/").content.decode()
    assert "paid &rarr; processing" in page and "provisioning &rarr; active" in page and "by system" in page
    assert "hosting shown" in page and "shown.com" in page and "Done" in page
    assert "Suspend" in page and "Terminate" in page and "Flag as fraud" not in page and "Retry" not in page


def test_lifecycle_actions_from_the_order_page(client, manager, owner, client_obj, shop, server):
    order = pay(manager, place(owner, client_obj, shop, "actions.com"))
    client.force_login(manager)
    client.post(f"/staff/orders/{order.pk}/suspend/", {"reason": "Abuse"})
    assert reload(order).status == OrderStatus.SUSPENDED
    assert b"Reactivate" in client.get(f"/staff/orders/{order.pk}/").content
    client.post(f"/staff/orders/{order.pk}/unsuspend/")
    assert reload(order).status == OrderStatus.ACTIVE
    client.post(f"/staff/orders/{order.pk}/terminate/", {"reason": "Requested"})
    assert reload(order).status == OrderStatus.TERMINATED
    assert b"This order is closed" in client.get(f"/staff/orders/{order.pk}/").content
    again = client.post(f"/staff/orders/{order.pk}/suspend/", follow=True)
    assert b"Only an active order can be suspended" in again.content


def test_fraud_and_retry_from_the_order_page(client, manager, owner, client_obj, shop, product):
    order = place(owner, client_obj, shop, "fraudy.com")
    client.force_login(manager)
    client.post(f"/staff/orders/{order.pk}/fraud/", {"reason": "Mismatch"})
    assert reload(order).status == OrderStatus.FRAUD
    page = client.get(f"/staff/orders/{order.pk}/").content
    assert b"Release the fraud hold" in page and b"Mismatch" in page
    client.post(f"/staff/orders/{order.pk}/clear-fraud/", {"reason": "Verified"})
    assert reload(order).status == OrderStatus.PENDING_PAYMENT

    pay(manager, order)  # no server is mapped: fulfilment fails
    assert reload(order).status == OrderStatus.FAILED
    page = client.get(f"/staff/orders/{order.pk}/").content
    assert b"Retry fulfilment" in page and b"server" in page.lower()
    failed = client.post(f"/staff/orders/{order.pk}/retry/", follow=True)
    assert b"still could not be fulfilled" in failed.content


def test_only_those_who_manage_orders_may_act(client, staff, manager, owner, client_obj, shop, server):
    order = place(owner, client_obj, shop, "guarded.com")
    for user in (owner, staff(Role.SUPPORT_AGENT)):
        client.force_login(user)
        for action in ("fraud", "clear-fraud", "retry", "suspend", "unsuspend", "terminate"):
            assert client.post(f"/staff/orders/{order.pk}/{action}/").status_code == 403
    assert reload(order).status == OrderStatus.PENDING_PAYMENT


# --- What the customer sees --------------------------------------------------------------------------------

@pytest.mark.parametrize("status,expected", [
    (OrderStatus.PAID, b"Setting up your order"), (OrderStatus.PROVISIONING, b"Setting up your order"),
    (OrderStatus.FAILED, b"We are finishing your order"), (OrderStatus.FRAUD, b"under review"),
    (OrderStatus.TERMINATED, b"have been terminated"),
])
def test_the_customer_is_told_where_their_order_is_without_internal_detail(
        client, owner, client_obj, shop, status, expected):
    order = place(owner, client_obj, shop, "progress.com")
    Order.objects.filter(pk=order.pk).update(status=status, status_reason="WHM exploded: internal detail")
    client.force_login(owner)
    page = client.get(f"/account/orders/{order.pk}/").content
    assert expected in page and b"WHM exploded" not in page


def test_an_active_order_links_to_its_services(client, manager, owner, client_obj, shop, server):
    order = pay(manager, place(owner, client_obj, shop, "linked.com"))
    account = HostingAccount.objects.get(domain="linked.com")
    client.force_login(owner)
    page = client.get(f"/account/orders/{order.pk}/").content
    assert b"Your services" in page and f"/account/hosting/{account.pk}/".encode() in page
    assert b"Domain linked.com" in page


# --- Add Order ------------------------------------------------------------------------------------------------

def test_staff_add_an_order_for_a_client(client, manager, owner, client_obj, shop, server, coupon):
    client.force_login(manager)
    picker = client.get("/staff/orders/new/?q=acme")
    assert client_obj.email.encode() in picker.content and b"orders/new/?client=" in picker.content
    here = f"/staff/orders/new/?client={client_obj.pk}"
    page = client.get(here)
    assert b"Add order for Acme Ltd" in page.content and b"The order is empty" in page.content

    client.post(here, {"action": "add_hosting", "product": shop["product"].pk, "cycle": "annual",
                       "domain": "byteam.com"})
    client.post(here, {"action": "add_domain", "domain": "byteam.com", "years": 2})
    client.post(here, {"action": "add_transfer", "domain": "moving.com", "auth_code": "epp-secret"})
    client.post(here, {"action": "coupon", "code": "save10"})
    page = client.get(here).content
    assert b"Starter (Annually) - byteam.com" in page and b"Domain registration: byteam.com (2 years)" in page
    assert b"SAVE10" in page and b"epp-secret" not in page
    removed = client.post(here, {"action": "remove_item", "item": 999999})  # not in this cart
    assert removed.status_code == 404
    response = client.post(here, {"action": "checkout", "payment_method": "bank-transfer", "notes": "By phone"})
    order = Order.objects.get()
    assert response["Location"] == f"/staff/orders/{order.pk}/"
    assert order.client == client_obj and order.placed_by == manager and order.coupon_code == "SAVE10"
    assert order.invoices.get().status == "unpaid" and order.notes == "By phone"
    # The client sees it, and paying it fulfils it like any other order.
    pay(manager, order)
    assert reload(order).status == OrderStatus.ACTIVE
    client.force_login(owner)
    assert order.reference.encode() in client.get("/account/orders/").content


def test_add_order_reports_problems_and_never_takes_a_price(client, manager, client_obj, shop):
    client.force_login(manager)
    here = f"/staff/orders/new/?client={client_obj.pk}"
    nothing = client.post(here, {"action": "checkout", "payment_method": "bank-transfer"}, follow=True)
    assert b"cart is empty" in nothing.content and Order.objects.count() == 0
    bad = client.post(here, {"action": "add_hosting", "product": 9999, "cycle": "annual", "domain": "x.com"},
                      follow=True)
    assert b"Select a valid choice" in bad.content
    assert b"Choose an action" in client.post(here, {"action": "bogus"}, follow=True).content
    client.post(here, {"action": "add_hosting", "product": shop["product"].pk, "cycle": "annual",
                       "domain": "priced.com", "unit_price": "0.01", "total": "0.01"})
    page = client.get(here).content
    assert b"100.00" in page and b"0.01" not in page  # the catalogue decides
    assert client.get("/staff/orders/new/?client=999999").status_code == 404


# --- API -------------------------------------------------------------------------------------------------------------

def test_the_order_api_carries_fulfilment_and_hides_internal_detail_from_customers(
        api, manager, owner, client_obj, shop, product):
    order = pay(manager, place(owner, client_obj, shop, "apiorder.com"))  # fails: no server
    api.force_authenticate(owner)
    body = api.get(f"/api/v1/orders/{order.pk}/").json()
    assert body["status"] == "failed" and body["status_reason"] == ""
    assert {i["fulfilment_status"] for i in body["items"]} >= {"failed"} and all(
        i["fulfilment_error"] == "" for i in body["items"])
    api.force_authenticate(manager)
    body = api.get(f"/api/v1/orders/{order.pk}/").json()
    assert "server" in body["status_reason"].lower()
    assert any("server" in i["fulfilment_error"].lower() for i in body["items"])
    assert all("auth_code" not in json_key for i in body["items"] for json_key in i)


def test_the_group_filter(api, manager, owner, client_obj, shop, server):
    place(owner, client_obj, shop, "one.com")
    pay(manager, place(owner, client_obj, shop, "two.com"))
    api.force_authenticate(manager)
    assert api.get("/api/v1/orders/?group=pending").json()["count"] == 1
    assert api.get("/api/v1/orders/?group=active").json()["count"] == 1
    assert api.get("/api/v1/orders/?group=cancelled").json()["count"] == 0
    assert api.get("/api/v1/orders/?group=bogus").status_code == 400


def test_lifecycle_actions_over_the_api(api, manager, owner, staff, client_obj, shop, server):
    order = pay(manager, place(owner, client_obj, shop, "viaapi.com"))
    url = f"/api/v1/orders/{order.pk}"
    api.force_authenticate(owner)
    for action in ("fraud", "clear-fraud", "retry-fulfilment", "suspend", "unsuspend", "terminate"):
        assert api.post(f"{url}/{action}/", {}).status_code == 403
    assert api.get(f"{url}/timeline/").status_code == 403

    api.force_authenticate(manager)
    assert api.post(f"{url}/suspend/", {"reason": "Abuse"}).json()["status"] == "suspended"
    assert api.post(f"{url}/suspend/", {}).json()["error"]["code"] == "invalid_status"
    assert api.post(f"{url}/unsuspend/").json()["status"] == "active"
    illegal = api.post(f"{url}/fraud/", {"reason": "x"})
    assert illegal.status_code == 409 and illegal.json()["error"]["code"] == "invalid_transition"
    assert api.post(f"{url}/terminate/", {"reason": "Requested"}).json()["status"] == "terminated"
    timeline = api.get(f"{url}/timeline/").json()
    moves = [(e["from_status"], e["to_status"]) for e in timeline if e["to_status"]]
    assert moves[0] == ("pending_payment", "paid") and moves[-1] == ("active", "terminated")
    assert {"provisioning", "active"} <= {t for _, t in moves}

    api.force_authenticate(staff(Role.SUPPORT_AGENT))
    assert api.get(f"{url}/timeline/").status_code == 200 and api.post(f"{url}/suspend/", {}).status_code == 403


def test_fraud_and_retry_over_the_api(api, manager, owner, client_obj, shop, product):
    order = place(owner, client_obj, shop, "apifraud.com")
    api.force_authenticate(manager)
    assert api.post(f"/api/v1/orders/{order.pk}/fraud/", {"reason": "Chargeback risk"}).json()["status"] == "fraud"
    assert api.post(f"/api/v1/orders/{order.pk}/clear-fraud/", {}).json()["status"] == "pending_payment"
    pay(manager, order)
    assert reload(order).status == OrderStatus.FAILED
    retried = api.post(f"/api/v1/orders/{order.pk}/retry-fulfilment/")
    assert retried.status_code == 200 and retried.json()["status"] == "failed"  # still no server: reported
    srv = product_services.create_server(manager, {"name": "s", "hostname": "s.example.com"})
    product_services.set_product_servers(manager, product, [srv.pk])
    from apps.hosting import services as hosting

    hosting.assign_server(manager, HostingAccount.objects.get(domain="apifraud.com"), srv)
    assert api.post(f"/api/v1/orders/{order.pk}/retry-fulfilment/").json()["status"] == "active"


def test_a_strangers_order_actions_look_missing(api, customer, manager, owner, client_obj, shop):
    order = place(owner, client_obj, shop, "stranger.com")
    api.force_authenticate(customer)
    assert api.post(f"/api/v1/orders/{order.pk}/fraud/", {}).status_code == 404
    assert api.get(f"/api/v1/orders/{order.pk}/timeline/").status_code == 404
