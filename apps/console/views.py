"""The staff console pages. Widgets, search and the log read existing records; nothing here writes."""
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone

from apps.accounts.roles import perm
from apps.audit.models import AuditEvent
from apps.core.decorators import portal_permission_required, staff_required

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


@portal_permission_required(perm("view_audit_log"))
def audit_log(request):
    term = request.GET.get("q", "").strip()
    action = request.GET.get("action", "").strip()
    events = AuditEvent.objects.select_related("actor")
    if term:
        events = events.filter(Q(action__icontains=term) | Q(actor_repr__icontains=term) | Q(target_repr__icontains=term)
                               | Q(target_type__icontains=term) | Q(target_id=term) | Q(request_id=term))
    if action:
        events = events.filter(action__startswith=action)
    kinds = sorted({a.split(".")[0] for a in AuditEvent.objects.order_by().values_list("action", flat=True).distinct()})
    page = Paginator(events.order_by("-created_at", "-id"), 50).get_page(request.GET.get("page"))
    return render(request, "console/audit_log.html", {"page": page, "term": term, "action": action, "kinds": kinds})
