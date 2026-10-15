"""
WordPress > Overview: the real state of the website's WordPress in one place with the switches that matter (HTTPS, maintenance mode, search engines),
the facts (version, PHP, database, certificate, backups) and one-click recommended plugins and themes. Each switch changes the real site; the page then
shows what the server reports.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core import portal
from apps.core.exceptions import ServiceError

from . import care, care_tools, control, dashboard, wp_recommended
from .adapters.capabilities import Capability
from .views import _own_account


def _is_wordpress(account):
    return care.can_manage_updates(account)


@login_required
def wordpress_overview(request, pk):
    account = _own_account(request, pk)
    if not _is_wordpress(account):
        messages.info(request, "WordPress tools appear here once a WordPress website is set up on this plan.")
        return redirect("hosting_customer:detail", pk=account.pk)
    card = dashboard.website_card(request.user, account)
    settings = care.care_for(account)
    ssl = card["ssl"]
    backups = care.backup_state(request.user, account)
    pk_args = [account.pk]
    return render(request, "hosting/customer/wordpress.html", {
        "account": account, "app": card["app"], "card": card, "care": settings, "ssl": ssl, "backups": backups, "sidebar": portal.service_sidebar(request, account),
        "can_https": ssl is not None, "https_on": bool(ssl and ssl.https_redirect), "ssl_active": bool(ssl and ssl.active),
        "plugins": wp_recommended.PLUGINS, "themes": wp_recommended.THEMES,
        "do_url": reverse("hosting_customer:wordpress_do", args=pk_args), "admin_url": reverse("hosting_customer:wp_admin", args=pk_args),
        "password_url": reverse("hosting_customer:wp_password", args=pk_args), "manager_url": reverse("hosting_customer:care_wordpress", args=pk_args),
        "staging_url": reverse("hosting_customer:staging", args=pk_args), "care_url": reverse("hosting_customer:care", args=pk_args),
        "cache_url": reverse("hosting_customer:cache_clear", args=pk_args)})


@require_POST
@login_required
def wordpress_do(request, pk):
    account = _own_account(request, pk)
    post, action = request.POST, request.POST.get("action", "")
    enabled = post.get("value") == "on"
    try:
        if not _is_wordpress(account):
            raise ServiceError("This is for a WordPress website that WHPanel set up.", code="not_wordpress")
        settings = care.care_for(account)
        if action == "https":
            card = dashboard.website_card(request.user, account)
            if card["ssl"] is None:
                raise ServiceError("This server does not offer the HTTPS switch.", code="capability_not_supported")
            if enabled and not card["ssl"].active:
                raise ServiceError("The website has no active certificate yet, so it cannot be forced to HTTPS. Install the certificate first.", code="no_certificate")
            control.perform(request.user, account, Capability.SSL, "set_https_redirect", account.domain, enabled,
                            audit_metadata={"domain": account.domain, "enabled": enabled}, request=request)
            messages.success(request, "Visitors are now sent to HTTPS." if enabled else "Visitors are no longer forced to HTTPS.")
        elif action == "maintenance":
            care_tools.save_site_options(request.user, account, enabled, settings.discourage_search)
            messages.success(request, "Maintenance mode is on: visitors see a 'be right back' page." if enabled else "Maintenance mode is off.")
        elif action == "search":
            care_tools.save_site_options(request.user, account, settings.maintenance, enabled)
            messages.success(request, "Search engines are asked not to index the website." if enabled else "Search engines may index the website again.")
        elif action == "install":
            slug = post.get("slug", "")
            kind = wp_recommended.kind_of(slug)
            if not kind:
                raise ServiceError("That is not one of the recommended installations.", code="not_recommended")
            care_tools.wordpress_action(request.user, account, "install_" + kind, slug)
            messages.success(request, ("Installed and switched on." if kind == "plugin" else "Installed. Switch it on from Plugins and themes when you want to use it."))
        else:
            raise ServiceError("That action is not available.", code="unknown_action")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect("hosting_customer:wordpress", pk=account.pk)
