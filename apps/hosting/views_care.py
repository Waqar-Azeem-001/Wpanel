"""
The customer's Care page for one website: a speed test, a malware scan, WordPress updates and automatic backups on one calm screen. The work is in ``care``; this only
reads the choices, calls it, and says in plain words what happened.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.audit import services as audit
from apps.core import portal
from apps.core.exceptions import ServiceError

from . import care, care_tools, plan_sites, site_apps
from .models import SiteCare
from .views import _own_account


def _site(request, pk, domain):
    """(account, domain) for the plan's own website ("") or one of the websites on a domain added to the plan (404 for any other name)."""
    account = _own_account(request, pk)
    domain = "" if (domain or account.domain).lower() == account.domain.lower() else domain.lower()
    if domain and domain not in {s["domain"] for s in plan_sites.summary(request.user, account)["sites"]}:
        raise Http404
    return account, domain


def _name(*args, domain=""):
    """The address of a Care page: the plan's own website, or the same page for an added domain."""
    page = args[0]
    pk = args[1]
    if domain:
        return reverse("hosting_customer:site_" + page, args=[pk, domain])
    return reverse("hosting_customer:" + page, args=[pk])


def _url(account, domain=""):
    return _name("care", account.pk, domain=domain)


@login_required
def my_hosting_care(request, pk, domain=""):
    account, domain = _site(request, pk, domain)
    settings = care.care_for(account, domain)
    scan = care.poll_scan(request.user, account, domain)
    backups = care.backup_state(request.user, account) if not domain else None
    speed = account.speed_tests.filter(domain=domain, kind="server").first()
    return render(request, "hosting/customer/care.html", {
        "account": account, "site_domain": site_apps.site_domain(account, domain), "visit_url": site_apps.site_url(account, domain), "domain": domain,
        "sidebar": portal.service_sidebar(request, account), "care": settings, "scan": scan,
        "speed": speed, "pagespeed": account.speed_tests.filter(domain=domain, kind="pagespeed").first(), "backups": backups,
        "quarantined": settings.quarantine, "performance_ok": care_tools.performance_supported(account),
        "traffic_url": reverse("hosting_customer:care_traffic", args=[account.pk]) if not domain else "",
        "wordpress_url": _name("care_wordpress", account.pk, domain=domain),
        "wordpress": care.can_manage_updates(account, domain), "scan_choices": SiteCare.SCAN_CHOICES, "core_choices": SiteCare.CORE_CHOICES,
        "action_url": _name("care_action", account.pk, domain=domain),
        "files_url": reverse("hosting_customer:section", args=[account.pk, "files"]),
        "backups_url": reverse("hosting_customer:section", args=[account.pk, "backups"])})


@require_POST
@login_required
def my_hosting_care_action(request, pk, domain=""):
    account, domain = _site(request, pk, domain)
    post, action = request.POST, request.POST.get("action", "")
    try:
        if action == "speed":
            result = care.speed_test(account, domain=domain)
            if result.ok:
                messages.success(request, f"Speed test done: {result.score} out of 100.")
            else:
                messages.error(request, result.error or "The home page answered with an error.")
        elif action == "scan":
            scan = care.start_scan(request.user, account, domain=domain)
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
            settings = care.care_for(account, domain)
            settings.scan_schedule = value
            settings.save(update_fields=["scan_schedule", "updated_at"])
            audit.record("hosting.scan_schedule_set", actor=request.user, target=account, metadata={"schedule": value}, request=request)
            messages.success(request, "The scan schedule was saved.")
        elif action == "updates":
            care.save_updates(request.user, account, post.get("core", ""), post.get("plugins") == "1", post.get("themes") == "1", domain=domain)
            messages.success(request, "WordPress will keep itself up to date as you chose. WordPress checks twice a day, when your website is visited.")
        elif action == "pagespeed":
            result = care_tools.pagespeed(account, post.get("strategy", "mobile"), domain=domain)
            messages.success(request, f"Google PageSpeed score: {result.score} out of 100.")
        elif action == "site_options":
            care_tools.save_site_options(request.user, account, post.get("maintenance") == "1", post.get("discourage_search") == "1", domain=domain)
            messages.success(request, "Saved. The website now uses these choices.")
        elif action == "performance":
            care_tools.save_performance(request.user, account, post.get("enabled") == "1", domain=domain)
            messages.success(request, "Performance rules are on." if post.get("enabled") == "1" else "Performance rules are off.")
        elif action == "quarantine":
            care_tools.quarantine_file(request.user, account, post.get("path", ""), domain=domain)
            messages.success(request, "The file was moved aside. Nothing was deleted; you can put it back from the list below.")
        elif action == "restore":
            care_tools.restore_file(request.user, account, post.get("stored", ""), domain=domain)
            messages.success(request, "The file was put back.")
        elif action == "backup_schedule":
            if domain:
                raise ServiceError("Backups cover the whole hosting plan: set them on the plan's own website.", code="plan_level")
            care.save_backup_schedule(request.user, account, post.get("schedule", ""))
            messages.success(request, "The backup schedule was saved.")
        else:
            raise ServiceError("That action is not available.", code="unknown_action")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect(_url(account, domain))


@login_required
def my_hosting_care_traffic(request, pk):
    from . import control

    account = _own_account(request, pk)
    try:
        has_statistics = "insights" in control.capabilities_for(account)
    except ServiceError:
        has_statistics = False
    return render(request, "hosting/customer/care_traffic.html", {
        "account": account, "sidebar": portal.service_sidebar(request, account), "traffic": care_tools.traffic(request.user, account),
        "care_url": _url(account), "statistics_url": reverse("hosting_customer:statistics", args=[account.pk]) if has_statistics else ""})


@login_required
def my_hosting_statistics(request, pk):
    """Open the hosting server's own visitor statistics (AWStats) for the main domain through a single-use sign-in."""
    from urllib.parse import urlsplit

    from . import control

    account = _own_account(request, pk)
    try:
        url = control.perform(request.user, account, "insights", "statistics_access", account.domain, request=request, audit_metadata={"domain": account.domain})
        if urlsplit(url).scheme not in ("http", "https"):
            raise ServiceError("The statistics address was not valid.", code="bad_statistics_url")
    except ServiceError as exc:
        messages.error(request, exc.message)
        return redirect("hosting_customer:care_traffic", pk=account.pk)
    return redirect(url)


@login_required
def my_hosting_care_wordpress(request, pk, domain=""):
    account, domain = _site(request, pk, domain)
    data, error = None, ""
    if care.can_manage_updates(account, domain) and request.GET.get("load") == "1":
        try:
            data = care_tools.wordpress_action(request.user, account, "inventory", domain=domain)
        except ServiceError as exc:
            error = exc.message
    return render(request, "hosting/customer/care_wordpress.html", {
        "account": account, "site_domain": site_apps.site_domain(account, domain), "domain": domain,
        "sidebar": portal.service_sidebar(request, account), "data": data, "error": error, "wordpress": care.can_manage_updates(account, domain),
        "care_url": _url(account, domain), "load_url": _name("care_wordpress", account.pk, domain=domain) + "?load=1",
        "action_url": _name("care_wordpress_action", account.pk, domain=domain)})


@require_POST
@login_required
def my_hosting_cache_clear(request, pk):
    """Clear the website's caches (WordPress's own and the well-known page-cache plugins) from the dashboard's Essentials card."""
    account = _own_account(request, pk)
    try:
        care_tools.wordpress_action(request.user, account, "flush_cache")
        messages.success(request, "The cache was cleared. Your visitors get fresh pages from now on.")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect("hosting_customer:detail", pk=account.pk)


@require_POST
@login_required
def my_hosting_care_wordpress_action(request, pk, domain=""):
    account, domain = _site(request, pk, domain)
    action, target = request.POST.get("action", ""), request.POST.get("target", "")
    try:
        care_tools.wordpress_action(request.user, account, action, target, domain=domain)
        messages.success(request, "Done.")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect(_name("care_wordpress", account.pk, domain=domain) + "?load=1")
