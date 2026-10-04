"""
Phase 17: automation status - whether a worker is running, and the last run of every job, recorded by
``apps.core.celery_signals`` into ``TaskRun`` without any task having to report itself.
"""
import pytest
from celery import shared_task
from django.test import Client as HttpClient
from django.urls import reverse

from apps.accounts.roles import Role
from apps.core.models import TaskRun, TaskRunStatus
from apps.core.tasks import ping

pytestmark = pytest.mark.django_db


def browser(user=None):
    client = HttpClient(HTTP_HOST="localhost", raise_request_exception=False)
    if user is not None:
        client.force_login(user)
    return client


@shared_task
def _sample_ok_task():
    return "done"


@shared_task
def _sample_failing_task():
    raise ValueError("on purpose")


# --- Recording a task's own run --------------------------------------------------------------------------------

def test_a_successful_task_is_recorded(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    TaskRun.objects.filter(name=_sample_ok_task.name).delete()
    _sample_ok_task.delay()
    run = TaskRun.objects.get(name=_sample_ok_task.name)
    assert run.status == TaskRunStatus.SUCCESS and run.detail == "done" and run.started_at and run.finished_at


def test_a_failing_task_is_recorded_with_the_exception_and_not_lost(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = False
    TaskRun.objects.filter(name=_sample_failing_task.name).delete()
    _sample_failing_task.delay()
    run = TaskRun.objects.get(name=_sample_failing_task.name)
    assert run.status == TaskRunStatus.FAILURE and "on purpose" in run.detail


def test_the_ping_task_itself_is_recorded_and_answers_pong():
    TaskRun.objects.filter(name=ping.name).delete()
    assert ping.delay().get() == "pong"
    assert TaskRun.objects.get(name=ping.name).status == TaskRunStatus.SUCCESS


# --- The health widget's own use of it --------------------------------------------------------------------------

def test_the_health_widget_reports_the_background_worker(staff):
    from apps.console import widgets

    admin = staff(Role.ADMIN)
    checks = {c["name"]: c for c in widgets.health_widget(admin)["checks"]}
    assert "Background jobs" in checks and checks["Background jobs"]["ok"] is True


def test_the_health_widget_reports_a_broken_worker_as_a_problem_not_an_error(staff, monkeypatch):
    from apps.console import widgets

    def broken():
        raise TimeoutError("no worker answered")

    monkeypatch.setattr(widgets, "_celery", broken)
    checks = {c["name"]: c for c in widgets.health_widget(staff(Role.ADMIN))["checks"]}
    assert checks["Background jobs"]["ok"] is False


# --- The automation page ----------------------------------------------------------------------------------------

def test_the_automation_page_needs_view_settings(staff):
    assert browser().get(reverse("console:automation")).status_code == 302  # anonymous: sent to sign in
    assert browser(staff(Role.MANAGER)).get(reverse("console:automation")).status_code == 403  # no view_settings
    assert browser(staff(Role.ADMIN)).get(reverse("console:automation")).status_code == 200


def test_the_page_lists_every_scheduled_job_and_says_which_have_never_run(staff, settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    TaskRun.objects.all().delete()
    page = browser(staff(Role.ADMIN)).get(reverse("console:automation")).content.decode()
    for entry in settings.CELERY_BEAT_SCHEDULE.values():
        assert entry["task"] in page
    assert "Never run yet." in page


def test_a_scheduled_jobs_last_run_shows_on_the_page_once_it_has_run(staff, settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    from apps.orders.tasks import purge_guest_carts_task

    purge_guest_carts_task.delay()
    page = browser(staff(Role.ADMIN)).get(reverse("console:automation")).content.decode()
    assert "Success" in page and "apps.orders.tasks.purge_guest_carts_task" in page


def test_an_on_demand_task_appears_in_the_other_recent_jobs_table_not_the_scheduled_one(staff, settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    TaskRun.objects.filter(name=_sample_ok_task.name).delete()
    _sample_ok_task.delay()
    page = browser(staff(Role.ADMIN)).get(reverse("console:automation"))
    on_demand_names = [r.name for r in page.context["on_demand"]]
    assert _sample_ok_task.name in on_demand_names
    scheduled_names = [j["task"] for j in page.context["jobs"]]
    assert _sample_ok_task.name not in scheduled_names


def test_the_health_widget_lists_the_launch_readiness_checks(staff):
    from apps.console import widgets

    checks = {c["name"]: c for c in widgets.health_widget(staff(Role.ADMIN))["checks"]}
    assert {"Public address", "Error tracking", "Scheduled jobs", "Hosting usage sync"} <= set(checks)
    assert checks["Scheduled jobs"]["ok"] is True
    assert checks["Hosting usage sync"]["ok"] is False  # honest: it is a known open item until built
