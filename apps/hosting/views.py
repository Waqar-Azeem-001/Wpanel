"""Server-rendered hosting pages (customer self-service, staff). Rules live in ``services``."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.roles import perm
from apps.clients.services import single_contact_client
from apps.core import portal
from apps.core.decorators import portal_permission_required
from apps.core.exceptions import ServiceError
from apps.orders.models import ItemKind, OrderItem

from . import forms, services
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
    return render(request, "hosting/customer/list.html", {"accounts": accounts, "sidebar": [view_panel]})


def _own_account(request, pk):
    return get_object_or_404(services.visible_hosting_accounts_for_user(request.user), pk=pk)


def _service_page(request, pk, template, **extra):
    account = _own_account(request, pk)
    return render(request, template, {"account": account, "sidebar": portal.service_sidebar(request, account), **extra})


@login_required
def my_hosting_detail(request, pk):
    return _service_page(request, pk, "hosting/customer/detail.html")


@login_required
def my_hosting_information(request, pk):
    return _service_page(request, pk, "hosting/customer/information.html")


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
    return render(request, "hosting/staff/list.html", {"filter_form": filter_form, "page": page})


@portal_permission_required(perm("view_hosting"))
def staff_hosting_detail(request, pk):
    account = get_object_or_404(HostingAccount.objects.select_related("client", "product", "server"), pk=pk)
    can_manage = request.user.has_perm(perm("manage_hosting"))
    return render(request, "hosting/staff/detail.html", {
        "account": account,
        "can_manage": can_manage,
        "assign_server_form": forms.AssignServerForm(product=account.product) if account.server is None else None,
        "cancel_form": forms.CancelHostingForm(),
        "suspend_form": forms.SuspendHostingForm(),
        "terminate_form": forms.TerminateHostingForm(),
        "change_package_form": forms.ChangePackageForm(),
    })


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
