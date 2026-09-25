"""Staff pages: the programme report, affiliates, commissions, payouts and settings."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.roles import perm
from apps.clients.models import Client
from apps.core.decorators import portal_permission_required
from apps.core.exceptions import ServiceError
from apps.core.web import ACTION_ERRORS, apply_form_error, error_text, run_action

from . import forms, services
from .models import (Affiliate, AffiliateSettings, AffiliateStatus, Commission, CommissionStatus, Payout)

VIEW = portal_permission_required(perm("view_affiliates"))
MANAGE = portal_permission_required(perm("manage_affiliates"))


def _first_error(form):
    return "; ".join(f"{form.fields[f].label or f}: {e[0]}" for f, e in form.errors.items())


@VIEW
def overview(request):
    try:
        days = int(request.GET.get("days", 0))
    except ValueError:
        days = 0
    days = days if days in (7, 30, 90) else 0
    return render(request, "affiliates/staff/overview.html", {
        "report": services.report(days=days or None), "days": days, "section": "overview",
        "awaiting": Affiliate.objects.filter(status=AffiliateStatus.PENDING).count(),
        "can_settings": request.user.has_perm(perm("manage_settings"))})


@VIEW
def affiliate_list(request):
    queryset = Affiliate.objects.select_related("user")
    status = request.GET.get("status", "")
    if status in AffiliateStatus.values:
        queryset = queryset.filter(status=status)
    term = request.GET.get("q", "").strip()
    if term:
        queryset = queryset.filter(Q(code__icontains=term) | Q(user__email__icontains=term)
                                   | Q(user__first_name__icontains=term) | Q(user__last_name__icontains=term))
    page = Paginator(queryset.order_by("-created_at"), 30).get_page(request.GET.get("page"))
    return render(request, "affiliates/staff/affiliates.html", {
        "page": page, "status": status, "q": term, "statuses": AffiliateStatus.choices, "section": "affiliates"})


@VIEW
def affiliate_detail(request, pk):
    affiliate = get_object_or_404(Affiliate.objects.select_related("user"), pk=pk)
    can = services.can_manage(request.user)
    payable = list(services.payable_commissions(affiliate).select_related("invoice"))
    config = AffiliateSettings.load()
    kind, value = services.rule_for(affiliate, config)
    context = {
        "affiliate": affiliate, "can_manage": can, "config": config, "balances": services.balances(affiliate),
        "stats": services.affiliate_stats(affiliate), "link": services.referral_url(affiliate),
        "rule": (kind, value), "commissions": affiliate.commissions.select_related("invoice")[:20],
        "payouts": affiliate.payouts.all()[:10], "payable": payable, "section": "affiliates",
        "referrals": affiliate.referrals.select_related("client")[:20]}
    if can:
        context.update({
            "note_form": forms.NoteForm(), "reject_form": forms.RequiredNoteForm(),
            "override_form": forms.OverrideForm(initial={"kind": affiliate.commission_kind,
                                                         "value": affiliate.commission_value}),
            "code_form": forms.CodeForm(initial={"code": affiliate.code}),
            "payout_form": forms.PayoutForm(payable=payable)})
    return render(request, "affiliates/staff/affiliate.html", context)


def _affiliate_action(view):
    """POST-only staff action on one affiliate, run through the service layer and flashed."""
    @require_POST
    @MANAGE
    def wrapped(request, pk):
        affiliate = get_object_or_404(Affiliate, pk=pk)
        return run_action(request, lambda: view(request, affiliate), "affiliates_staff:affiliate", pk=pk)

    wrapped.__name__ = view.__name__
    return wrapped


@_affiliate_action
def approve(request, affiliate):
    services.approve_affiliate(request.user, affiliate, note=request.POST.get("note", ""), request=request)
    return "Approved. The affiliate was told."


@_affiliate_action
def reject(request, affiliate):
    services.reject_affiliate(request.user, affiliate, note=request.POST.get("note", ""), request=request)
    return "Rejected. The applicant was told why."


@_affiliate_action
def suspend(request, affiliate):
    services.suspend_affiliate(request.user, affiliate, note=request.POST.get("note", ""), request=request)
    return "Suspended: no new commissions are earned and nothing is paid out until they are reactivated."


@_affiliate_action
def reactivate(request, affiliate):
    services.reactivate_affiliate(request.user, affiliate, note=request.POST.get("note", ""), request=request)
    return "Reactivated."


@_affiliate_action
def override(request, affiliate):
    form = forms.OverrideForm(request.POST)
    if not form.is_valid():
        raise ServiceError(_first_error(form))
    data = form.cleaned_data
    services.set_override(request.user, affiliate, kind=data["kind"], value=data["value"], request=request)
    return "Commission rule saved." if data["kind"] else "This affiliate now uses the programme's default rule."


@_affiliate_action
def change_code(request, affiliate):
    form = forms.CodeForm(request.POST)
    if not form.is_valid():
        raise ServiceError(_first_error(form))
    services.set_code(request.user, affiliate, form.cleaned_data["code"], request=request)
    return "Referral code changed. Links using the old code no longer work."


@_affiliate_action
def payout_details(request, affiliate):
    services.update_payout_details(request.user, affiliate, request.POST.get("payout_details", ""), request=request)
    return "Payout details saved."


@_affiliate_action
def record_payout(request, affiliate):
    payable = list(services.payable_commissions(affiliate).select_related("invoice"))
    form = forms.PayoutForm(request.POST, payable=payable)
    if not form.is_valid():
        raise ServiceError(_first_error(form))
    data = form.cleaned_data
    payout = services.record_payout(
        request.user, affiliate, method=data["method"], reference=data["reference"], paid_on=data["paid_on"],
        note=data["note"], commission_ids=[int(i) for i in data["commissions"]], request=request)
    return f"Payout {payout.code} of {payout.currency} {payout.amount} recorded. The affiliate was told."


@VIEW
def commission_list(request):
    queryset = Commission.objects.select_related("affiliate__user", "invoice")
    status = request.GET.get("status", "")
    if status in CommissionStatus.values:
        queryset = queryset.filter(status=status)
    if request.GET.get("review"):
        queryset = queryset.filter(needs_review=True)
    term = request.GET.get("q", "").strip()
    if term:
        queryset = queryset.filter(Q(invoice__number__icontains=term) | Q(affiliate__code__icontains=term)
                                   | Q(affiliate__user__email__icontains=term))
    page = Paginator(queryset.order_by("-created_at", "-id"), 30).get_page(request.GET.get("page"))
    return render(request, "affiliates/staff/commissions.html", {
        "page": page, "status": status, "q": term, "statuses": CommissionStatus.choices, "section": "commissions",
        "review": bool(request.GET.get("review")), "can_manage": services.can_manage(request.user),
        "now": timezone.now()})


@require_POST
@MANAGE
def commission_approve(request, pk):
    commission = get_object_or_404(Commission, pk=pk)

    def action():
        services.approve_commission(request.user, commission, request=request)
        return "Commission approved."

    return run_action(request, action, "affiliates_staff:commissions")


@require_POST
@MANAGE
def commission_reject(request, pk):
    commission = get_object_or_404(Commission, pk=pk)

    def action():
        services.reject_commission(request.user, commission, note=request.POST.get("note", ""), request=request)
        return "Commission rejected."

    return run_action(request, action, "affiliates_staff:commissions")


@VIEW
def payout_list(request):
    queryset = Payout.objects.select_related("affiliate__user")
    term = request.GET.get("q", "").strip()
    if term:
        queryset = queryset.filter(Q(affiliate__code__icontains=term) | Q(reference__icontains=term)
                                   | Q(affiliate__user__email__icontains=term))
    page = Paginator(queryset, 30).get_page(request.GET.get("page"))
    return render(request, "affiliates/staff/payouts.html", {"page": page, "q": term, "section": "payouts"})


@require_POST
@MANAGE
def attribute(request, client_pk):
    client = get_object_or_404(Client, pk=client_pk)
    form = forms.AttributeForm(request.POST)
    try:
        if not form.is_valid():
            raise ServiceError("Enter an affiliate code.")
        affiliate = Affiliate.objects.filter(code=form.cleaned_data["code"].strip().lower()).first()
        if affiliate is None:
            raise ServiceError("There is no affiliate with that code.", code="not_found")
        services.attribute_client(request.user, client, affiliate, request=request)
    except ACTION_ERRORS as exc:
        messages.error(request, error_text(exc))
    else:
        messages.success(request, f"{client.display_name} is now credited to {affiliate.code}.")
    return redirect("clients_staff:detail", pk=client.pk)


@portal_permission_required(perm("view_settings"))
def settings_page(request):
    row = AffiliateSettings.load()
    can = request.user.has_perm(perm("manage_settings"))
    form = forms.SettingsForm(request.POST or None, instance=row)
    if request.method == "POST":
        if not can:
            raise PermissionDenied
        if form.is_valid():
            try:
                services.save_settings(request.user, request=request, **form.cleaned_data)
            except ACTION_ERRORS as exc:
                apply_form_error(form, exc)
            else:
                messages.success(request, "Affiliate settings saved.")
                return redirect("affiliates_staff:settings")
    return render(request, "affiliates/staff/settings.html", {"form": form, "can_manage": can, "section": "settings"})

