"""Cart operations, checkout and order cancellation (service layer)."""
from decimal import Decimal

import pytest
from django.core import mail

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.billing import services as billing
from apps.clients import services as client_services
from apps.clients.models import ContactRole
from apps.core.exceptions import ServiceError
from apps.notifications.models import Notification
from apps.orders import pricing, services
from apps.orders.models import Cart, CartStatus, CouponRedemption, ItemKind, Order, OrderItem, OrderStatus

pytestmark = pytest.mark.django_db

D = Decimal


def _fill(owner, client_obj, shop):
    host = services.add_hosting(owner, client_obj, shop["product"], "example.com", "annual")
    services.add_addon(owner, host, shop["addon"])
    services.add_domain_registration(owner, client_obj, "example.com", 2)
    return services.get_open_cart(owner, client_obj)


# --- Who may build a cart ----------------------------------------------------------------------

def test_a_non_contact_cannot_open_a_cart_for_a_client(customer, client_obj):
    with pytest.raises(ServiceError) as exc:
        services.get_open_cart(customer, client_obj)
    assert exc.value.code == "permission_denied"


def test_any_contact_role_can_shop_and_staff_with_manage_orders_can_too(manager, client_obj, shop, make_user):
    tech = make_user("tech@example.com")
    client_services.add_contact(manager, client_obj, email="tech@example.com", role=ContactRole.TECHNICAL)
    assert services.get_open_cart(tech, client_obj).user == tech
    assert services.get_open_cart(manager, client_obj).user == manager  # staff can build one on a client's behalf


def test_support_agent_cannot_shop_for_a_client(staff, client_obj):
    with pytest.raises(ServiceError):
        services.get_open_cart(staff(Role.SUPPORT_AGENT), client_obj)  # view_orders only, no manage_orders


def test_there_is_one_open_cart_per_user_and_client(owner, client_obj):
    assert services.get_open_cart(owner, client_obj).pk == services.get_open_cart(owner, client_obj).pk
    assert Cart.objects.count() == 1


def test_resolve_client(owner, manager, client_obj, customer):
    assert services.resolve_client(owner) == client_obj  # the single client they belong to
    assert services.resolve_client(owner, client_obj.pk) == client_obj

    second = client_services.create_client(manager, {"first_name": "B", "email": "b@x.test"})
    client_services.add_contact(manager, second, email=owner.email, role=ContactRole.OWNER)
    with pytest.raises(ServiceError) as exc:
        services.resolve_client(owner)  # ambiguous: never guess which account an order is for
    assert exc.value.code == "client_required"
    assert services.resolve_client(owner, second.pk) == second

    with pytest.raises(ServiceError) as exc:  # someone else's client looks exactly like a missing one
        services.resolve_client(customer, client_obj.pk)
    assert exc.value.code == "not_found" and exc.value.status_code == 404
    with pytest.raises(ServiceError) as exc:
        services.resolve_client(owner, 999999)
    assert exc.value.code == "not_found"


def test_items_of_someone_elses_cart_cannot_be_touched(owner, client_obj, shop, customer):
    item = services.add_hosting(owner, client_obj, shop["product"], "example.com", "annual")
    with pytest.raises(ServiceError) as exc:
        services.remove_item(customer, item)
    assert exc.value.code == "permission_denied"
    with pytest.raises(ServiceError):
        services.add_addon(customer, item, shop["addon"])
    with pytest.raises(ServiceError):
        services.apply_coupon(customer, item.cart, "X")


# --- Checkout: the order ------------------------------------------------------------------------

def test_checkout_creates_a_pending_order_with_snapshotted_figures(owner, client_obj, shop, manager, coupon):
    billing.save_tax_rule(manager, "US", name="Sales tax", rate=D("8"))
    cart = _fill(owner, client_obj, shop)
    services.apply_coupon(owner, cart, "SAVE10")
    expected = pricing.price_cart(cart)

    order = services.checkout(owner, cart, payment_method_code=shop["bank"].code, notes="  Please be quick  ")
    assert order.status == OrderStatus.PENDING_PAYMENT and order.reference == f"O{order.pk:06d}"
    assert (order.subtotal, order.discount_total, order.tax_total, order.total) == (
        expected.subtotal, expected.discount_total, expected.tax_total, expected.total)
    assert (order.subtotal, order.discount_total, order.tax_total, order.total) == (
        D("154.00"), D("15.40"), D("11.09"), D("149.69"))
    assert order.currency == "USD" and order.tax_name == "Sales tax" and order.tax_rate == D("8.00")
    assert order.coupon_code == "SAVE10" and order.payment_method_name == "Bank transfer"
    assert order.placed_by == owner and order.notes == "Please be quick"

    items = list(order.items.order_by("id"))
    assert [i.kind for i in items] == [ItemKind.HOSTING, ItemKind.ADDON, ItemKind.DOMAIN_REGISTER]
    assert [i.line_total for i in items] == [D("110.00"), D("20.00"), D("24.00")]
    assert items[1].parent == items[0]  # the add-on stays attached to its hosting line
    assert sum(i.line_total for i in items) == order.subtotal


def test_checkout_closes_the_cart_and_records_the_coupon_redemption(owner, client_obj, shop, coupon):
    cart = _fill(owner, client_obj, shop)
    services.apply_coupon(owner, cart, "SAVE10")
    order = services.checkout(owner, cart, payment_method_code=shop["bank"].code)
    assert Cart.objects.get(pk=cart.pk).status == CartStatus.CONVERTED
    redemption = CouponRedemption.objects.get()
    assert (redemption.order, redemption.client, redemption.discount_amount) == (order, client_obj, D("15.40"))
    # ...and the next time the customer shops they get a fresh, empty cart.
    assert services.get_open_cart(owner, client_obj).pk != cart.pk
    assert not services.get_open_cart(owner, client_obj).items.exists()


def test_billing_details_are_snapshotted_and_later_edits_do_not_rewrite_the_order(owner, client_obj, shop, manager):
    order = services.checkout(owner, _fill(owner, client_obj, shop), payment_method_code=shop["bank"].code)
    assert (order.billing_name, order.billing_company, order.billing_email, order.billing_country) == (
        "Ada Lovelace", "Acme Ltd", "ada@acme.test", "US")
    client_services.update_client(manager, client_obj, {"company_name": "Renamed Inc", "country": "GB"})
    order.refresh_from_db()
    assert order.billing_company == "Acme Ltd" and order.billing_country == "US"


def test_checkout_is_audited_notified_and_emailed(owner, client_obj, shop):
    mail.outbox.clear()
    order = services.checkout(owner, _fill(owner, client_obj, shop), payment_method_code=shop["bank"].code)
    event = AuditEvent.objects.get(action="order.placed")
    assert event.actor == owner and event.metadata["total"] == str(order.total)
    assert Notification.objects.filter(user=owner, event="order.placed").exists()
    assert len(mail.outbox) == 1
    body = mail.outbox[0].body
    assert order.reference in mail.outbox[0].subject and "Pay to IBAN PK00 TEST 0001." in body
    assert "Starter (Annually) - example.com" in body and f"Total due: USD {order.total}" in body


def test_transfer_auth_code_travels_to_the_order_encrypted(owner, client_obj, shop):
    services.add_domain_transfer(owner, client_obj, "moving.com", "epp-secret-1")
    order = services.checkout(owner, services.get_open_cart(owner, client_obj),
                              payment_method_code=shop["bank"].code)
    item = order.items.get()
    assert item.kind == ItemKind.DOMAIN_TRANSFER and item.get_auth_code() == "epp-secret-1"
    assert "epp-secret-1" not in item.auth_code_encrypted


# --- Checkout: things that must be refused ---------------------------------------------------------

def test_empty_cart_cannot_be_checked_out(owner, client_obj, shop):
    with pytest.raises(ServiceError) as exc:
        services.checkout(owner, services.get_open_cart(owner, client_obj), payment_method_code=shop["bank"].code)
    assert exc.value.code == "cart_empty"
    assert not Order.objects.exists()


@pytest.mark.parametrize("code", ["nope", ""])
def test_payment_method_must_exist(owner, client_obj, shop, code):
    with pytest.raises(ServiceError) as exc:
        services.checkout(owner, _fill(owner, client_obj, shop), payment_method_code=code)
    assert exc.value.code == "payment_method_invalid"


def test_inactive_payment_method_is_refused(owner, client_obj, shop, manager):
    billing.set_payment_method_active(manager, shop["bank"], False)
    with pytest.raises(ServiceError) as exc:
        services.checkout(owner, _fill(owner, client_obj, shop), payment_method_code=shop["bank"].code)
    assert exc.value.code == "payment_method_invalid"


def test_repeating_a_checkout_creates_no_second_order_and_says_why(owner, client_obj, shop):
    cart = _fill(owner, client_obj, shop)
    services.checkout(owner, cart, payment_method_code=shop["bank"].code)
    with pytest.raises(ServiceError) as exc:  # `cart` is a stale in-memory object still marked open
        services.checkout(owner, cart, payment_method_code=shop["bank"].code)
    assert exc.value.code == "cart_closed" and Order.objects.count() == 1


def test_a_closed_cart_accepts_no_more_changes(owner, client_obj, shop):
    cart = _fill(owner, client_obj, shop)
    services.checkout(owner, cart, payment_method_code=shop["bank"].code)
    cart.refresh_from_db()
    with pytest.raises(ServiceError) as exc:
        services.apply_coupon(owner, cart, "X")
    assert exc.value.code == "cart_closed"


def test_a_plan_retired_after_adding_blocks_checkout(owner, client_obj, shop, manager):
    from apps.products import services as product_services

    cart = _fill(owner, client_obj, shop)
    product_services.set_product_status(manager, shop["product"], "retired")
    with pytest.raises(ServiceError) as exc:
        services.checkout(owner, cart, payment_method_code=shop["bank"].code)
    assert exc.value.code == "cart_invalid" and "no longer available" in exc.value.message
    assert not Order.objects.exists()


def test_a_domain_taken_after_adding_blocks_checkout(owner, client_obj, shop, manager):
    from apps.domains import services as domain_services

    cart = _fill(owner, client_obj, shop)
    domain_services.request_registration(manager, client_obj, "example.com", 1)
    with pytest.raises(ServiceError, match="no longer available"):
        services.checkout(owner, cart, payment_method_code=shop["bank"].code)
    assert not Order.objects.exists() and Cart.objects.get(pk=cart.pk).status == CartStatus.OPEN


def test_a_price_change_after_adding_is_picked_up_at_checkout(owner, client_obj, shop, manager):
    """The cart stores selections only, so an order can never carry a stale price."""
    from apps.products import services as product_services

    cart = _fill(owner, client_obj, shop)
    product_services.set_price(manager, shop["product"], billing_cycle="annual", price="120.00", setup_fee="10.00")
    order = services.checkout(owner, cart, payment_method_code=shop["bank"].code)
    assert order.items.filter(kind=ItemKind.HOSTING).get().unit_price == D("120.00")
    assert order.subtotal == D("174.00")


def test_an_expired_coupon_blocks_checkout_rather_than_being_dropped(owner, client_obj, shop, manager, coupon):
    cart = _fill(owner, client_obj, shop)
    services.apply_coupon(owner, cart, "SAVE10")
    billing.set_coupon_active(manager, coupon, False)
    with pytest.raises(ServiceError):
        services.checkout(owner, cart, payment_method_code=shop["bank"].code)
    assert not Order.objects.exists()


def test_coupon_usage_limit_is_enforced_at_checkout_not_just_when_applied(owner, client_obj, shop, manager):
    """Two carts hold the same single-use code; whichever checks out second is refused."""
    billing.save_coupon(manager, "SINGLE", {"discount_type": "percent", "value": D("10"), "max_redemptions": 1})
    other = client_services.create_client(manager, {"first_name": "B", "email": "b@x.test"})
    other_owner = other.contacts.get().user

    cart_a = services.get_open_cart(owner, client_obj)
    services.add_hosting(owner, client_obj, shop["product"], "a.com", "annual")
    services.apply_coupon(owner, cart_a, "SINGLE")
    cart_b = services.get_open_cart(other_owner, other)
    services.add_hosting(other_owner, other, shop["product"], "b.com", "annual")
    services.apply_coupon(other_owner, cart_b, "SINGLE")  # still unused, so applying succeeds

    services.checkout(owner, cart_a, payment_method_code=shop["bank"].code)
    with pytest.raises(ServiceError, match="usage limit"):
        services.checkout(other_owner, cart_b, payment_method_code=shop["bank"].code)
    assert Order.objects.count() == 1


def test_a_failure_part_way_leaves_no_order_and_keeps_the_cart_open(owner, client_obj, shop, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("notification service down")

    monkeypatch.setattr("apps.orders.services.notifications.dispatch", boom)
    cart = _fill(owner, client_obj, shop)
    with pytest.raises(RuntimeError):
        services.checkout(owner, cart, payment_method_code=shop["bank"].code)
    assert not Order.objects.exists() and not OrderItem.objects.exists() and not CouponRedemption.objects.exists()
    assert Cart.objects.get(pk=cart.pk).status == CartStatus.OPEN and cart.items.count() == 3


# --- Domain names are held by unpaid orders --------------------------------------------------------

def test_an_unpaid_order_holds_its_domain_until_cancelled(owner, client_obj, shop, manager):
    order = services.checkout(owner, _fill(owner, client_obj, shop), payment_method_code=shop["bank"].code)
    other = client_services.create_client(manager, {"first_name": "B", "email": "b@x.test"})
    other_owner = other.contacts.get().user
    with pytest.raises(ServiceError) as exc:
        services.add_domain_registration(other_owner, other, "example.com", 1)
    assert exc.value.code == "domain_reserved"

    services.cancel_order(owner, order)
    services.add_domain_registration(other_owner, other, "example.com", 1)  # released


# --- Cancelling ------------------------------------------------------------------------------------

def test_customer_can_cancel_an_unpaid_order_and_it_is_audited(owner, client_obj, shop):
    order = services.checkout(owner, _fill(owner, client_obj, shop), payment_method_code=shop["bank"].code)
    cancelled = services.cancel_order(owner, order, reason="  ordered by mistake ")
    assert cancelled.status == OrderStatus.CANCELLED and cancelled.cancel_reason == "ordered by mistake"
    assert AuditEvent.objects.filter(action="order.cancelled", target_id=str(order.pk)).exists()


def test_only_pending_orders_can_be_cancelled(owner, client_obj, shop):
    order = services.checkout(owner, _fill(owner, client_obj, shop), payment_method_code=shop["bank"].code)
    services.cancel_order(owner, order)
    with pytest.raises(ServiceError) as exc:
        services.cancel_order(owner, order)
    assert exc.value.code == "invalid_status"
    Order.objects.filter(pk=order.pk).update(status=OrderStatus.PAID)
    with pytest.raises(ServiceError):
        services.cancel_order(owner, order)


def test_cancelling_needs_a_relationship_to_the_order(owner, client_obj, shop, customer, manager):
    order = services.checkout(owner, _fill(owner, client_obj, shop), payment_method_code=shop["bank"].code)
    with pytest.raises(ServiceError) as exc:
        services.cancel_order(customer, order)
    assert exc.value.code == "permission_denied"
    assert services.cancel_order(manager, order).status == OrderStatus.CANCELLED  # manage_orders


# --- Visibility & search ----------------------------------------------------------------------------

def test_orders_are_scoped_to_their_client_contacts(owner, client_obj, shop, manager, customer):
    order = services.checkout(owner, _fill(owner, client_obj, shop), payment_method_code=shop["bank"].code)
    assert order in services.visible_orders_for_user(owner)
    assert order in services.visible_orders_for_user(manager)
    assert order not in services.visible_orders_for_user(customer)
    assert services.visible_orders_for_user(None).count() == 0


def test_search_by_reference_client_domain_and_coupon(owner, client_obj, shop, manager, coupon):
    cart = _fill(owner, client_obj, shop)
    services.apply_coupon(owner, cart, "SAVE10")
    order = services.checkout(owner, cart, payment_method_code=shop["bank"].code)
    everything = Order.objects.all()
    for term in (order.reference, order.reference.lower(), str(order.pk), "acme", "example.com", "save10"):
        assert list(services.search_orders(everything, term)) == [order], term
    assert services.search_orders(everything, "nothing-matches").count() == 0
    assert services.search_orders(everything, "").count() == 1
