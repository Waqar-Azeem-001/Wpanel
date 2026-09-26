"""The Users area: everyone who can sign in (customers and every kind of staff), with the actions the roles allow."""
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.accounts import services as account_services
from apps.accounts.models import AccountStatus, User
from apps.accounts.roles import PRIVILEGED_ROLES, STAFF_ROLES, Role, perm
from apps.audit.models import AuditEvent
from apps.billing.models import OPEN_STATUSES, Invoice
from apps.clients.models import ClientContact
from apps.core.decorators import portal_permission_required
from apps.core.web import ACTION_ERRORS, error_text
from apps.domains.models import Domain
from apps.hosting.models import HostingAccount
from apps.orders.models import Order
from apps.support.models import Ticket

from . import forms

VIEW_USERS = perm("view_users")
PAGE_SIZE = 25

# The tabs across the top of the list, in the order people look for them.
ROLE_TABS = [
    (Role.CUSTOMER, _("Customers")), (Role.SUPPORT_AGENT, _("Support")), (Role.TECHNICAL, _("Technical")),
    (Role.MANAGER, _("Managers")), (Role.ADMIN, _("Admins")), (Role.SUPER_ADMIN, _("Super admins")),
]
OPEN_TICKET = ("open", "customer_reply", "in_progress")


def allowed_roles(actor):
    """The staff roles this person may hand out: the admin roles only a Super Admin may."""
    return {r for r in STAFF_ROLES if actor.is_superuser or r not in PRIVILEGED_ROLES}


def can_edit(actor, person):
    """Never yourself (your profile is your own page), and an admin account only by a Super Admin."""
    return person.pk != actor.pk and (actor.is_superuser or person.role not in PRIVILEGED_ROLES)


@portal_permission_required(VIEW_USERS)
def users(request, create_form=None):
    actor = request.user
    role = request.GET.get("role", "")
    role = role if role in Role.values else ""
    filter_form = forms.UserFilterForm(request.GET)
    filter_form.is_valid()
    term = (filter_form.cleaned_data.get("q") or "").strip()
    status = filter_form.cleaned_data.get("status") or ""

    rows = User.objects.prefetch_related(Prefetch("client_contacts", queryset=ClientContact.objects.select_related("client")))
    if role:
        rows = rows.filter(role=role)
    if status:
        rows = rows.filter(status=status)
    if term:
        rows = rows.filter(Q(email__icontains=term) | Q(first_name__icontains=term) | Q(last_name__icontains=term)
                           | Q(phone__icontains=term))
    page = Paginator(rows.order_by("role", "email"), PAGE_SIZE).get_page(request.GET.get("page"))
    for person in page:
        person.can_edit = can_edit(actor, person)

    counts = dict(User.objects.order_by().values_list("role").annotate(n=Count("id")))
    tabs = [("", _("All"), sum(counts.values()))] + [(r.value, label, counts.get(r.value, 0)) for r, label in ROLE_TABS]
    can_add = actor.has_perm(perm("assign_roles")) and actor.has_perm(perm("manage_users"))
    return render(request, "console/users.html", {
        "page": page, "role": role, "status": status, "term": term, "tabs": tabs, "filter_form": filter_form,
        "can_add": can_add, "form": create_form or forms.StaffUserForm(allowed_roles=allowed_roles(actor)),
        "show_add": create_form is not None})


@portal_permission_required(VIEW_USERS)
def user_detail(request, pk):
    actor = request.user
    person = get_object_or_404(User, pk=pk)
    editable = can_edit(actor, person)
    contacts = list(person.client_contacts.select_related("client"))
    clients = []
    see_billing = actor.has_perm(perm("view_billing"))
    for contact in contacts:
        client = contact.client
        clients.append({
            "client": client, "role": contact.get_role_display(),
            "orders": Order.objects.filter(client=client).count(),
            "services": HostingAccount.objects.filter(client=client).count(),
            "domains": Domain.objects.filter(client=client).count(),
            "tickets": Ticket.objects.filter(client=client, status__in=OPEN_TICKET).count(),
            "unpaid": Invoice.objects.filter(client=client, status__in=OPEN_STATUSES).count() if see_billing else None,
        })
    assigned_tickets = (Ticket.objects.filter(assigned_to=person, status__in=OPEN_TICKET).count()
                        if person.role in STAFF_ROLES else None)
    events = []
    can_see_activity = actor.has_perm(perm("view_audit_log"))
    if can_see_activity:
        events = list(AuditEvent.objects.filter(
            Q(actor=person) | Q(target_type="accounts.user", target_id=str(person.pk))).order_by("-created_at", "-id")[:12])
    can_manage = actor.has_perm(perm("manage_users"))
    return render(request, "console/user_detail.html", {
        "person": person, "clients": clients, "assigned_tickets": assigned_tickets, "events": events,
        "can_see_activity": can_see_activity,
        "can_edit_details": can_manage and editable,
        "can_set_status": can_manage and editable,
        "can_set_role": can_manage and editable and actor.has_perm(perm("assign_roles")) and person.role in STAFF_ROLES,
        "role_choices": [(v, label) for v, label in forms.STAFF_ROLE_CHOICES if v in allowed_roles(actor)],
        "details_form": forms.UserDetailsForm(initial={f: getattr(person, f) for f in ("first_name", "last_name", "phone")}),
        "status_choices": AccountStatus.choices, "can_see_clients": actor.has_perm(perm("view_clients"))})


@require_POST
@portal_permission_required(perm("manage_users"))
def user_edit(request, pk):
    person = get_object_or_404(User, pk=pk)
    form = forms.UserDetailsForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Check the details and try again.")
    else:
        try:
            account_services.update_user_details(request.user, person, request=request, **form.cleaned_data)
        except ACTION_ERRORS as exc:
            messages.error(request, error_text(exc))
        else:
            messages.success(request, "Details saved.")
    return redirect("console:user_detail", pk=pk)
