"""Server-rendered renewal and upgrade pages. Every rule lives in ``services``."""
from datetime import datetime, time

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.roles import perm
from apps.core.decorators import portal_permission_required
from apps.core.exceptions import ServiceError
from apps.core.web import ACTION_ERRORS, apply_form_error, error_text, run_action
from apps.domains import services as domain_services
from apps.hosting import services as hosting_services
from apps.hosting.models import HostingAccount
from apps.products.models import Product

from . import forms, services
from .models import ChangeStatus, ServiceChange


def _own_account(request, pk):
    return get_object_or_404(hosting_services.visible_hosting_accounts_for_user(request.user), pk=pk)


def _own_domain(request, pk):
    return get_object_or_404(domain_services.visible_domains_for_user(request.user), pk=pk)


def _to_invoice(request, change, *, staff=False):
    messages.success(request, f"Invoice {change.invoice.number} is ready to pay.")
    name = "billing_staff:invoice_detail" if staff else "billing_customer:invoice_detail"
    return redirect(name, pk=change.invoice_id)


def _midnight(day):
    return timezone.make_aware(datetime.combine(day, time.min))


# --- Customer ---------------------------------------------------------------------------------------

@require_POST
@login_required
def hosting_renew(request, pk):
    account = _own_account(request, pk)
    try:
        change = services.create_hosting_renewal(request.user, account, request=request)
    except ACTION_ERRORS as exc:
        messages.error(request, error_text(exc))
        return redirect("hosting_customer:detail", pk=pk)
    return _to_invoice(request, change)


@login_required
def hosting_upgrade(request, pk):
    account = _own_account(request, pk)
    if request.method == "POST":
        form = forms.UpgradeForm(request.POST)
        try:
            if not form.is_valid():
                raise ServiceError("Choose a plan.")
            product = get_object_or_404(Product, pk=form.cleaned_data["product"])
            change = services.create_upgrade(request.user, account, product, request=request)
        except ACTION_ERRORS as exc:
            messages.error(request, error_text(exc))
            return redirect("renewals_customer:hosting_upgrade", pk=pk)
        return _to_invoice(request, change)
    error, previews = "", []
    try:
        services.require_upgradeable(account)
        previews = services.available_upgrades(account)
    except ServiceError as exc:
        error = exc.message
    return render(request, "renewals/customer/upgrade.html", {"account": account, "previews": previews,
                                                              "error": error})


@require_POST
@login_required
def domain_renew(request, pk):
    domain = _own_domain(request, pk)
    form = forms.RenewDomainForm(request.POST)
    try:
        if not form.is_valid():
            raise ServiceError("Choose a term of 1 to 10 years.")
        change = services.create_domain_renewal(request.user, domain, form.cleaned_data["years"], request=request)
    except ACTION_ERRORS as exc:
        messages.error(request, error_text(exc))
        return redirect("domains_customer:detail", pk=pk)
    return _to_invoice(request, change)


# --- Staff --------------------------------------------------------------------------------------------

@portal_permission_required(perm("view_billing"))
def staff_list(request):
    status = request.GET.get("status", "")
    queryset = ServiceChange.objects.select_related("client", "invoice", "hosting_account", "domain")
    if status in ChangeStatus.values:
        queryset = queryset.filter(status=status)
    hosting, domains = services.due_for_renewal()
    return render(request, "renewals/staff/list.html", {
        "page": Paginator(queryset, 25).get_page(request.GET.get("page")), "status": status,
        "statuses": ChangeStatus.choices, "due_hosting": hosting[:50], "due_domains": domains[:50],
        "needs_attention": ServiceChange.objects.filter(status=ChangeStatus.FAILED).count(),
        "can_manage": request.user.has_perm(perm("manage_billing")), "section": "renewals"})


@portal_permission_required(perm("manage_billing"))
def staff_hosting_term(request, pk):
    account = get_object_or_404(HostingAccount.objects.select_related("client", "product"), pk=pk)
    initial = {}
    if account.billing_cycle:
        initial = {"billing_cycle": account.billing_cycle, "custom_months": account.custom_months,
                   "term_start": timezone.localdate(account.term_start), "term_paid": account.term_paid,
                   "expires_at": timezone.localdate(account.expires_at)}
    form = forms.HostingTermForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            services.set_hosting_term(request.user, account, billing_cycle=data["billing_cycle"],
                                      custom_months=data["custom_months"] or 0,
                                      term_start=_midnight(data["term_start"]),
                                      expires_at=_midnight(data["expires_at"]), term_paid=data["term_paid"],
                                      request=request)
        except ACTION_ERRORS as exc:
            apply_form_error(form, exc)
        else:
            messages.success(request, "Billing term saved.")
            return redirect("hosting_staff:detail", pk=pk)
    return render(request, "renewals/staff/term.html", {"form": form, "account": account, "section": "renewals"})


@require_POST
@portal_permission_required(perm("view_billing"))
def staff_hosting_renew(request, pk):
    account = get_object_or_404(HostingAccount, pk=pk)
    try:
        change = services.create_hosting_renewal(request.user, account, request=request)
    except ACTION_ERRORS as exc:
        messages.error(request, error_text(exc))
        return redirect("hosting_staff:detail", pk=pk)
    return _to_invoice(request, change, staff=True)


@require_POST
@portal_permission_required(perm("view_billing"))
def staff_domain_renew(request, pk):
    domain = get_object_or_404(domain_services.visible_domains_for_user(request.user), pk=pk)
    form = forms.RenewDomainForm(request.POST)
    try:
        if not form.is_valid():
            raise ServiceError("Choose a term of 1 to 10 years.")
        change = services.create_domain_renewal(request.user, domain, form.cleaned_data["years"], request=request)
    except ACTION_ERRORS as exc:
        messages.error(request, error_text(exc))
        return redirect("domains_staff:detail", pk=pk)
    return _to_invoice(request, change, staff=True)


@require_POST
@portal_permission_required(perm("manage_billing"))
def staff_generate(request):
    def action():
        result = services.generate_renewal_invoices()
        text = f"Created {result['hosting']} hosting and {result['domains']} domain renewal invoice(s)."
        if result["errors"]:
            text += f" {len(result['errors'])} could not be created: " + "; ".join(result["errors"][:3])
        return text

    return run_action(request, action, "renewals_staff:list")


@require_POST
@portal_permission_required(perm("manage_billing"))
def staff_retry(request, pk):
    change = get_object_or_404(ServiceChange, pk=pk)

    def action():
        services.retry_change(request.user, change, request=request)
        return "Applied."

    return run_action(request, action, "billing_staff:invoice_detail", pk=change.invoice_id)


@require_POST
@portal_permission_required(perm("manage_billing"))
def staff_dismiss(request, pk):
    change = get_object_or_404(ServiceChange, pk=pk)
    form = forms.NoteForm(request.POST)

    def action():
        note = form.cleaned_data.get("note", "") if form.is_valid() else ""
        services.dismiss_change(request.user, change, note=note, request=request)
        return "Marked as handled."

    return run_action(request, action, "billing_staff:invoice_detail", pk=change.invoice_id)
