"""The health panel is green only when the thing is really happening, not when it is merely configured."""
from datetime import datetime, timedelta, timezone

import pytest
from django.core.cache import cache

from apps.console import widgets
from apps.core import job_marks
from apps.domains.adapters.base import RegistrarError
from apps.domains.models import RegistrarProvider
from apps.notifications.models import EmailMessage, EmailProvider

pytestmark = pytest.mark.django_db


@pytest.fixture
def real_run(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = False  # as on the server: a worker and a scheduler are supposed to be running
    cache.clear()


def test_scheduled_jobs_are_red_until_a_job_has_really_finished_lately(real_run):
    assert widgets._scheduled_jobs()[0] is False
    cache.set(job_marks.PREFIX + "apps.orders.tasks.sweep_stuck_orders_task", (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(), None)
    assert widgets._scheduled_jobs()[0] is True
    cache.set(job_marks.PREFIX + "apps.orders.tasks.sweep_stuck_orders_task", (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(), None)
    assert widgets._scheduled_jobs()[0] is False  # the scheduler stopped


def test_usage_sync_is_red_when_it_is_scheduled_but_has_not_run(real_run):
    assert widgets._usage_sync()[0] is False
    cache.set(job_marks.PREFIX + "apps.hosting.tasks.record_resource_readings_task", datetime.now(timezone.utc).isoformat(), None)
    assert widgets._usage_sync()[0] is True


def test_backups_are_red_with_none_or_a_stale_one_and_green_with_a_fresh_one(real_run, settings, tmp_path, monkeypatch):
    from apps.console import storage

    settings.PANEL_BACKUP_DIR = str(tmp_path)
    assert widgets._backups()[0] is False
    name = "panel-postgresql-20261017T013000Z-full.bak.enc"
    (tmp_path / name).write_bytes(b"x")
    old = (datetime.now(timezone.utc) - timedelta(hours=60)).timestamp()
    import os

    os.utime(tmp_path / name, (old, old))
    assert widgets._backups()[0] is False and "hours old" in widgets._backups()[1]
    os.utime(tmp_path / name, None)
    assert widgets._backups()[0] is True


def test_a_manual_registrar_is_red_and_a_real_one_is_asked(real_run, monkeypatch):
    RegistrarProvider.objects.create(name="Manual", kind="manual", is_active=True)
    ok, detail = widgets._registrar()
    assert ok is False and "NOT registered" in detail
    RegistrarProvider.objects.update(is_active=False)
    RegistrarProvider.objects.create(name="OnlineNIC", kind="onlinenic", is_active=True, sandbox=False)
    calls = []

    def refuse(self, domain):
        calls.append(domain)
        raise RegistrarError("Your IP does not exist in IP whitelist.")

    monkeypatch.setattr("apps.domains.adapters.onlinenic.OnlineNicAdapter.check_availability", refuse)
    ok, detail = widgets._registrar()
    assert ok is False and "whitelist" in detail
    widgets._registrar()
    assert len(calls) == 1  # remembered for a few minutes, not asked on every page view
    cache.clear()
    monkeypatch.setattr("apps.domains.adapters.onlinenic.OnlineNicAdapter.check_availability", lambda self, d: True)
    assert widgets._registrar()[0] is True


def test_the_email_provider_is_red_when_every_email_of_the_last_day_failed(real_run):
    EmailProvider.objects.create(name="SMTP", kind="smtp", is_active=True)
    assert widgets._email()[0] is True  # nothing sent yet: nothing is known to be wrong
    EmailMessage.objects.create(template="t", to_email="a@b.test", subject="s", body_text="b", status="failed")
    ok, detail = widgets._email()
    assert ok is False and "none went out" in detail
    EmailMessage.objects.create(template="t", to_email="a@b.test", subject="s", body_text="b", status="sent")
    assert widgets._email()[0] is True
