"""Customer pages for cancelling a service. Every rule lives in ``services``."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core import portal
from apps.core.web import ACTION_ERRORS, apply_form_error, run_action
from apps.domains import services as domain_services
from apps.hosting import services as hosting_services

from . import forms, services
from .models import CancellationStatus


def _service(request, kind, pk):
    if kind == "hosting":
        return get_object_or_404(hosting_services.visible_hosting_accounts_for_user(request.user), pk=pk)
    return get_object_or_404(domain_services.visible_domains_for_user(request.user), pk=pk)


def _back(service):
    name = "hosting_customer:detail" if hasattr(service, "username") else "domains_customer:detail"
    return redirect(name, pk=service.pk)


@login_required
def cancellation_list(request):
    page = Paginator(services.visible_requests_for_user(request.user), 20).get_page(request.GET.get("page"))
    return render(request, "lifecycle/customer/list.html", {"page": page})


@login_required
def cancellation_new(request, kind, pk):
    if kind not in ("hosting", "domain"):
        return redirect("lifecycle_customer:list")
    service = _service(request, kind, pk)
    existing = services.open_request_for(service)
    if existing is not None:
        messages.info(request, "There is already a cancellation request for this service.")
        return redirect("lifecycle_customer:detail", pk=existing.pk)
    is_domain = kind == "domain"
    form = forms.CancellationForm(request.POST or None, domain=is_domain)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            cr = services.request_cancellation(
                request.user, service, reason_code=data["reason_code"], reason_text=data["reason_text"],
                timing=data.get("timing", "end_of_term"), request=request)
        except ACTION_ERRORS as exc:
            apply_form_error(form, exc)
        else:
            messages.success(request, "Your cancellation request has been sent. We will review it shortly.")
            return redirect("lifecycle_customer:detail", pk=cr.pk)
    return render(request, "lifecycle/customer/new.html", {
        "form": form, "service": service, "kind": kind, "name": service.name if is_domain else service.domain,
        "timeline": services.timeline(service),
        "sidebar": (portal.domain_sidebar if is_domain else portal.service_sidebar)(request, service)})


@login_required
def cancellation_detail(request, pk):
    cr = get_object_or_404(services.visible_requests_for_user(request.user), pk=pk)
    return render(request, "lifecycle/customer/detail.html", {
        "cr": cr, "can_withdraw": cr.status in (CancellationStatus.PENDING, CancellationStatus.APPROVED)
        and services.can_request(request.user, cr.service)})


@require_POST
@login_required
def cancellation_withdraw(request, pk):
    cr = get_object_or_404(services.visible_requests_for_user(request.user), pk=pk)

    def action():
        services.withdraw(request.user, cr, request=request)
        return "Your cancellation request was withdrawn. The service continues as normal."

    return run_action(request, action, "lifecycle_customer:detail", pk=pk)
