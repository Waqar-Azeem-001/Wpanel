"""The staff console pages. Widgets, search and the log read existing records; nothing here writes."""
import csv

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.roles import perm
from apps.audit import services as audit_services
from apps.audit.models import AuditEvent
from apps.core.decorators import portal_permission_required, staff_required
from apps.core.web import ACTION_ERRORS, error_text

from . import search as search_module
from . import widgets


@staff_required
def dashboard(request):
    user = request.user
    shortcuts = [(label, name) for label, name, codename in (
        ("New client", "clients_staff:create", "manage_clients"), ("New invoice", "billing_staff:invoice_new", "manage_billing"),
        ("New order", "orders_staff:new", "manage_orders"), ("Open a ticket", "support_staff:ticket_new", "view_support"))
        if user.has_perm(perm(codename))]
    hour = timezone.localtime().hour
    greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
    return render(request, "console/dashboard.html", {"widgets": widgets.visible_widgets(user), "shortcuts": shortcuts,
                                                       "greeting": greeting})


@staff_required
def widget(request, key):
    """One widget, on its own (the dashboard loads each of these as it appears)."""
    found = widgets.BY_KEY.get(key)
    if found is None:
        raise Http404
    if found not in widgets.visible_widgets(request.user):
        raise PermissionDenied
    return render(request, "console/widget.html", widgets.context_for(found, request.user))


@staff_required
def search(request):
    term = request.GET.get("q", "").strip()
    groups = search_module.search(request.user, term)
    return render(request, "console/search.html", {
        "term": term, "groups": groups, "too_short": bool(term) and len(term) < search_module.MIN_LENGTH,
        "total": sum(g.total for g in groups)})


def _filtered_events(request):
    term = request.GET.get("q", "").strip()
    action = request.GET.get("action", "").strip()
    events = AuditEvent.objects.select_related("actor")
    if term:
        events = events.filter(Q(action__icontains=term) | Q(actor_repr__icontains=term) | Q(target_repr__icontains=term)
                               | Q(target_type__icontains=term) | Q(target_id=term) | Q(request_id=term))
    if action:
        events = events.filter(action__startswith=action)
    return events.order_by("-created_at", "-id"), term, action


@portal_permission_required(perm("view_audit_log"))
def audit_log(request):
    events, term, action = _filtered_events(request)
    kinds = sorted({a.split(".")[0] for a in AuditEvent.objects.order_by().values_list("action", flat=True).distinct()})
    page = Paginator(events, 50).get_page(request.GET.get("page"))
    return render(request, "console/audit_log.html", {
        "page": page, "term": term, "action": action, "kinds": kinds, "is_super": request.user.is_superuser,
        "min_purge_days": audit_services.MIN_PURGE_DAYS})


@portal_permission_required(perm("view_audit_log"))
def audit_export(request):
    """The filtered log as a spreadsheet file (the newest 5,000 matching entries)."""
    events, _, _ = _filtered_events(request)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="audit-log.csv"'
    writer = csv.writer(response)
    writer.writerow(["When", "Who", "Action", "Record type", "Record", "Details", "IP address", "Request id"])
    for event in events[:5000]:
        writer.writerow([event.created_at.isoformat(), event.actor_repr or "system", event.action, event.target_type,
                         event.target_repr, str(event.metadata), event.ip_address or "", event.request_id])
    return response


@require_POST
@portal_permission_required(perm("view_audit_log"))
def audit_purge(request):
    """Super Admin only (the service refuses anyone else): delete entries older than N days."""
    raw = request.POST.get("days", "")
    try:
        count = audit_services.purge(request.user, older_than_days=int(raw) if raw.isdigit() else 0,
                                     area=request.POST.get("area", "").strip(), request=request)
    except ACTION_ERRORS as exc:
        messages.error(request, error_text(exc))
    else:
        messages.success(request, f"{count} audit entr{'y' if count == 1 else 'ies'} deleted.")
    return redirect("console:audit_log")
