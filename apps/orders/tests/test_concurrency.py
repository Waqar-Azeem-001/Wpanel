"""
Races only a real database can show (PostgreSQL; SQLite ignores row locks): two workers picking up the same paid
order. The claim (Paid -> Processing) is taken under the order's row lock; the lock is held for a moment here so the
threads genuinely overlap, and the test fails if the lock is removed.
"""
import threading
import time

import pytest
from django.db import connection, connections

from apps.billing import integrity, payments
from apps.domains.models import Domain
from apps.hosting.models import HostingAccount
from apps.orders import fulfilment, lifecycle, services
from apps.orders.models import OrderStatus
from apps.products import services as product_services

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="needs real row locking (PostgreSQL)"),
]


def run_in_threads(*jobs):
    results, errors = [None] * len(jobs), []
    barrier = threading.Barrier(len(jobs))

    def worker(index, job):
        try:
            barrier.wait(timeout=10)
            results[index] = job()
        except Exception as exc:  # noqa: BLE001 - reported to the test
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(i, job)) for i, job in enumerate(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results, errors


@pytest.fixture
def paid_order(manager, owner, client_obj, shop, product):
    server = product_services.create_server(manager, {"name": "srv1", "hostname": "srv1.example.com"})
    product_services.set_product_servers(manager, product, [server.pk])
    host = services.add_hosting(owner, client_obj, shop["product"], "race.com", "annual")
    services.add_addon(owner, host, shop["addon"])
    services.add_domain_registration(owner, client_obj, "race.com", 1)
    order = services.checkout(owner, services.get_open_cart(owner, client_obj), payment_method_code="bank-transfer")
    invoice = order.invoices.get()
    payments.record_payment(manager, invoice, amount=str(invoice.total))  # auto-fulfilment is off in tests
    order.refresh_from_db()
    assert order.status == OrderStatus.PAID
    return order


@pytest.fixture
def slow_claim(monkeypatch):
    """Hold the order lock a moment after the claim's first move, so an unlocked second worker would also claim."""
    original = lifecycle.transition

    def slow(order, new, **kwargs):
        result = original(order, new, **kwargs)
        if new == OrderStatus.PROCESSING:
            time.sleep(0.5)
        return result

    monkeypatch.setattr(lifecycle, "transition", slow)


def test_two_workers_never_fulfil_the_same_order_twice(paid_order, slow_claim):
    results, errors = run_in_threads(lambda: fulfilment.fulfil_order(paid_order.pk),
                                     lambda: fulfilment.fulfil_order(paid_order.pk))

    assert errors == []
    assert HostingAccount.objects.count() == 1 and Domain.objects.count() == 1
    paid_order.refresh_from_db()
    assert paid_order.status == OrderStatus.ACTIVE
    assert integrity.verify_all() == []


def test_a_retry_racing_the_first_run_does_nothing_extra(paid_order, slow_claim):
    from apps.core.system import SYSTEM

    results, errors = run_in_threads(lambda: fulfilment.fulfil_order(paid_order.pk, actor=SYSTEM),
                                     lambda: fulfilment.fulfil_order(paid_order.pk, actor=SYSTEM),
                                     lambda: fulfilment.fulfil_order(paid_order.pk, actor=SYSTEM))

    assert errors == []
    assert HostingAccount.objects.count() == 1
    moves = [e.action for e in lifecycle.timeline(paid_order) if e.action.startswith("order.") and
             e.action in ("order.processing", "order.provisioning", "order.activated")]
    assert moves == ["order.processing", "order.provisioning", "order.activated"]  # one run, in order
