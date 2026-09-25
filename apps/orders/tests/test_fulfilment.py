"""Order lifecycle and fulfilment: paid -> processing -> provisioning -> active, failures, retries, staff actions."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.billing import integrity, payments
from apps.billing import services as billing
from apps.core.exceptions import ServiceError
from apps.core.system import SYSTEM
from apps.domains.models import Domain
from apps.hosting.models import HostingAccount, HostingStatus
from apps.notifications.models import Notification
from apps.orders import fulfilment, lifecycle, services, staff_actions
from apps.orders.models import FulfilmentStatus, Order, OrderItem, OrderStatus
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


_names = iter(f"site{n}.com" for n in range(1000))


def place(owner, client_obj, shop, *, coupon=None, transfer=False, name="example.com"):
    """An order for a plan (100 + 10 setup), a backups add-on (20) and a 2-year domain (24) on ``name``."""
    if name is None:
        name = next(_names)
    host = services.add_hosting(owner, client_obj, shop["product"], name, "annual")
    services.add_addon(owner, host, shop["addon"])
    services.add_domain_registration(owner, client_obj, name, 2)
    if transfer:
        services.add_domain_transfer(owner, client_obj, "moving.com", "epp-secret")            # 9
    cart = services.get_open_cart(owner, client_obj)
    if coupon:
        services.apply_coupon(owner, cart, coupon)
    return services.checkout(owner, cart, payment_method_code="bank-transfer")


def pay(manager, order):
    invoice = order.invoices.get()
    payments.record_payment(manager, invoice, amount=str(invoice.total))
    order.refresh_from_db()
    return order


def reload(obj):
    obj.refresh_from_db()
    return obj


# --- The happy path ---------------------------------------------------------------------------------

def test_a_paid_order_becomes_active_with_its_services(manager, owner, client_obj, shop, server):
    mail.outbox.clear()
    order = pay(manager, place(owner, client_obj, shop))
    assert order.status == OrderStatus.ACTIVE
    host, addon, domain_item = list(order.items.order_by("id"))
    assert all(i.fulfilment_status == FulfilmentStatus.DONE for i in (host, addon, domain_item))

    account = host.hosting_account
    assert account.status == HostingStatus.ACTIVE and account.domain == "example.com"
    assert account.server == server and account.package_name == "starter_pkg"
    domain = domain_item.domain
    assert domain.status == "active" and domain.expires_at > timezone.now() + timedelta(days=700)
    assert addon.hosting_account is None and addon.domain is None

    subjects = [m.subject for m in mail.outbox]
    assert any("Hosting" in s or "hosting" in s for s in subjects) and any("is active" in s for s in subjects)
    assert Notification.objects.filter(user=owner, event="order.active").exists()
    assert integrity.verify_all() == []


def test_the_hosting_term_starts_and_records_what_was_paid_for_the_plan(manager, owner, client_obj, shop, server, coupon):
    billing.save_tax_rule(manager, "US", name="Sales tax", rate=D("7.5"))
    order = pay(manager, place(owner, client_obj, shop, coupon="SAVE10"))
    account = order.items.get(kind="hosting").hosting_account
    assert account.billing_cycle == "annual" and account.term_start
    assert account.expires_at > timezone.now() + timedelta(days=360)
    # 100.00 plan line minus its 10% share of the coupon; no setup fee, no tax.
    assert account.term_paid == D("90.00")


def test_every_transition_is_audited_with_from_and_to_and_the_system_as_actor(manager, owner, client_obj, shop, server):
    order = pay(manager, place(owner, client_obj, shop))
    events = list(lifecycle.timeline(order).values_list("action", "metadata", "actor_id"))
    moves = [(a, m.get("from"), m.get("to")) for a, m, _ in events if "from" in m]
    assert moves == [("order.paid", "pending_payment", "paid"), ("order.processing", "paid", "processing"),
                     ("order.provisioning", "processing", "provisioning"),
                     ("order.activated", "provisioning", "active")]
    assert [actor for a, _, actor in events if a in ("order.processing", "order.activated")] == [None, None]


def test_transfers_are_fulfilled_and_the_auth_code_is_cleared(manager, owner, client_obj, shop, server):
    order = pay(manager, place(owner, client_obj, shop, transfer=True))
    item = order.items.get(kind="domain_transfer")
    assert order.status == OrderStatus.ACTIVE and item.domain.status == "active"
    assert item.auth_code_encrypted == "" and item.domain.auth_code_encrypted == ""


def test_running_fulfilment_again_does_nothing(manager, owner, client_obj, shop, server):
    order = pay(manager, place(owner, client_obj, shop))
    before = (HostingAccount.objects.count(), Domain.objects.count(), lifecycle.timeline(order).count())
    fulfilment.fulfil_order(order.pk)
    fulfilment.fulfil_order(order.pk)
    assert (HostingAccount.objects.count(), Domain.objects.count(), lifecycle.timeline(order).count()) == before


def test_no_fulfilment_when_disabled_or_unpaid(settings, manager, owner, client_obj, shop, server, monkeypatch):
    queued = []
    monkeypatch.setattr("apps.orders.tasks.fulfil_order_task.delay", lambda pk: queued.append(pk))
    order = place(owner, client_obj, shop)
    assert queued == [] and order.status == OrderStatus.PENDING_PAYMENT
    settings.ORDER_AUTO_FULFIL = False
    order = pay(manager, order)
    assert queued == [] and order.status == OrderStatus.PAID  # paid, waiting for the switch (or the sweeper)


def test_a_broken_queue_never_undoes_the_payment(manager, owner, client_obj, shop, server, monkeypatch):
    def down(pk):
        raise ConnectionError("broker down")

    monkeypatch.setattr("apps.orders.tasks.fulfil_order_task.delay", down)
    order = pay(manager, place(owner, client_obj, shop))
    assert order.status == OrderStatus.PAID and order.invoices.get().status == "paid"
    assert fulfilment.sweep(now=timezone.now() + timedelta(minutes=10)) == {"queued": 1, "reset": 0}
    assert reload(order).status == OrderStatus.ACTIVE  # the sweeper caught it


# --- Failure and retry ---------------------------------------------------------------------------------

def test_a_failed_line_fails_the_order_but_keeps_what_worked_and_a_retry_finishes_the_rest(
        manager, owner, client_obj, shop, product):
    order = pay(manager, place(owner, client_obj, shop))  # no server is mapped to the plan
    assert order.status == OrderStatus.FAILED
    host, addon, domain_item = list(order.items.order_by("id"))
    assert host.fulfilment_status == FulfilmentStatus.FAILED and "server" in host.fulfilment_error.lower()
    assert domain_item.fulfilment_status == FulfilmentStatus.DONE and domain_item.domain.status == "active"
    assert addon.fulfilment_status == FulfilmentStatus.PENDING  # waits for its plan
    assert "server" in order.status_reason.lower()
    assert AuditEvent.objects.filter(action="order.item_failed").exists()

    srv = product_services.create_server(manager, {"name": "srv1", "hostname": "srv1.example.com"})
    product_services.set_product_servers(manager, product, [srv.pk])
    from apps.hosting import services as hosting

    hosting.assign_server(manager, host.hosting_account, srv)
    staff_actions.retry_fulfilment(manager, order)
    order.refresh_from_db()
    assert order.status == OrderStatus.ACTIVE and all(
        i.fulfilment_status == FulfilmentStatus.DONE for i in order.items.all())
    assert HostingAccount.objects.count() == 1 and Domain.objects.count() == 1  # nothing was created twice


def test_a_registrar_failure_then_a_retry(manager, owner, client_obj, shop, server, monkeypatch):
    from apps.domains.adapters import manual
    from apps.domains.adapters.base import RegistrarError

    original = manual.ManualAdapter.register_domain

    def broken(self, *args, **kwargs):
        raise RegistrarError("registry offline")

    monkeypatch.setattr(manual.ManualAdapter, "register_domain", broken)
    order = pay(manager, place(owner, client_obj, shop))
    assert order.status == OrderStatus.FAILED and "registry offline" in order.status_reason
    still = staff_actions.retry_fulfilment(manager, order)
    assert still.status == OrderStatus.FAILED  # still broken: reported, not hidden

    monkeypatch.setattr(manual.ManualAdapter, "register_domain", original)
    assert staff_actions.retry_fulfilment(manager, order).status == OrderStatus.ACTIVE
    assert HostingAccount.objects.count() == 1 and Domain.objects.count() == 1


def test_only_a_failed_order_can_be_retried(manager, owner, client_obj, shop, server):
    order = pay(manager, place(owner, client_obj, shop))
    with pytest.raises(ServiceError) as exc:
        staff_actions.retry_fulfilment(manager, order)
    assert exc.value.code == "invalid_status"


def test_an_unexpected_error_in_one_line_is_contained(manager, owner, client_obj, shop, server, monkeypatch):
    def boom(order, item, actor):
        raise RuntimeError("bug")

    monkeypatch.setitem(fulfilment.HANDLERS, "domain_register", boom)
    order = pay(manager, place(owner, client_obj, shop))
    assert order.status == OrderStatus.FAILED and "unexpected" in order.status_reason.lower()
    assert order.items.get(kind="hosting").fulfilment_status == FulfilmentStatus.DONE


# --- The state machine ------------------------------------------------------------------------------------

@pytest.mark.parametrize("current,new", [
    (OrderStatus.CANCELLED, OrderStatus.PAID), (OrderStatus.TERMINATED, OrderStatus.ACTIVE),
    (OrderStatus.ACTIVE, OrderStatus.PAID), (OrderStatus.PENDING_PAYMENT, OrderStatus.ACTIVE),
    (OrderStatus.PAID, OrderStatus.CANCELLED), (OrderStatus.PROCESSING, OrderStatus.CANCELLED),
    (OrderStatus.PROVISIONING, OrderStatus.FRAUD), (OrderStatus.SUSPENDED, OrderStatus.PAID),
])
def test_illegal_moves_are_refused_and_change_nothing(owner, client_obj, shop, current, new):
    order = place(owner, client_obj, shop)
    Order.objects.filter(pk=order.pk).update(status=current)
    with pytest.raises(ServiceError) as exc:
        lifecycle.transition(reload(order), new)
    assert exc.value.code == "invalid_transition" and exc.value.status_code == 409
    assert reload(order).status == current


def test_the_table_has_no_way_out_of_the_final_states():
    assert lifecycle.ALLOWED[OrderStatus.CANCELLED] == set() == lifecycle.ALLOWED[OrderStatus.TERMINATED]
    assert set(lifecycle.ALLOWED) == set(OrderStatus.values)
    reachable = {new for moves in lifecycle.ALLOWED.values() for new in moves} | {OrderStatus.DRAFT}
    assert reachable == set(OrderStatus.values)  # every status can be reached by some legal path


def test_a_move_to_the_current_status_is_a_quiet_no_op(owner, client_obj, shop):
    order = place(owner, client_obj, shop)
    count = lifecycle.timeline(order).count()
    lifecycle.transition(order, OrderStatus.PENDING_PAYMENT)
    assert lifecycle.timeline(order).count() == count


# --- Fraud ----------------------------------------------------------------------------------------------------

def test_a_fraud_hold_stops_fulfilment_even_if_it_is_paid_and_clearing_resumes_it(manager, owner, client_obj, shop, server):
    order = place(owner, client_obj, shop)
    staff_actions.mark_fraud(manager, order, reason="Stolen card pattern")
    order = pay(manager, reload(order))
    assert order.status == OrderStatus.FRAUD and order.status_reason == "Stolen card pattern"
    assert not HostingAccount.objects.exists()
    staff_actions.clear_fraud(manager, order, reason="Verified by phone")
    assert reload(order).status == OrderStatus.ACTIVE and HostingAccount.objects.count() == 1


def test_clearing_fraud_on_an_unpaid_order_returns_it_to_awaiting_payment(manager, owner, client_obj, shop):
    order = place(owner, client_obj, shop)
    staff_actions.mark_fraud(manager, order)
    assert staff_actions.clear_fraud(manager, order).status == OrderStatus.PENDING_PAYMENT
    with pytest.raises(ServiceError):
        staff_actions.clear_fraud(manager, order)  # nothing to clear now


def test_fraud_needs_manage_orders_and_a_legal_state(staff, manager, owner, client_obj, shop, server):
    order = place(owner, client_obj, shop)
    for actor in (owner, staff(Role.SUPPORT_AGENT)):
        with pytest.raises(ServiceError) as exc:
            staff_actions.mark_fraud(actor, order)
        assert exc.value.code == "permission_denied"
    active = pay(manager, place(owner, client_obj, shop, name=None))
    with pytest.raises(ServiceError) as exc:
        staff_actions.mark_fraud(manager, active)
    assert exc.value.code == "invalid_transition"


def test_staff_can_close_a_failed_or_fraud_order_but_customers_cannot(manager, owner, client_obj, shop, product):
    failed = pay(manager, place(owner, client_obj, shop))  # no server: fails
    assert failed.status == OrderStatus.FAILED
    with pytest.raises(ServiceError):
        services.cancel_order(owner, failed)
    services.cancel_order(manager, failed, reason="Refunded")
    assert reload(failed).status == OrderStatus.CANCELLED and reload(failed).cancel_reason == "Refunded"


# --- Suspend, unsuspend, terminate ------------------------------------------------------------------------------

def test_suspending_and_reactivating_an_order_follows_its_hosting(manager, owner, client_obj, shop, server):
    order = pay(manager, place(owner, client_obj, shop))
    account = order.items.get(kind="hosting").hosting_account
    staff_actions.suspend_order(manager, order, reason="Non-payment")
    assert reload(order).status == OrderStatus.SUSPENDED and reload(account).status == HostingStatus.SUSPENDED
    assert reload(order).status_reason == "Non-payment" and reload(account).suspend_reason == "Non-payment"
    staff_actions.unsuspend_order(manager, order)
    assert reload(order).status == OrderStatus.ACTIVE and reload(account).status == HostingStatus.ACTIVE


def test_terminating_an_order_is_final_and_terminates_hosting(manager, owner, client_obj, shop, server):
    order = pay(manager, place(owner, client_obj, shop))
    account = order.items.get(kind="hosting").hosting_account
    staff_actions.terminate_order(manager, order, reason="Requested by client")
    assert reload(order).status == OrderStatus.TERMINATED and reload(account).status == HostingStatus.TERMINATED
    with pytest.raises(ServiceError):
        staff_actions.unsuspend_order(manager, order)
    assert order.items.get(kind="domain_register").domain.status == "active"  # registrations are left alone


def test_a_cascade_that_fails_leaves_the_order_as_it_was_and_can_be_rerun(manager, owner, client_obj, shop, server, monkeypatch):
    from apps.hosting.adapters import manual
    from apps.hosting.adapters.base import HostingError

    order = pay(manager, place(owner, client_obj, shop))
    account = order.items.get(kind="hosting").hosting_account
    original = manual.ManualAdapter.suspend_account

    def broken(self, username, reason=""):
        raise HostingError("WHM unreachable")

    monkeypatch.setattr(manual.ManualAdapter, "suspend_account", broken)
    with pytest.raises(ServiceError) as exc:
        staff_actions.suspend_order(manager, order)
    assert exc.value.code == "cascade_failed" and "example.com" in exc.value.message
    assert reload(order).status == OrderStatus.ACTIVE and reload(account).status == HostingStatus.ACTIVE
    monkeypatch.setattr(manual.ManualAdapter, "suspend_account", original)
    staff_actions.suspend_order(manager, order)
    assert reload(order).status == OrderStatus.SUSPENDED


def test_suspend_rules(staff, manager, owner, client_obj, shop, server):
    order = pay(manager, place(owner, client_obj, shop))
    with pytest.raises(ServiceError):
        staff_actions.suspend_order(owner, order)
    staff_actions.suspend_order(manager, order)
    with pytest.raises(ServiceError):
        staff_actions.suspend_order(manager, order)  # already suspended
    pending = place(owner, client_obj, shop, name=None)
    with pytest.raises(ServiceError):
        staff_actions.suspend_order(manager, pending)


# --- The sweeper ----------------------------------------------------------------------------------------------------

def test_the_sweeper_resets_orders_stuck_mid_fulfilment(manager, owner, client_obj, shop):
    order = place(owner, client_obj, shop)
    Order.objects.filter(pk=order.pk).update(status=OrderStatus.PROVISIONING,
                                            updated_at=timezone.now() - timedelta(hours=2))
    assert fulfilment.sweep() == {"queued": 0, "reset": 1}
    order.refresh_from_db()
    assert order.status == OrderStatus.FAILED and "interrupted" in order.status_reason.lower()
    assert fulfilment.sweep() == {"queued": 0, "reset": 0}


def test_the_sweeper_leaves_recent_and_healthy_orders_alone(manager, owner, client_obj, shop, server):
    order = place(owner, client_obj, shop)
    Order.objects.filter(pk=order.pk).update(status=OrderStatus.PROVISIONING)
    assert fulfilment.sweep() == {"queued": 0, "reset": 0}


# --- The system actor -----------------------------------------------------------------------------------------------

def test_the_system_actor_is_recorded_as_no_user():
    from apps.audit import services as audit

    event = audit.record("test.system", actor=SYSTEM)
    assert event.actor is None and event.actor_repr == "" and "system" in str(event)
    assert SYSTEM.has_perm("anything") and SYSTEM.is_authenticated and str(SYSTEM) == "system"


def test_fulfilment_creates_no_orphans_when_the_order_is_fraudulent_before_payment(manager, owner, client_obj, shop, server):
    order = place(owner, client_obj, shop)
    staff_actions.mark_fraud(manager, order)
    pay(manager, reload(order))
    assert OrderItem.objects.filter(hosting_account__isnull=False).count() == 0


# --- verify_billing covers the lifecycle -----------------------------------------------------------------------

def test_verify_billing_catches_an_order_active_with_an_unfulfilled_line_or_unpaid_invoice(
        manager, owner, client_obj, shop, server):
    order = pay(manager, place(owner, client_obj, shop, name=None))
    assert integrity.verify_all() == []
    OrderItem.objects.filter(order=order, kind="domain_register").update(fulfilment_status="pending")
    assert any("never fulfilled" in p for p in integrity.verify_all())
    OrderItem.objects.filter(order=order, kind="domain_register").update(fulfilment_status="done")
    order.invoices.update(status="unpaid")
    assert any("has not been paid" in p for p in integrity.verify_all())
    order.invoices.update(status="paid")
    Order.objects.filter(pk=order.pk).update(status=OrderStatus.PENDING_PAYMENT)
    assert any("still awaiting payment" in p for p in integrity.verify_all())
