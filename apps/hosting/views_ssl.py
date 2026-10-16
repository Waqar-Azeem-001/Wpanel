"""
The customer's SSL screen: one certificate per domain with its status, issuer and expiry, switches for automatic renewal and
"always use HTTPS", and install, renew and remove. Every operation goes through the hosting-control gateway; the provider checks
that the domain points at the server before it issues a certificate and refuses "always use HTTPS" without a working one.
"""
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core import portal
from apps.core.exceptions import ServiceError

from apps.products.categories import lowest_price
from apps.products.models import Addon, AddonKind, CatalogStatus
from urllib.parse import urlencode

from . import dashboard
from .views import _own_account
from .views_domains import _read, _run, _supports


def _upsell(account):
    """The paid extras to suggest under the certificates: SSL certificates, and WHOIS privacy for the domains. Only active add-ons staff set up; asking for one opens a ticket already filled in."""
    ticket = reverse("support_customer:new")
    out = []
    for kind, title in ((AddonKind.SSL, "Upgrade your SSL certificate"), (AddonKind.WHOIS_PRIVACY, "Add WHOIS privacy")):
        for addon in Addon.objects.filter(kind=kind, status=CatalogStatus.ACTIVE).prefetch_related("prices").order_by("name"):
            price = lowest_price(addon)
            query = urlencode({"hosting_account": account.pk, "subject": f"Order: {addon.name} for {account.domain}",
                               "body": f"Hello, I would like to order {addon.name} for {account.domain}."})
            out.append({"addon": addon, "kind": kind, "title": title, "price": price, "url": f"{ticket}?{query}"})
    return out


def _ssl_url(account):
    return reverse("hosting_customer:ssl", args=[account.pk])


@login_required
def my_hosting_ssl(request, pk):
    account = _own_account(request, pk)
    sidebar = portal.service_sidebar(request, account)
    if not _supports(account, "ssl"):
        return render(request, "hosting/customer/section.html", {
            "account": account, "sidebar": sidebar, "section": dashboard.section_page(request.user, account, "ssl")})
    try:
        rows = _read(request, account, "ssl", "list_ssl_status")
    except ServiceError as exc:
        messages.error(request, exc.message)
        rows = None
    counts = {k: sum(1 for r in rows or [] if r.status == k) for k in ("active", "expiring", "expired", "none")}
    return render(request, "hosting/customer/ssl.html", {
        "account": account, "sidebar": sidebar, "counts": counts,
        "rows": None if rows is None else [{"s": r, "expires": datetime.fromisoformat(r.expires_at) if r.expires_at else None} for r in rows],
        "upsell": _upsell(account), "domain_page_url": reverse("hosting_customer:domains", args=[account.pk]) if _supports(account, "domains") else "",
        "action_url": reverse("hosting_customer:ssl_action", args=[account.pk]),
        "dns_url": reverse("hosting_customer:dns", args=[account.pk]) if _supports(account, "dns") else ""})


@require_POST
@login_required
def my_hosting_ssl_action(request, pk):
    account = _own_account(request, pk)
    post = request.POST
    action, domain = post.get("action", ""), post.get("domain", "")
    enabled = post.get("enabled") == "1"
    try:
        if action == "install":
            _run(request, account, "ssl", "request_ssl", domain, metadata={"domain": domain})
            messages.success(request, f"The certificate for {domain} is installed and valid for 90 days.")
        elif action == "remove":
            _run(request, account, "ssl", "remove_ssl", domain, metadata={"domain": domain})
            messages.success(request, f"The certificate for {domain} was removed.")
        elif action == "auto_renew":
            _run(request, account, "ssl", "set_ssl_auto_renew", domain, enabled, metadata={"domain": domain, "enabled": enabled})
            messages.success(request, "Automatic renewal is on." if enabled else "Automatic renewal is off. Renew before it expires.")
        elif action == "https":
            _run(request, account, "ssl", "set_https_redirect", domain, enabled, metadata={"domain": domain, "enabled": enabled})
            messages.success(request, "Visitors are now sent to the secure version." if enabled else "Visitors are no longer forced to HTTPS.")
        else:
            raise ServiceError("That action is not available.", code="unknown_action")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect(_ssl_url(account))
