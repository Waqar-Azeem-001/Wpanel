"""Server-rendered client pages (staff and customer). Rules live in ``services``."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.roles import perm
from apps.core.decorators import portal_permission_required
from apps.core.exceptions import ServiceError

from . import forms, records, services
from . import tabs as client_tabs
from .models import Client, ClientContact


def _apply_error(form, exc):
    if isinstance(exc, ValidationError) and hasattr(exc, "error_dict"):
        for field, errors in exc.message_dict.items():
            form.add_error(field if field in form.fields else None, errors)
    elif isinstance(exc, ValidationError):
        form.add_error(None, exc)
    else:
        form.add_error(None, exc.message)


def _initial(client, fields):
    return {f: getattr(client, f) for f in fields}


# --- Staff -------------------------------------------------------------------------

@portal_permission_required(perm("view_clients"))
def staff_client_list(request):
    filter_form = forms.ClientFilterForm(request.GET or None)
    queryset = Client.objects.all()
    if filter_form.is_valid():
        queryset = services.search_clients(queryset, filter_form.cleaned_data["q"])
        if filter_form.cleaned_data["status"]:
            queryset = queryset.filter(status=filter_form.cleaned_data["status"])
    page = Paginator(queryset.order_by("-created_at", "-id"), 25).get_page(request.GET.get("page"))
    context = {"filter_form": filter_form, "page": page, "query": request.GET.copy()}
    context["query"].pop("page", None)
    template = "clients/staff/_table.html" if request.headers.get("HX-Request") else "clients/staff/list.html"
    return render(request, template, context)


@portal_permission_required(perm("manage_clients"))
def staff_client_create(request):
    form = forms.NewClientForm(request.POST or None, initial={"currency": "USD"})
    if request.method == "POST" and form.is_valid():
        data = dict(form.cleaned_data)
        owner_email = data.pop("owner_email") or None
        try:
            client = services.create_client(request.user, data, owner_email=owner_email, request=request)
        except (ServiceError, ValidationError) as exc:
            _apply_error(form, exc)
        else:
            messages.success(request, f"Client {client.reference} created.")
            return redirect("clients_staff:detail", pk=client.pk)
    return render(request, "clients/staff/form.html", {"form": form, "title": "Add client"})


@portal_permission_required(perm("view_clients"))
def staff_client_detail(request, pk):
    client = get_object_or_404(Client, pk=pk)
    contacts = client.contacts.select_related("user").order_by("role", "id")
    activity = services.client_activity(client)[:25]
    return render(request, "clients/staff/detail.html", {
        "client": client, "contacts": contacts, "activity": activity[:5], "tab": "summary",
        "records": records.account_records(request.user, client),
        "status_form": forms.ClientStatusForm(initial={"status": client.status}),
        "can_manage": request.user.has_perm(perm("manage_clients")),
        "can_bill": request.user.has_perm(perm("manage_billing")),
    })


@portal_permission_required(perm("view_clients"))
def staff_client_tab(request, pk, tab):
    """One list tab of the profile (invoices, tickets, log...): 25 rows a page, only if the person may open it."""
    client = get_object_or_404(Client, pk=pk)
    found = client_tabs.BY_KEY.get(tab)
    if found is None or found.url_name != "clients_staff:tab":
        raise Http404
    if not client_tabs.allowed(request.user, found):
        raise PermissionDenied
    context = {"client": client, "tab": tab, "found": found, "can_manage": request.user.has_perm(perm("manage_clients")),
               "can_bill": request.user.has_perm(perm("manage_billing"))}
    if tab == "notes":
        return render(request, "clients/staff/tab_notes.html", context)
    if tab == "affiliate":
        from apps.affiliates.models import Referral

        context["referral"] = Referral.objects.select_related("affiliate").filter(client=client).first()
    return render(request, "clients/staff/tab.html", {**context, **client_tabs.table(request.user, client, tab, request.GET.get("page"))})


@portal_permission_required(perm("manage_clients"))
def staff_client_edit(request, pk):
    client = get_object_or_404(Client, pk=pk)
    form = forms.StaffClientForm(request.POST or None, initial=_initial(client, services.STAFF_FIELDS))
    if request.method == "POST" and form.is_valid():
        try:
            services.update_client(request.user, client, form.cleaned_data, request=request)
        except (ServiceError, ValidationError) as exc:
            _apply_error(form, exc)
        else:
            messages.success(request, "Client updated.")
            return redirect("clients_staff:detail", pk=client.pk)
    return render(request, "clients/staff/form.html", {"form": form, "client": client, "tab": "profile",
                                                        "title": f"Edit {client.display_name}"})


def _post_action(request, pk, action, back="clients_staff:detail"):
    """Run a staff POST action, flash the outcome, and return to the profile (or the tab it came from)."""
    client = get_object_or_404(Client, pk=pk)
    try:
        message = action(client)
    except (ServiceError, ValidationError) as exc:
        messages.error(request, exc.message if isinstance(exc, ServiceError) else "; ".join(exc.messages))
    else:
        messages.success(request, message)
    return redirect(back, pk=client.pk)


@require_POST
@portal_permission_required(perm("manage_clients"))
def staff_client_status(request, pk):
    form = forms.ClientStatusForm(request.POST)

    def action(client):
        if not form.is_valid():
            raise ServiceError("Choose a valid status.")
        services.set_client_status(request.user, client, form.cleaned_data["status"],
                                   reason=form.cleaned_data["reason"], request=request)
        return f"Status set to {client.get_status_display()}."

    return _post_action(request, pk, action)


@portal_permission_required(perm("view_clients"))
def staff_contact_add(request, pk):
    """GET: the Contacts tab (people on the account, and a form to add one). POST: add a contact."""
    if request.method != "POST":
        client = get_object_or_404(Client, pk=pk)
        return render(request, "clients/staff/tab_contacts.html", {
            "client": client, "tab": "contacts", "contacts": client.contacts.select_related("user").order_by("role", "id"),
            "contact_form": forms.AddContactForm(), "role_choices": forms.ContactRole.choices,
            "can_manage": request.user.has_perm(perm("manage_clients"))})
    if not request.user.has_perm(perm("manage_clients")):
        raise PermissionDenied
    form = forms.AddContactForm(request.POST)

    def action(client):
        if not form.is_valid():
            raise ServiceError("Enter a valid email and role.")
        contact = services.add_contact(request.user, client, request=request, **form.cleaned_data)
        return f"{contact.user.email} added as {contact.get_role_display().lower()} contact."

    return _post_action(request, pk, action, back="clients_staff:contact_add")


@require_POST
@portal_permission_required(perm("manage_clients"))
def staff_contact_role(request, pk, contact_id):
    form = forms.ContactRoleForm(request.POST)

    def action(client):
        contact = get_object_or_404(ClientContact, pk=contact_id, client=client)
        if not form.is_valid():
            raise ServiceError("Choose a valid role.")
        services.change_contact_role(request.user, contact, form.cleaned_data["role"], request=request)
        return f"{contact.user.email} is now a {form.cleaned_data['role']} contact."

    return _post_action(request, pk, action, back="clients_staff:contact_add")


@require_POST
@portal_permission_required(perm("manage_clients"))
def staff_contact_remove(request, pk, contact_id):
    def action(client):
        contact = get_object_or_404(ClientContact, pk=contact_id, client=client)
        email = contact.user.email
        services.remove_contact(request.user, contact, request=request)
        return f"{email} removed."

    return _post_action(request, pk, action, back="clients_staff:contact_add")


# --- Customer ---------------------------------------------------------------------------

def _own_client(request, pk):
    client = Client.objects.filter(pk=pk, contacts__user=request.user).first()
    if client is None:
        raise Http404
    return client


@login_required
def my_clients(request):
    clients = Client.objects.filter(contacts__user=request.user).order_by("created_at")
    if len(clients) == 1:
        return redirect("clients_customer:detail", pk=clients[0].pk)
    return render(request, "clients/customer/list.html", {"clients": clients})


@login_required
def my_client_detail(request, pk):
    client = _own_client(request, pk)
    role = services.contact_role(request.user, client)
    can_edit = role in services.CUSTOMER_EDIT_ROLES and client.status != "closed"
    form = forms.CustomerClientForm(request.POST or None, initial=_initial(client, services.CUSTOMER_FIELDS))
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied
        if form.is_valid():
            try:
                services.update_client(request.user, client, form.cleaned_data, request=request)
            except (ServiceError, ValidationError) as exc:
                _apply_error(form, exc)
            else:
                messages.success(request, "Account details updated.")
                return redirect("clients_customer:detail", pk=client.pk)
    return render(request, "clients/customer/detail.html",
                  {"client": client, "form": form, "can_edit": can_edit, "my_role": role})


@login_required
def my_contacts(request):
    """The people on each of the customer's accounts, with their roles (read-only: staff manage contacts)."""
    clients = Client.objects.filter(contacts__user=request.user).distinct().order_by("created_at")
    accounts = [(client, client.contacts.select_related("user").order_by("role", "id")) for client in clients]
    return render(request, "clients/customer/contacts.html", {"accounts": accounts})
