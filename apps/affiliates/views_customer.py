"""The affiliate's own pages. Every rule lives in ``services``; an affiliate sees only their own numbers."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.core.exceptions import ServiceError
from apps.core.web import ACTION_ERRORS, apply_form_error, run_action

from . import forms, services
from .models import AffiliateSettings, AffiliateStatus


@login_required
def dashboard(request):
    affiliate = services.get_affiliate(request.user)
    config = AffiliateSettings.load()
    if affiliate is None:
        form = forms.JoinForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                services.enrol(request.user, accept_terms=form.cleaned_data["accept_terms"],
                               payout_details=form.cleaned_data["payout_details"], request=request)
            except ACTION_ERRORS as exc:
                apply_form_error(form, exc)
            else:
                messages.success(request, "Thanks for applying. We will let you know as soon as you are approved."
                                 if config.require_approval else "Welcome to the affiliate programme!")
                return redirect("affiliates_customer:dashboard")
        return render(request, "affiliates/customer/join.html", {"form": form, "config": config})
    context = {"affiliate": affiliate, "config": config, "active": affiliate.status == AffiliateStatus.ACTIVE,
               "balances": services.balances(affiliate), "stats": services.affiliate_stats(affiliate),
               "link": services.referral_url(affiliate),
               "details_form": forms.PayoutDetailsForm(initial={"payout_details": affiliate.payout_details}),
               "recent_commissions": affiliate.commissions.select_related("referral")[:10],
               "recent_payouts": affiliate.payouts.all()[:5]}
    return render(request, "affiliates/customer/dashboard.html", context)


@require_POST
@login_required
def update_details(request):
    affiliate = services.get_affiliate(request.user)
    form = forms.PayoutDetailsForm(request.POST)

    def action():
        if affiliate is None:
            raise ServiceError("Join the programme first.", code="not_affiliate")
        if not form.is_valid():
            raise ServiceError("Payout details are too long.", code="invalid")
        services.update_payout_details(request.user, affiliate, form.cleaned_data["payout_details"], request=request)
        return "Payout details saved."

    return run_action(request, action, "affiliates_customer:dashboard")


@login_required
def commission_list(request):
    affiliate = services.get_affiliate(request.user)
    if affiliate is None:
        return redirect("affiliates_customer:dashboard")
    queryset = affiliate.commissions.select_related("referral")
    status = request.GET.get("status", "")
    if status in ("pending", "approved", "paid", "rejected"):
        queryset = queryset.filter(status=status)
    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    return render(request, "affiliates/customer/commissions.html", {"affiliate": affiliate, "page": page,
                                                                    "status": status})


@login_required
def payout_list(request):
    affiliate = services.get_affiliate(request.user)
    if affiliate is None:
        return redirect("affiliates_customer:dashboard")
    page = Paginator(affiliate.payouts.all(), 25).get_page(request.GET.get("page"))
    return render(request, "affiliates/customer/payouts.html", {"affiliate": affiliate, "page": page})
