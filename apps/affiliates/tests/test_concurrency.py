"""
Races only a real database can show (PostgreSQL; SQLite ignores row locks): two people recording a payout for the same
commissions, and the "invoice paid" event arriving twice at once.
"""
import threading
import time
from decimal import Decimal

import pytest
from django.db import connection, connections

from apps.affiliates import services
from apps.affiliates.models import Commission, CommissionStatus, Payout
from apps.core.exceptions import ServiceError

from .conftest import pay_invoice

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
        thread.join(timeout=30)
    return results, errors


def test_two_payouts_for_the_same_commissions_pay_once(manager, admin, referred, affiliate, tax, monkeypatch):
    services.save_settings(admin, minimum_payout=Decimal("10"))
    commission = Commission.objects.get(invoice=pay_invoice(manager, referred[1], "1000.00"))
    services.approve_commission(manager, commission)
    original = Payout.objects.create

    def slow_create(*args, **kwargs):
        time.sleep(0.4)  # hold the affiliate row lock so an unlocked second recorder would also get through
        return original(*args, **kwargs)

    monkeypatch.setattr(Payout.objects, "create", slow_create)
    results, errors = run_in_threads(lambda: services.record_payout(manager, affiliate, method="Bank"),
                                     lambda: services.record_payout(manager, affiliate, method="Bank"))
    assert sum(1 for r in results if r is not None) == 1
    assert len(errors) == 1 and isinstance(errors[0], ServiceError) and errors[0].code == "nothing_to_pay"
    assert Payout.objects.count() == 1 and Commission.objects.get().status == CommissionStatus.PAID


def test_the_paid_event_arriving_twice_at_once_earns_one_commission(manager, referred, affiliate, tax):
    invoice = pay_invoice(manager, referred[1], "1000.00")
    Commission.objects.all().delete()
    results, errors = run_in_threads(lambda: services.on_invoice_paid(invoice), lambda: services.on_invoice_paid(invoice))
    assert errors == [] and Commission.objects.count() == 1
    assert sum(1 for r in results if r is not None) == 1
