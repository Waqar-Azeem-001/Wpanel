"""
The customer's PHP screen: per domain, the PHP version, memory, upload and run-time limits, error display and extensions, chosen
only from what the plan offers (never a raw configuration file). Every operation goes through the hosting-control gateway; the
provider refuses anything outside its lists, an extension the website needs, and an upload limit above the memory limit.
"""
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core import portal
from apps.core.exceptions import ServiceError

from . import dashboard
from .views import _own_account
from .views_domains import _read, _run, _supports


def _php_url(account, domain=""):
    url = reverse("hosting_customer:php", args=[account.pk])
    return url + ("?" + urlencode({"domain": domain}) if domain else "")


@login_required
def my_hosting_php(request, pk):
    account = _own_account(request, pk)
    sidebar = portal.service_sidebar(request, account)
    if not _supports(account, "php"):
        return render(request, "hosting/customer/section.html", {
            "account": account, "sidebar": sidebar, "section": dashboard.section_page(request.user, account, "php")})
    domains = []
    if _supports(account, "domains"):
        try:
            domains = [d.name for d in _read(request, account, "domains", "list_domains")]
        except ServiceError:
            domains = []
    domains = domains or [account.domain]
    domain = request.GET.get("domain") if request.GET.get("domain") in domains else domains[0]
    try:
        options = _read(request, account, "php", "php_options")
        current = _read(request, account, "php", "get_php_settings", domain)
    except ServiceError as exc:
        messages.error(request, exc.message)
        options = current = None
    try:
        extensions = _read(request, account, "php", "list_php_extensions", domain)
    except ServiceError:
        extensions = []  # a provider that does not let the account choose extensions (cPanel) simply has none to show
    return render(request, "hosting/customer/php.html", {
        "account": account, "sidebar": sidebar, "domains": domains, "domain": domain, "options": options, "current": current,
        "extensions": extensions, "domain_urls": [(d, _php_url(account, d)) for d in domains],
        "action_url": reverse("hosting_customer:php_action", args=[account.pk]),
        "eol": bool(options and current and current.version in options.end_of_life)})


@require_POST
@login_required
def my_hosting_php_action(request, pk):
    account = _own_account(request, pk)
    post = request.POST
    action, domain = post.get("action", ""), post.get("domain", "")
    try:
        if action == "settings":
            changes = {"version": post.get("version", ""), "memory_limit": post.get("memory_limit", ""),
                       "upload_max_filesize": post.get("upload_max_filesize", ""),
                       "max_execution_time": post.get("max_execution_time", ""), "display_errors": post.get("display_errors") == "1"}
            _run(request, account, "php", "set_php_settings", domain, changes,
                 metadata={"domain": domain, "version": changes["version"], "display_errors": changes["display_errors"]})
            messages.success(request, "PHP settings saved.")
        elif action == "extension":
            name, enabled = post.get("name", ""), post.get("enabled") == "1"
            _run(request, account, "php", "set_php_extension", domain, name, enabled,
                 metadata={"domain": domain, "extension": name, "enabled": enabled})
            messages.success(request, f"{name} is now {'on' if enabled else 'off'}.")
        else:
            raise ServiceError("That action is not available.", code="unknown_action")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect(_php_url(account, domain))
