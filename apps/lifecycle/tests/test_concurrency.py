"""
Races only a real database can show (PostgreSQL; SQLite ignores row locks): two reviewers approving one request, and two
workers ending one service. The same technique as ``apps.renewals.tests.test_concurrency``.
"""
import threading
import time

import pytest
from django.db import connection, connections

from apps.core.exceptions import ServiceError
from apps.hosting import services as hosting_services
from apps.hosting.models import HostingAccount, HostingStatus
from apps.lifecycle import services
from apps.lifecycle.models import CancellationRequest, CancellationStatus

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


def test_two_reviewers_approving_one_request_approve_it_once(account, owner, manager, monkeypatch):
    cr = services.request_cancellation(owner, account, reason_code="not_needed", timing="end_of_term")
    original = services._cancel_open_renewals

    def slow(*args, **kwargs):
        time.sleep(0.4)  # hold the row lock so an unlocked second reviewer would also get through
        return original(*args, **kwargs)

    monkeypatch.setattr(services, "_cancel_open_renewals", slow)
    results, errors = run_in_threads(lambda: services.approve(manager, cr), lambda: services.approve(manager, cr))
    assert sum(1 for r in results if r is not None) == 1
    assert len(errors) == 1 and isinstance(errors[0], ServiceError) and errors[0].code == "invalid_status"
    assert CancellationRequest.objects.get(pk=cr.pk).status == CancellationStatus.APPROVED


def test_two_workers_ending_the_same_service_end_it_once(account, owner, manager, monkeypatch):
    cr = services.approve(manager, services.request_cancellation(owner, account, reason_code="not_needed",
                                                                 timing="end_of_term"))
    calls = []
    real = hosting_services.terminate_account

    def slow_terminate(*args, **kwargs):
        calls.append(1)
        time.sleep(0.5)  # the second worker arrives while the first is still talking to the server
        return real(*args, **kwargs)

    monkeypatch.setattr(hosting_services, "terminate_account", slow_terminate)
    results, errors = run_in_threads(lambda: services.execute(cr), lambda: services.execute(cr))
    assert errors == [] and len(calls) == 1
    assert HostingAccount.objects.get(pk=account.pk).status == HostingStatus.TERMINATED
    assert CancellationRequest.objects.get(pk=cr.pk).status == CancellationStatus.COMPLETED
