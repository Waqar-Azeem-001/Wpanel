"""
Races only a real database can show (PostgreSQL; SQLite ignores row locks). Each lock is held for a
moment so the threads genuinely overlap - the same technique, and the same mutation check, as
``apps.billing.tests.test_concurrency``.
"""
import threading
import time
from decimal import Decimal

import pytest
from django.db import connection, connections, transaction

from apps.billing import integrity
from apps.billing.models import Invoice
from apps.hosting.models import HostingAccount
from apps.renewals import services
from apps.renewals.models import ChangeStatus, ServiceChange

from .conftest import pay

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="needs real row locking (PostgreSQL)"),
]

D = Decimal
HOLD = 0.4


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


@pytest.fixture
def slow(monkeypatch):
    """Hold the service row lock a moment after it is taken, so an unlocked reader would act on stale data."""
    original_pending, original_apply = services._pending_change, services._apply_hosting

    def slow_pending(**kwargs):
        found = original_pending(**kwargs)
        time.sleep(HOLD)
        return found

    def slow_apply(change):
        result = original_apply(change)
        time.sleep(HOLD)
        return result

    monkeypatch.setattr(services, "_pending_change", slow_pending)
    monkeypatch.setattr(services, "_apply_hosting", slow_apply)


def test_a_double_clicked_renewal_makes_one_invoice(owner, account, slow):
    results, errors = run_in_threads(*[lambda: services.create_hosting_renewal(owner, account)] * 2)

    assert errors == []
    assert results[0].pk == results[1].pk
    assert ServiceChange.objects.count() == 1 and Invoice.objects.count() == 1


def test_a_renewal_and_an_upgrade_at_once_leave_one_open_change(owner, account, pro, slow):
    results, errors = run_in_threads(lambda: services.create_hosting_renewal(owner, account),
                                     lambda: services.create_upgrade(owner, account, pro))

    assert len(results) == 2 and len([r for r in results if r is not None]) == 1
    assert len(errors) == 1 and getattr(errors[0], "code", "") == "change_pending"
    assert ServiceChange.objects.filter(status=ChangeStatus.PENDING).count() == 1


def test_the_paid_signal_delivered_twice_at_once_extends_once(manager, owner, account, slow):
    change = services.create_hosting_renewal(owner, account)
    pay(manager, change.invoice)
    expiry = HostingAccount.objects.get(pk=account.pk).expires_at
    ServiceChange.objects.filter(pk=change.pk).update(status=ChangeStatus.PENDING)  # as if not yet applied

    def apply_in_the_payment_transaction():
        with transaction.atomic():  # as the paid signal does
            services.apply_paid_invoice(change.invoice)

    results, errors = run_in_threads(apply_in_the_payment_transaction, apply_in_the_payment_transaction)

    assert errors == []
    assert HostingAccount.objects.get(pk=account.pk).expires_at == services.add_months(expiry, 12)  # once, not twice
    assert ServiceChange.objects.get(pk=change.pk).status == ChangeStatus.APPLIED
    assert integrity.verify_all() == []
