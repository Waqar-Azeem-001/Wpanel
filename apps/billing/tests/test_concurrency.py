"""
Races that only a real database can show: SQLite ignores row locks, so these run on PostgreSQL only
(CI's database). They prove the invoice row lock and the unique constraints keep the books straight
when the same event or payment arrives twice at once.
"""
import threading
import time
from decimal import Decimal

import pytest
from django.db import connection, connections

from apps.billing import integrity, invoicing, payments
from apps.billing.models import Invoice, Transaction, WebhookEvent

from .conftest import lines, signed_event

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="needs real row locking (PostgreSQL)"),
]

D = Decimal
HOLD = 0.4  # seconds each lock is held, so the threads genuinely overlap instead of running back to back


@pytest.fixture(autouse=True)
def widen_the_race_window(monkeypatch):
    """Hold every lock a moment after taking it: without the lock, both threads would then act on stale data."""
    original_invoice_lock, original_number = payments._lock_invoice, invoicing._next_number

    def slow_invoice_lock(pk):
        invoice = original_invoice_lock(pk)
        time.sleep(HOLD)
        return invoice

    def slow_number(key, prefix):
        number = original_number(key, prefix)
        time.sleep(HOLD)
        return number

    monkeypatch.setattr(payments, "_lock_invoice", slow_invoice_lock)
    monkeypatch.setattr(invoicing, "_next_number", slow_number)


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
        thread.join(timeout=30)
    return results, errors


def test_the_same_webhook_delivered_twice_at_once_pays_once(manager, owner, client_obj, card, provider):
    invoice = invoicing.issue_invoice(manager, invoicing.create_invoice(
        manager, client_obj, lines=lines(("Hosting", 1, "100.00"))))
    tx, _ = payments.start_gateway_payment(owner, invoice, card)
    body, headers = signed_event(provider, "evt_race", "payment.succeeded", tx.external_id, "100.00")

    results, errors = run_in_threads(*[lambda: payments.handle_webhook(provider, body, headers)] * 2)

    assert errors == []
    assert sorted(r.status for r in results) == ["duplicate", "processed"]
    invoice.refresh_from_db()
    assert invoice.amount_paid == D("100.00") and invoice.status == "paid"
    assert WebhookEvent.objects.count() == 1
    assert integrity.verify_all() == []


def test_two_different_events_for_one_payment_apply_it_once(manager, owner, client_obj, card, provider):
    invoice = invoicing.issue_invoice(manager, invoicing.create_invoice(
        manager, client_obj, lines=lines(("Hosting", 1, "100.00"))))
    tx, _ = payments.start_gateway_payment(owner, invoice, card)
    first = signed_event(provider, "evt_a", "payment.succeeded", tx.external_id, "100.00")
    second = signed_event(provider, "evt_b", "payment.succeeded", tx.external_id, "100.00")

    results, errors = run_in_threads(lambda: payments.handle_webhook(provider, *first),
                                     lambda: payments.handle_webhook(provider, *second))

    assert errors == []
    assert sorted(r.status for r in results) == ["ignored", "processed"]
    assert Invoice.objects.get(pk=invoice.pk).amount_paid == D("100.00")
    assert integrity.verify_all() == []


def test_the_same_idempotency_key_at_once_records_one_payment(manager, client_obj):
    invoice = invoicing.issue_invoice(manager, invoicing.create_invoice(
        manager, client_obj, lines=lines(("Hosting", 1, "100.00"))))

    results, errors = run_in_threads(
        *[lambda: payments.record_payment(manager, invoice, amount="60.00", idempotency_key="race-1")] * 2)

    assert errors == []
    assert results[0].pk == results[1].pk and Transaction.objects.count() == 1
    assert Invoice.objects.get(pk=invoice.pk).amount_paid == D("60.00")


def test_concurrent_payments_can_never_overpay(manager, client_obj):
    invoice = invoicing.issue_invoice(manager, invoicing.create_invoice(
        manager, client_obj, lines=lines(("Hosting", 1, "100.00"))))

    results, errors = run_in_threads(
        *[lambda: payments.record_payment(manager, invoice, amount="70.00")] * 2)

    assert len([r for r in results if r is not None]) == 1  # the second one saw the first and was refused
    assert len(errors) == 1 and getattr(errors[0], "code", "") == "amount_exceeds_balance"
    invoice = Invoice.objects.get(pk=invoice.pk)
    assert invoice.amount_paid == D("70.00") and invoice.status == "partially_paid"


def test_numbers_stay_gap_free_under_concurrent_issue(manager, client_obj):
    drafts = [invoicing.create_invoice(manager, client_obj, lines=lines(("x", 1, "1.00"))) for _ in range(4)]

    results, errors = run_in_threads(*[(lambda d=d: invoicing.issue_invoice(manager, d)) for d in drafts])

    assert errors == []
    assert sorted(r.number for r in results) == ["INV-000001", "INV-000002", "INV-000003", "INV-000004"]
    assert integrity.verify_all() == []
