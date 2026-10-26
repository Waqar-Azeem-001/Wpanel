"""What each status check decides from the facts it is given (the page and the schedule are in test_status.py)."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.core import job_marks, status
from apps.notifications.models import EmailMessage
from apps.products.models import Server

pytestmark = pytest.mark.django_db


def server(name="A", state="active"):
    return Server.objects.create(name=name, hostname=f"{name.lower()}.example.test", kind="manual", status=state)


def dashboard_says(monkeypatch, *healths):
    cards = [{"health": h, "server": SimpleNamespace(status="active")} for h in healths]
    monkeypatch.setattr("apps.hosting.infra_dashboard.dashboard", lambda: {"cards": cards})


def test_no_active_server_means_the_row_is_left_off_the_page(monkeypatch):
    assert status._servers() is None
    server("Off", state="inactive")
    assert status._servers() is None
    cache.clear()
    monkeypatch.setattr(status, "_bounded", lambda probe, seconds: probe())  # a check normally runs in its own thread, which cannot see this test's in-memory database
    assert "Hosting servers" not in [name for name, _, _ in status.refresh()["rows"]]


@pytest.mark.parametrize("healths,expected", [
    (["healthy"], "ok"), (["healthy", "healthy"], "ok"),
    (["healthy", "degraded"], "degraded"), (["healthy", "maintenance"], "degraded"), (["healthy", "down"], "degraded"),
    (["down"], "down"), (["unreachable", "offline"], "down"),
    (["unknown"], "unknown"), ([], "unknown"),
])
def test_the_servers_row_follows_what_the_infrastructure_dashboard_reports(monkeypatch, healths, expected):
    server()
    dashboard_says(monkeypatch, *healths)
    assert status._servers() == expected


@pytest.mark.parametrize("sent,failed,expected", [(0, 0, "ok"), (5, 0, "ok"), (5, 2, "ok"), (1, 3, "degraded"), (0, 2, "down")])
def test_the_email_row_follows_what_really_went_out_in_the_last_six_hours(sent, failed, expected):
    for _ in range(sent):
        EmailMessage.objects.create(template="t", to_email="a@b.test", subject="s", body_text="b", status="sent")
    for _ in range(failed):
        EmailMessage.objects.create(template="t", to_email="a@b.test", subject="s", body_text="b", status="failed")
    assert status._email() == expected


def test_an_old_failure_does_not_count():
    message = EmailMessage.objects.create(template="t", to_email="a@b.test", subject="s", body_text="b", status="failed")
    EmailMessage.objects.filter(pk=message.pk).update(created_at=timezone.now() - timedelta(hours=7))
    assert status._email() == "ok"


def test_the_jobs_row_needs_a_job_that_finished_in_the_last_twelve_minutes(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = False
    cache.clear()
    assert status._jobs() == "degraded"  # nothing has finished yet
    job_marks.mark_finished(sender=SimpleNamespace(name="apps.orders.tasks.sweep_stuck_orders_task"), state="SUCCESS")
    assert status._jobs() == "ok"
    old = (timezone.now() - timedelta(minutes=30)).isoformat()
    cache.set(job_marks.PREFIX + "apps.orders.tasks.sweep_stuck_orders_task", old, None)
    assert status._jobs() == "degraded"
    settings.CELERY_TASK_ALWAYS_EAGER = True
    assert status._jobs() == "ok"  # development has no scheduler to ask


def test_the_headline_is_the_worst_row(monkeypatch):
    monkeypatch.setattr(status, "_servers", lambda: "ok")
    monkeypatch.setattr(status, "_email", lambda: "degraded")
    monkeypatch.setattr(status, "_jobs", lambda: "ok")
    assert status.refresh()["overall"] == "degraded"
    monkeypatch.setattr(status, "_jobs", lambda: "down")
    assert status.refresh()["overall"] == "down"
    monkeypatch.setattr(status, "_email", lambda: "ok")
    monkeypatch.setattr(status, "_jobs", lambda: "unknown")
    assert status.refresh()["overall"] == "unknown"
