"""
The customer's Cron jobs screen: scheduled PHP scripts with common schedules, edit, switch off, delete, and "run now" with the last
result. Every operation goes through the hosting-control gateway. A job is never a free shell command: the provider accepts only a
PHP script inside the account and refuses schedules more frequent than the plan allows. The audit trail records the job and the
script, never its output.
"""
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core import portal
from apps.core.exceptions import ServiceError

from . import control, dashboard
from .views import _own_account
from .views_domains import _read, _run, _supports

CUSTOM = "custom"


def _cron_url(account):
    return reverse("hosting_customer:cron", args=[account.pk])


def _when(text):
    return datetime.fromisoformat(text) if text else None


@login_required
def my_hosting_cron(request, pk):
    account = _own_account(request, pk)
    sidebar = portal.service_sidebar(request, account)
    if not _supports(account, "cron"):
        return render(request, "hosting/customer/section.html", {
            "account": account, "sidebar": sidebar, "section": dashboard.section_page(request.user, account, "cron")})
    try:
        options = _read(request, account, "cron", "cron_options")
        jobs = _read(request, account, "cron", "list_cron_jobs")
    except ServiceError as exc:
        messages.error(request, exc.message)
        options, jobs = None, []
    names = {schedule: label for label, schedule in (options.presets if options else [])}
    rows = [{"j": j, "when": names.get(j.schedule, ""), "ran": _when(j.last_run_at)} for j in jobs]
    return render(request, "hosting/customer/cron.html", {
        "account": account, "sidebar": sidebar, "options": options, "rows": rows, "custom": CUSTOM,
        "full": bool(options and len(jobs) >= options.max_jobs), "count": len(jobs),
        "action_url": reverse("hosting_customer:cron_action", args=[account.pk])})


def _schedule(post):
    chosen = post.get("preset", CUSTOM)
    return post.get("schedule", "") if chosen == CUSTOM else chosen


@require_POST
@login_required
def my_hosting_cron_action(request, pk):
    account = _own_account(request, pk)
    post = request.POST
    action, job = post.get("action", ""), post.get("job", "")
    try:
        if action == "add":
            command = post.get("command", "")
            _run(request, account, "cron", "create_cron_job", _schedule(post), command,
                 metadata={"script": command.split(" ")[1] if len(command.split(" ")) > 1 else ""})
            messages.success(request, "The job was added.")
        elif action == "edit":
            control.perform(request.user, account, "cron", "update_cron_job", job, schedule=_schedule(post),
                            command=post.get("command", ""), audit_metadata={"job": job}, request=request)
            messages.success(request, "The job was saved.")
        elif action == "toggle":
            enabled = post.get("enabled") == "1"
            control.perform(request.user, account, "cron", "update_cron_job", job, enabled=enabled,
                            audit_metadata={"job": job, "enabled": enabled}, request=request)
            messages.success(request, "The job is on." if enabled else "The job is off. It stays here until you turn it on again.")
        elif action == "delete":
            _run(request, account, "cron", "delete_cron_job", job, metadata={"job": job})
            messages.success(request, "The job was deleted.")
        elif action == "run":
            _run(request, account, "cron", "run_cron_job", job, metadata={"job": job})
            messages.success(request, "The job ran. The result is shown below it.")
        else:
            raise ServiceError("That action is not available.", code="unknown_action")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect(_cron_url(account))
