"""The public status page: open to everyone, says what is wrong in plain words, never shows internal detail, and never looks at a server itself (a background job does)."""
import time
from datetime import timedelta

import pytest
from django.conf import settings
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.core import status

pytestmark = pytest.mark.django_db


def page(client):
    return client.get(reverse("status")).content.decode()


def test_the_status_page_is_public_and_says_all_is_well_once_the_job_has_looked(client):
    status.refresh()
    response = client.get(reverse("status"))
    html = response.content.decode()
    assert response.status_code == 200 and "System status" in html and "Customer portal" in html and "Operational" in html
    assert "seconds ago" in html and "public" in response["Cache-Control"]


def test_a_down_component_changes_the_headline_and_a_failing_check_shows_as_checking(client, monkeypatch):
    monkeypatch.setattr(status, "_servers", lambda: "down")
    status.refresh()
    html = page(client)
    assert "We are fixing an outage" in html and "Outage" in html

    def boom():
        raise RuntimeError("secret internal address 10.0.0.5")

    monkeypatch.setattr(status, "_servers", boom)
    status.refresh()
    html = page(client)
    assert "Checking" in html and "10.0.0.5" not in html and "secret" not in html


def test_the_page_never_looks_at_a_server_itself(client, monkeypatch):
    def forbidden():
        raise AssertionError("the status page looked at a server while a visitor waited")

    for name in ("_servers", "_email", "_jobs"):
        monkeypatch.setattr(status, name, forbidden)
    cache.clear()
    html = page(client)  # nothing has been kept yet: it says so, and still answers
    assert "Not checked yet" in html and "Customer portal" in html
    monkeypatch.setattr(status, "_servers", lambda: "ok")
    monkeypatch.setattr(status, "_email", lambda: "ok")
    monkeypatch.setattr(status, "_jobs", lambda: "ok")
    status.refresh()
    for name in ("_servers", "_email", "_jobs"):
        monkeypatch.setattr(status, name, forbidden)
    assert "Operational" in page(client)  # served from what the job kept


def test_old_figures_are_shown_with_a_warning_when_the_job_has_stopped(client):
    status.refresh()
    kept = cache.get(status.CACHE_KEY)
    kept["checked"] = timezone.now() - timedelta(minutes=10)
    cache.set(status.CACHE_KEY, kept, 3600)
    html = page(client)
    assert "monitoring has stopped updating" in html and "10 minutes ago" in html and "Operational" in html  # old data, plainly marked


def test_a_server_that_does_not_answer_cannot_hold_the_job_up(monkeypatch):
    def hangs():
        time.sleep(3)
        return "ok"

    monkeypatch.setattr(status, "_servers", hangs)
    monkeypatch.setattr(status, "PROBE_SECONDS", 0.3)
    started = time.monotonic()
    result = status.refresh()
    assert time.monotonic() - started < 2
    assert dict((n, s) for n, s, _ in result["rows"])["Hosting servers"] == "degraded"


def test_the_background_job_is_scheduled_every_50_seconds_and_keeps_the_answer(monkeypatch):
    entry = settings.CELERY_BEAT_SCHEDULE["refresh-public-status"]
    assert entry["task"] == "apps.core.tasks.refresh_status_task" and entry["schedule"] == 50.0
    from apps.core.tasks import refresh_status_task

    cache.clear()
    refresh_status_task()
    assert cache.get(status.CACHE_KEY) is not None


def test_the_footer_links_to_the_status_page(client):
    assert reverse("status") in client.get("/").content.decode()
