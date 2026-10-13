"""
The customer's Care page for one website: a speed test, a malware scan, WordPress updates and automatic backups on one calm screen. The work is in ``care``; this only
reads the choices, calls it, and says in plain words what happened.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.audit import services as audit
from apps.core import portal
from apps.core.exceptions import ServiceError

from . import care
from .models import SiteCare
from .views import _own_account


def _url(account):
    return reverse("hosting_customer:care", args=[account.pk])


@login_required
def my_hosting_care(request, pk):
    account = _own_account(request, pk)
    settings = care.care_for(account)
    scan = care.poll_scan(request.user, account)
    backups = care.backup_state(request.user, account)
    speed = account.speed_tests.first()
    return render(request, "hosting/customer/care.html", {
        "account": account, "sidebar": portal.service_sidebar(request, account), "care": settings, "scan": scan,
        "speed": speed, "history": list(account.speed_tests.all()[1:6]), "backups": backups,
        "wordpress": care.can_manage_updates(account), "scan_choices": SiteCare.SCAN_CHOICES, "core_choices": SiteCare.CORE_CHOICES,
        "action_url": reverse("hosting_customer:care_action", args=[account.pk]),
        "files_url": reverse("hosting_customer:section", args=[account.pk, "files"]),
        "backups_url": reverse("hosting_customer:section", args=[account.pk, "backups"])})


@require_POST
@login_required
def my_hosting_care_action(request, pk):
    account = _own_account(request, pk)
    post, action = request.POST, request.POST.get("action", "")
    try:
        if action == "speed":
            result = care.speed_test(account)
            if result.ok:
                messages.success(request, f"Speed test done: {result.score} out of 100.")
            else:
                messages.error(request, result.error or "The home page answered with an error.")
        elif action == "scan":
            scan = care.start_scan(request.user, account)
            if scan.state == "failed":
                messages.error(request, scan.error or "The scan could not run.")
            elif scan.state == "running":
                messages.info(request, "The scan is running. This page shows the result when it is done.")
            else:
                messages.success(request, "No problems were found." if not scan.findings else f"The scan found {len(scan.findings)} thing(s) to look at.")
        elif action == "scan_schedule":
            value = post.get("scan_schedule", "")
            if value not in dict(SiteCare.SCAN_CHOICES):
                raise ServiceError("Choose one of the listed options.", code="bad_choice")
            settings = care.care_for(account)
            settings.scan_schedule = value
            settings.save(update_fields=["scan_schedule", "updated_at"])
            audit.record("hosting.scan_schedule_set", actor=request.user, target=account, metadata={"schedule": value}, request=request)
            messages.success(request, "The scan schedule was saved.")
        elif action == "updates":
            care.save_updates(request.user, account, post.get("core", ""), post.get("plugins") == "1", post.get("themes") == "1")
            messages.success(request, "WordPress will keep itself up to date as you chose. WordPress checks twice a day, when your website is visited.")
        elif action == "backup_schedule":
            care.save_backup_schedule(request.user, account, post.get("schedule", ""))
            messages.success(request, "The backup schedule was saved.")
        else:
            raise ServiceError("That action is not available.", code="unknown_action")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect(_url(account))
