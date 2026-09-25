"""Staff pages: the lifecycle overview and its timings, and the cancellation queue."""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.roles import perm
from apps.core.decorators import portal_permission_required
from apps.billing.models import Transaction
from apps.core.exceptions import ServiceError
from apps.core.web import ACTION_ERRORS, apply_form_error, error_text, run_action
from apps.domains.models import Domain
from apps.hosting.models import HostingAccount

from . import forms, services
from .models import CancellationReason, CancellationStatus, LifecycleSettings, Stage


def reviewer_required(view):
    """Staff who can see hosting or domains may open these pages; anonymous users go to login, others get 403."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not services.is_staff_reviewer(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapped


@reviewer_required
def overview(request):
    data = services.overview()
    data.update({"section": "overview", "stages": Stage, "now": timezone.now(),
                 "can_settings": request.user.has_perm(perm("manage_settings"))})
    return render(request, "lifecycle/staff/overview.html", data)


@reviewer_required
def cancellation_list(request):
    queryset = services.visible_requests_for_user(request.user)
    status = request.GET.get("status", "open")
    if status == "open":
        queryset = queryset.filter(status__in=(CancellationStatus.PENDING, CancellationStatus.APPROVED))
    elif status in CancellationStatus.values:
        queryset = queryset.filter(status=status)
    kind = request.GET.get("kind", "")
    if kind == "hosting":
        queryset = queryset.filter(hosting_account__isnull=False)
    elif kind == "domain":
        queryset = queryset.filter(domain__isnull=False)
    term = request.GET.get("q", "").strip()
    if term:
        queryset = queryset.filter(Q(hosting_account__domain__icontains=term) | Q(domain__name__icontains=term)
                                   | Q(client__email__icontains=term) | Q(client__company_name__icontains=term))
    page = Paginator(queryset.order_by("-created_at", "-id"), 30).get_page(request.GET.get("page"))
    return render(request, "lifecycle/staff/list.html", {
        "page": page, "status": status, "kind": kind, "q": term, "statuses": CancellationStatus.choices,
        "section": "cancellations"})


def _refund_payments(cr):
    return services.refundable_payments(cr.service)


@reviewer_required
def cancellation_detail(request, pk):
    cr = get_object_or_404(services.visible_requests_for_user(request.user), pk=pk)
    service = cr.service
    can_act = services.can_manage(request.user, service)
    context = {"cr": cr, "section": "cancellations", "can_act": can_act,
               "timeline": services.timeline(service) if cr.kind == "hosting" else None,
               "stage": services.stage_of(service), "suggested": services.suggested_refund(service)}
    if can_act and cr.status == CancellationStatus.PENDING:
        payments = _refund_payments(cr) if request.user.has_perm(perm("manage_billing")) else []
        context["approve_form"] = forms.ApproveForm(initial={"timing": cr.timing}, payments=payments,
                                                    domain=cr.kind == "domain")
        context["reject_form"] = forms.RejectForm()
        context["refundable"] = payments
    return render(request, "lifecycle/staff/detail.html", context)


def _refund_payment(payment_id):
    if not payment_id:
        return None
    return Transaction.objects.filter(pk=payment_id).first()


@require_POST
@reviewer_required
def cancellation_approve(request, pk):
    cr = get_object_or_404(services.visible_requests_for_user(request.user), pk=pk)
    payments = _refund_payments(cr) if request.user.has_perm(perm("manage_billing")) else []
    form = forms.ApproveForm(request.POST, payments=payments, domain=cr.kind == "domain")

    def action():
        if not form.is_valid():
            raise ServiceError("Check the form: " + "; ".join(f"{form.fields[f].label}: {e[0]}"
                                                               for f, e in form.errors.items()))
        data = form.cleaned_data
        result = services.approve(
            request.user, cr, timing=data["timing"], refund_amount=data.get("refund_amount") or 0,
            refund_payment=_refund_payment(data.get("refund_payment")), note=data["note"], request=request)
        if result.status == CancellationStatus.COMPLETED:
            return "Approved and carried out." + (f" Refund of {result.refund_amount} recorded."
                                                   if result.refund_transaction_id else "")
        return f"Approved. The service ends on {timezone.localtime(result.effective_at):%d %b %Y}."

    return run_action(request, action, "lifecycle_staff:cancellation", pk=pk)


@require_POST
@reviewer_required
def cancellation_reject(request, pk):
    cr = get_object_or_404(services.visible_requests_for_user(request.user), pk=pk)
    form = forms.RejectForm(request.POST)

    def action():
        if not form.is_valid():
            raise ServiceError("Tell the customer why the request was declined.", code="note_required")
        services.reject(request.user, cr, note=form.cleaned_data["note"], request=request)
        return "Request declined. The customer was told why."

    return run_action(request, action, "lifecycle_staff:cancellation", pk=pk)


@require_POST
@reviewer_required
def cancellation_retry(request, pk):
    cr = get_object_or_404(services.visible_requests_for_user(request.user), pk=pk)

    def action():
        services.retry(request.user, cr, request=request)
        return "Carried out."

    return run_action(request, action, "lifecycle_staff:cancellation", pk=pk)


@require_POST
@reviewer_required
def cancellation_withdraw(request, pk):
    cr = get_object_or_404(services.visible_requests_for_user(request.user), pk=pk)

    def action():
        services.withdraw(request.user, cr, request=request)
        return "The request was withdrawn."

    return run_action(request, action, "lifecycle_staff:cancellation", pk=pk)


@reviewer_required
def cancel_service(request, kind, pk):
    """Staff: end a service on the customer's behalf (records the request, approves it and carries it out)."""
    if kind == "hosting":
        service = get_object_or_404(HostingAccount.objects.select_related("client"), pk=pk)
    elif kind == "domain":
        service = get_object_or_404(Domain.objects.select_related("client"), pk=pk)
    else:
        return redirect("lifecycle_staff:overview")
    if not services.can_manage(request.user, service):
        raise PermissionDenied
    is_domain = kind == "domain"
    payments = services.refundable_payments(service) if request.user.has_perm(perm("manage_billing")) else []
    form = forms.StaffCancelForm(request.POST or None, payments=payments, domain=is_domain)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        timing = data.get("timing", "end_of_term")
        try:
            cr = services.request_cancellation(request.user, service, reason_code=data["reason_code"],
                                               reason_text=data["reason_text"], timing=timing, request=request)
        except ACTION_ERRORS as exc:
            apply_form_error(form, exc)
        else:
            try:
                result = services.approve(request.user, cr, timing=timing, refund_amount=data.get("refund_amount") or 0,
                                          refund_payment=_refund_payment(data.get("refund_payment")),
                                          note=data["note"], request=request)
            except ACTION_ERRORS as exc:  # the request exists; the reviewer can finish it from its page
                messages.error(request, error_text(exc) + " The request is saved; you can finish it from here.")
            else:
                messages.success(request, "The service was cancelled." if result.status == CancellationStatus.COMPLETED
                                 else f"Cancellation scheduled for {timezone.localtime(result.effective_at):%d %b %Y}.")
            return redirect("lifecycle_staff:cancellation", pk=cr.pk)
    return render(request, "lifecycle/staff/cancel_service.html", {
        "form": form, "service": service, "kind": kind, "name": service.name if is_domain else service.domain,
        "suggested": services.suggested_refund(service), "timeline": services.timeline(service),
        "section": "cancellations", "reasons": CancellationReason})


@portal_permission_required(perm("view_settings"))
def settings_page(request):
    row = LifecycleSettings.load()
    can_manage = request.user.has_perm(perm("manage_settings"))
    form = forms.SettingsForm(request.POST or None, instance=row)
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied
        if form.is_valid():
            try:
                services.save_settings(request.user, request=request, **form.cleaned_data)
            except ACTION_ERRORS as exc:
                apply_form_error(form, exc)
            else:
                messages.success(request, "Lifecycle timings saved.")
                return redirect("lifecycle_staff:settings")
    return render(request, "lifecycle/staff/settings.html", {"form": form, "can_manage": can_manage,
                                                             "section": "settings"})
