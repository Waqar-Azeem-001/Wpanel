"""Server-rendered hosting pages (customer self-service, staff). Rules live in ``services``."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.roles import perm
from apps.clients.services import single_contact_client
from apps.core import bulk, portal
from apps.core.decorators import portal_permission_required
from apps.core.exceptions import ServiceError
from apps.orders.models import ItemKind, OrderItem

from . import admin_access, care, dashboard, forms, services
from .models import HostingAccount, HostingStatus


def _apply_error(form, exc):
    if isinstance(exc, ValidationError) and hasattr(exc, "error_dict"):
        for field, errors in exc.message_dict.items():
            form.add_error(field if field in form.fields else None, errors)
    elif isinstance(exc, ValidationError):
        form.add_error(None, exc)
    else:
        form.add_error(None, exc.message)


def _run(request, action, view_name, **redirect_kwargs):
    """Run a POST action, flash the outcome, and redirect to ``view_name``. ``action`` takes no args."""
    try:
        message = action()
    except (ServiceError, ValidationError) as exc:
        messages.error(request, exc.message if isinstance(exc, ServiceError) else "; ".join(exc.messages))
    else:
        if message:
            messages.success(request, message)
    return redirect(view_name, **redirect_kwargs)


# --- Customer -------------------------------------------------------------------------------

@login_required
def my_hosting_list(request):
    accounts = HostingAccount.objects.filter(client__contacts__user=request.user).select_related(
        "product", "server").distinct()
    accounts, view_panel = portal.status_filter(request, accounts, HostingStatus.choices,
                                                url_name="hosting_customer:list", all_label="All services")
    return render(request, "hosting/customer/list.html", {
        "accounts": accounts, "cards": dashboard.website_cards(request.user, accounts), "sidebar": [view_panel]})


def _own_account(request, pk):
    """The account for a customer's screen. Staff (who can see every account) must have started Admin Access for this one."""
    account = get_object_or_404(services.visible_hosting_accounts_for_user(request.user), pk=pk)
    admin_access.require(request, account)
    return account


def _service_page(request, pk, template, **extra):
    account = _own_account(request, pk)
    return render(request, template, {"account": account, "sidebar": portal.service_sidebar(request, account), **extra})


@login_required
def my_hosting_detail(request, pk):
    account = _own_account(request, pk)
    tiles = dashboard.workspace_tiles(account)
    return render(request, "hosting/customer/detail.html", {
        "account": account, "card": dashboard.website_card(request.user, account), "tiles": tiles,
        "care_rows": care.summary(request.user, account) if account.status == "active" else [],
        "usage_url": reverse("hosting_customer:usage", args=[account.pk]) if any(t["key"] == "usage" for t in tiles) else "",
        "sidebar": portal.service_sidebar(request, account)})


@login_required
def my_hosting_section(request, pk, section):
    """One management section of a website (files, databases, email, ...). Each is built in its own slice; until then
    the page says what it will do and whether the account's server supports it."""
    account = _own_account(request, pk)
    if section not in dashboard.SECTION_BY_KEY:
        raise Http404
    return render(request, "hosting/customer/section.html", {
        "account": account, "section": dashboard.section_page(request.user, account, section),
        "sidebar": portal.service_sidebar(request, account)})


@login_required
def my_hosting_information(request, pk):
    return _service_page(request, pk, "hosting/customer/information.html", can_change_password=_can_change_password(request, pk))


def _can_change_password(request, pk):
    account = _own_account(request, pk)
    return account.server is not None and account.status in (HostingStatus.ACTIVE, HostingStatus.SUSPENDED)


@require_POST
@login_required
def my_hosting_password(request, pk):
    """Give the account a new control-panel password and show it once, on this page only (never stored, emailed or put in a web address)."""
    account = _own_account(request, pk)
    chosen = request.POST.get("mode") == "own"
    try:
        password = services.change_account_password(request.user, account, request.POST.get("password", "") if chosen else None, request=request)
    except ServiceError as exc:
        messages.error(request, exc.message)
        return redirect("hosting_customer:information", pk=account.pk)
    response = render(request, "hosting/customer/information.html", {
        "account": account, "sidebar": portal.service_sidebar(request, account), "new_password": password, "chosen": chosen,
        "can_change_password": True})
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


@login_required
def my_hosting_addons(request, pk):
    account = _own_account(request, pk)
    addon_lines = OrderItem.objects.filter(parent__hosting_account=account, kind=ItemKind.ADDON).select_related(
        "order", "addon").order_by("-created_at", "-id")
    return render(request, "hosting/customer/addons.html", {
        "account": account, "addon_lines": addon_lines, "sidebar": portal.service_sidebar(request, account)})


# --- Staff -------------------------------------------------------------------------------

@portal_permission_required(perm("view_hosting"))
def staff_hosting_list(request):
    filter_form = forms.HostingFilterForm(request.GET or None)
    queryset = HostingAccount.objects.select_related("client", "product", "server")
    if filter_form.is_valid():
        queryset = services.search_hosting_accounts(queryset, filter_form.cleaned_data["q"])
        if filter_form.cleaned_data["status"]:
            queryset = queryset.filter(status=filter_form.cleaned_data["status"])
    page = Paginator(queryset.order_by("-created_at"), 25).get_page(request.GET.get("page"))
    return render(request, "hosting/staff/list.html", {"filter_form": filter_form, "page": page,
                                                        "can_manage": request.user.has_perm(perm("manage_hosting"))})


@require_POST
@portal_permission_required(perm("manage_hosting"))
def staff_hosting_bulk(request):
    """"With selected": terminate every ticked account that is active or suspended (the same rule, and the same
    real call to the server, as one at a time). There is no bulk delete here: a real hosting account is almost
    always protected from being removed from the database by the order it came from, and its row is how the system
    remembers a service exists at all until it is properly terminated - "terminate" is the meaningful bulk action."""
    do = request.POST.get("do")
    if do == "terminate":
        bulk.run(request, HostingAccount.objects.all(), bulk.selected_ids(request),
                 lambda a: services.terminate_account(request.user, a, request=request),
                 verb="account(s) terminated", label=lambda a: a.domain)
    elif do == "delete":  # only the records of finished accounts (failed, cancelled, terminated); nothing is sent to a server
        bulk.run(request, HostingAccount.objects.all(), bulk.selected_ids(request),
                 lambda a: services.delete_dead_record(request.user, a, request=request),
                 verb="record(s) deleted", label=lambda a: a.domain)
    else:
        messages.error(request, "Choose what to do with the selected accounts.")
    return bulk.back_to(request, "hosting_staff:list")


@require_POST
@portal_permission_required(perm("manage_hosting"))
def staff_hosting_delete_record(request, pk):
    account = get_object_or_404(HostingAccount, pk=pk)
    domain = account.domain
    try:
        services.delete_dead_record(request.user, account, request=request)
    except ServiceError as exc:
        messages.error(request, exc.message)
        return redirect("hosting_staff:detail", pk=pk)
    messages.success(request, f"The record of {domain} was deleted. Nothing was changed on the server.")
    return redirect("hosting_staff:list")


@portal_permission_required(perm("view_hosting"))
def staff_infrastructure(request):
    from . import infra_dashboard

    return render(request, "hosting/staff/infrastructure.html", infra_dashboard.dashboard())


@portal_permission_required(perm("view_hosting"))
def staff_hosting_detail(request, pk):
    account = get_object_or_404(HostingAccount.objects.select_related("client", "product", "server"), pk=pk)
    can_manage = request.user.has_perm(perm("manage_hosting"))
    return render(request, "hosting/staff/detail.html", {
        "account": account,
        "can_manage": can_manage,
        "can_delete_record": can_manage and account.status in services.DEAD_STATUSES,
        "can_admin": account.server is not None and account.status in (HostingStatus.ACTIVE, HostingStatus.SUSPENDED),
        "admin_minutes": admin_access.minutes_left(request, account),
        "admin_minutes_total": admin_access.MINUTES,
        "assign_server_form": forms.AssignServerForm(product=account.product) if account.server is None else None,
        "cancel_form": forms.CancelHostingForm(),
        "suspend_form": forms.SuspendHostingForm(),
        "terminate_form": forms.TerminateHostingForm(),
        "change_package_form": forms.ChangePackageForm(),
    })


@require_POST
@portal_permission_required(perm("view_hosting"))
def staff_hosting_admin_access_start(request, pk):
    account = get_object_or_404(HostingAccount.objects.select_related("client", "server"), pk=pk)
    if account.server is None or account.status not in (HostingStatus.ACTIVE, HostingStatus.SUSPENDED):
        messages.error(request, "This account has no live hosting to manage yet.")
        return redirect("hosting_staff:detail", pk=account.pk)
    try:
        admin_access.start(request, account, request.POST.get("reason", ""))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("hosting_staff:detail", pk=account.pk)
    return redirect("hosting_customer:detail", pk=account.pk)


@require_POST
@portal_permission_required(perm("view_hosting"))
def staff_hosting_admin_access_end(request, pk):
    account = get_object_or_404(HostingAccount.objects.select_related("client"), pk=pk)
    admin_access.end(request, account)
    messages.success(request, "Admin Access ended.")
    return redirect("hosting_staff:detail", pk=account.pk)


@require_POST
@portal_permission_required(perm("manage_hosting"))
def staff_hosting_assign_server(request, pk):
    account = get_object_or_404(HostingAccount, pk=pk)
    form = forms.AssignServerForm(request.POST, product=account.product)

    def action():
        if not form.is_valid():
            raise ServiceError("Choose a valid server.")
        services.assign_server(request.user, account, form.cleaned_data["server"], request=request)
        return "Server assigned."

    return _run(request, action, "hosting_staff:detail", pk=pk)


@require_POST
@portal_permission_required(perm("manage_hosting"))
def staff_hosting_complete(request, pk):
    account = get_object_or_404(HostingAccount, pk=pk)

    def action():
        services.complete_provisioning(request.user, account, request=request)
        return "Provisioned."

    return _run(request, action, "hosting_staff:detail", pk=pk)


@require_POST
@portal_permission_required(perm("manage_hosting"))
def staff_hosting_cancel(request, pk):
    account = get_object_or_404(HostingAccount, pk=pk)
    form = forms.CancelHostingForm(request.POST)

    def action():
        reason = form.cleaned_data.get("reason", "") if form.is_valid() else ""
        services.cancel_request(request.user, account, reason=reason, request=request)
        return "Request cancelled."

    return _run(request, action, "hosting_staff:detail", pk=pk)


@require_POST
@portal_permission_required(perm("manage_hosting"))
def staff_hosting_suspend(request, pk):
    account = get_object_or_404(HostingAccount, pk=pk)
    form = forms.SuspendHostingForm(request.POST)

    def action():
        reason = form.cleaned_data.get("reason", "") if form.is_valid() else ""
        services.suspend_account(request.user, account, reason=reason, request=request)
        return "Account suspended."

    return _run(request, action, "hosting_staff:detail", pk=pk)


@require_POST
@portal_permission_required(perm("manage_hosting"))
def staff_hosting_unsuspend(request, pk):
    account = get_object_or_404(HostingAccount, pk=pk)

    def action():
        services.unsuspend_account(request.user, account, request=request)
        return "Account unsuspended."

    return _run(request, action, "hosting_staff:detail", pk=pk)


@require_POST
@portal_permission_required(perm("manage_hosting"))
def staff_hosting_terminate(request, pk):
    account = get_object_or_404(HostingAccount, pk=pk)
    form = forms.TerminateHostingForm(request.POST)

    def action():
        keep_dns = form.cleaned_data.get("keep_dns", False) if form.is_valid() else False
        services.terminate_account(request.user, account, keep_dns=keep_dns, request=request)
        return "Account terminated."

    return _run(request, action, "hosting_staff:detail", pk=pk)


@require_POST
@portal_permission_required(perm("manage_hosting"))
def staff_hosting_change_package(request, pk):
    account = get_object_or_404(HostingAccount, pk=pk)
    form = forms.ChangePackageForm(request.POST)

    def action():
        if not form.is_valid():
            raise ServiceError("Choose a valid product.")
        services.change_package(request.user, account, form.cleaned_data["product"], request=request)
        return "Package changed."

    return _run(request, action, "hosting_staff:detail", pk=pk)


@require_POST
@portal_permission_required(perm("manage_hosting"))
def staff_hosting_sync_status(request, pk):
    account = get_object_or_404(HostingAccount, pk=pk)

    def action():
        services.sync_status(request.user, account, request=request)
        return "Status synced."

    return _run(request, action, "hosting_staff:detail", pk=pk)


@require_POST
@portal_permission_required(perm("manage_hosting"))
def staff_hosting_sync_usage(request, pk):
    account = get_object_or_404(HostingAccount, pk=pk)

    def action():
        services.sync_usage(request.user, account, request=request)
        return "Usage synced."

    return _run(request, action, "hosting_staff:detail", pk=pk)


@require_POST
@portal_permission_required(perm("manage_hosting"))
def staff_hosting_backup(request, pk):
    account = get_object_or_404(HostingAccount, pk=pk)

    def action():
        services.backup_account(request.user, account, request=request)
        return "Backup requested from the server."

    return _run(request, action, "hosting_staff:detail", pk=pk)
