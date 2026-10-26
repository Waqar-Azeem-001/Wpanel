"""The one-at-a-time lock and the "when did this job last finish" marks."""
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from django.core.cache import cache

from apps.core import job_marks
from apps.core.exceptions import ServiceError
from apps.core.locks import single_flight


def test_a_second_caller_waits_for_the_first_and_then_runs_after_it():
    order = []

    def first():
        with single_flight("t-wait"):
            order.append("first in")
            time.sleep(0.4)
            order.append("first out")

    thread = threading.Thread(target=first)
    thread.start()
    time.sleep(0.1)  # the first one holds the lock now
    with single_flight("t-wait"):
        order.append("second in")
    thread.join()
    assert order == ["first in", "first out", "second in"]


def test_a_caller_that_cannot_wait_is_told_clearly_and_the_holder_is_not_disturbed():
    with single_flight("t-busy"):
        with pytest.raises(ServiceError) as busy:
            with single_flight("t-busy", wait=0):
                pytest.fail("the block must not run while another caller holds the lock")
        assert busy.value.code == "in_progress" and busy.value.status_code == 409
        with pytest.raises(ServiceError):
            with single_flight("t-busy", wait=0):
                pass  # still held: the failed attempt did not release it
    with single_flight("t-busy", wait=0):
        pass  # now free


def test_the_lock_is_released_when_the_block_fails():
    with pytest.raises(RuntimeError):
        with single_flight("t-fail"):
            raise RuntimeError("boom")
    with single_flight("t-fail", wait=0):
        pass


def test_a_lock_that_expired_and_was_taken_by_someone_else_is_not_deleted_by_the_old_holder():
    with single_flight("t-steal"):
        cache.set("single-flight:t-steal", "someone-else", 60)  # as if ours had run out and another caller took it
    assert cache.get("single-flight:t-steal") == "someone-else"


def test_a_job_that_succeeded_is_remembered_and_a_failed_or_foreign_one_is_not():
    cache.clear()
    job_marks.mark_finished(sender=SimpleNamespace(name="apps.orders.tasks.sweep_stuck_orders_task"), state="SUCCESS")
    assert job_marks.last_finished("apps.orders.tasks.sweep_stuck_orders_task") is not None
    job_marks.mark_finished(sender=SimpleNamespace(name="apps.x.tasks.broken"), state="FAILURE")
    job_marks.mark_finished(sender=SimpleNamespace(name="celery.backend_cleanup"), state="SUCCESS")
    assert job_marks.last_finished("apps.x.tasks.broken") is None and job_marks.last_finished("celery.backend_cleanup") is None


def test_a_broken_cache_never_fails_the_job_and_a_garbled_mark_reads_as_never(monkeypatch):
    def broken(*args, **kwargs):
        raise ConnectionError("redis is down")

    monkeypatch.setattr(job_marks.cache, "set", broken)
    job_marks.mark_finished(sender=SimpleNamespace(name="apps.x.tasks.job"), state="SUCCESS")  # must not raise
    monkeypatch.undo()
    cache.set(job_marks.PREFIX + "apps.x.tasks.garbled", "not a date", None)
    assert job_marks.last_finished("apps.x.tasks.garbled") is None


def test_a_real_celery_task_leaves_its_mark_when_it_finishes():
    from apps.core.tasks import ping

    cache.clear()
    assert ping.apply().get() == "pong"
    # the signal marks only tasks of this project; ping is one of them
    assert job_marks.last_finished("apps.core.tasks.ping") is not None
    assert datetime.now(timezone.utc) - job_marks.last_finished("apps.core.tasks.ping") < timedelta(minutes=1)
