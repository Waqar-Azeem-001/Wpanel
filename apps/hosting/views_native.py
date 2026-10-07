"""
TRANSITIONAL (H15): "Advanced tools: Open cPanel". Signs the customer into cPanel with a one-time link, only for what WHPanel cannot do yet.

Order of events on a click: the hosting-control gateway authorises the person for this account (an owner, or staff in Admin Access with
a role that can manage hosting) and checks the account is live; the request is counted against a short-term limit; the provider makes the
link; the request is written to the audit trail (who, which account, nothing about the link); only then is the browser redirected.
The link is never shown, stored or logged, and the response says not to cache it or send it on in a Referer header. The WHM token and
the cPanel password are never involved on this side. Remove with cPanel.
"""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit.models import AuditEvent
from apps.core import portal
from apps.core.exceptions import ServiceError

from . import dashboard
from .models import HostingStatus
from .views import _own_account
from .views_domains import _run, _supports

LIMIT = 6  # sign-ins per account in the window below, counting refused ones: a brake on a stuck button or abuse
WINDOW_MINUTES = 10


def _recent(account):
    return AuditEvent.objects.filter(target_type__endswith="hostingaccount", target_id=str(account.pk), action__startswith="hosting.control",
                                     metadata__operation="open_native_panel")


@login_required
def my_hosting_native(request, pk):
    account = _own_account(request, pk)
    sidebar = portal.service_sidebar(request, account)
    if not _supports(account, "native_panel"):
        return render(request, "hosting/customer/section.html", {
            "account": account, "sidebar": sidebar, "section": dashboard.section_page(request.user, account, "native")})
    past = [{"when": e.created_at, "by": e.actor_repr or "system", "support": bool((e.metadata or {}).get("admin_access")),
             "failed": e.action.endswith("failed")} for e in _recent(account)[:5]]
    return render(request, "hosting/customer/native.html", {
        "account": account, "sidebar": sidebar, "past": past, "open_url": reverse("hosting_customer:native_open", args=[account.pk])})


@require_POST
@login_required
def my_hosting_native_open(request, pk):
    account = _own_account(request, pk)
    back = redirect("hosting_customer:native", pk=account.pk)
    if account.status != HostingStatus.ACTIVE:
        messages.error(request, "Your hosting must be active to open cPanel.")
        return back
    since = timezone.now() - timedelta(minutes=WINDOW_MINUTES)
    if _recent(account).filter(created_at__gte=since).count() >= LIMIT:
        messages.error(request, f"Too many sign-in requests for this account. Wait {WINDOW_MINUTES} minutes and try again.")
        return back
    try:
        session = _run(request, account, "native_panel", "open_native_panel", metadata={"transitional": True})
    except ServiceError as exc:
        messages.error(request, exc.message)
        return back
    response = HttpResponseRedirect(session.url)
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response
