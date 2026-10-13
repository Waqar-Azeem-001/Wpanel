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

from . import care, care_tools
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
    speed = account.speed_tests.filter(kind="server").first()
    return render(request, "hosting/customer/care.html", {
        "account": account, "sidebar": portal.service_sidebar(request, account), "care": settings, "scan": scan,
        "speed": speed, "pagespeed": account.speed_tests.filter(kind="pagespeed").first(), "backups": backups,
        "quarantined": settings.quarantine, "performance_ok": care_tools.performance_supported(account),
        "traffic_url": reverse("hosting_customer:care_traffic", args=[account.pk]), "wordpress_url": reverse("hosting_customer:care_wordpress", args=[account.pk]),
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
        elif action == "pagespeed":
            result = care_tools.pagespeed(account, post.get("strategy", "mobile"))
            messages.success(request, f"Google PageSpeed score: {result.score} out of 100.")
        elif action == "site_options":
            care_tools.save_site_options(request.user, account, post.get("maintenance") == "1", post.get("discourage_search") == "1")
            messages.success(request, "Saved. The website now uses these choices.")
        elif action == "performance":
            care_tools.save_performance(request.user, account, post.get("enabled") == "1")
            messages.success(request, "Performance rules are on." if post.get("enabled") == "1" else "Performance rules are off.")
        elif action == "quarantine":
            care_tools.quarantine_file(request.user, account, post.get("path", ""))
            messages.success(request, "The file was moved aside. Nothing was deleted; you can put it back from the list below.")
        elif action == "restore":
            care_tools.restore_file(request.user, account, post.get("stored", ""))
            messages.success(request, "The file was put back.")
        elif action == "backup_schedule":
            care.save_backup_schedule(request.user, account, post.get("schedule", ""))
            messages.success(request, "The backup schedule was saved.")
        else:
            raise ServiceError("That action is not available.", code="unknown_action")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect(_url(account))


@login_required
def my_hosting_care_traffic(request, pk):
    account = _own_account(request, pk)
    return render(request, "hosting/customer/care_traffic.html", {
        "account": account, "sidebar": portal.service_sidebar(request, account), "traffic": care_tools.traffic(request.user, account),
        "care_url": _url(account)})


@login_required
def my_hosting_care_wordpress(request, pk):
    account = _own_account(request, pk)
    data, error = None, ""
    if care.can_manage_updates(account) and request.GET.get("load") == "1":
        try:
            data = care_tools.wordpress_action(request.user, account, "inventory")
        except ServiceError as exc:
            error = exc.message
    return render(request, "hosting/customer/care_wordpress.html", {
        "account": account, "sidebar": portal.service_sidebar(request, account), "data": data, "error": error, "wordpress": care.can_manage_updates(account),
        "care_url": _url(account), "load_url": reverse("hosting_customer:care_wordpress", args=[account.pk]) + "?load=1",
        "action_url": reverse("hosting_customer:care_wordpress_action", args=[account.pk])})


@require_POST
@login_required
def my_hosting_care_wordpress_action(request, pk):
    account = _own_account(request, pk)
    action, target = request.POST.get("action", ""), request.POST.get("target", "")
    try:
        care_tools.wordpress_action(request.user, account, action, target)
        messages.success(request, "Done.")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect(reverse("hosting_customer:care_wordpress", args=[account.pk]) + "?load=1")
