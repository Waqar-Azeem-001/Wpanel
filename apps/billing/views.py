"""Staff pages for billing configuration. Rules live in ``services``."""
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.roles import perm
from apps.core.decorators import portal_permission_required
from apps.core.exceptions import ServiceError
from apps.core.web import run_action

from . import forms, services
from .models import Coupon, PaymentMethod, TaxRule


def _context(request, **extra):
    return {"can_manage": request.user.has_perm(perm("manage_billing")), **extra}


@portal_permission_required(perm("view_billing"))
def index(request):
    return redirect("billing_staff:payment_methods")


# --- Payment methods ------------------------------------------------------------------------

@portal_permission_required(perm("view_billing"))
def payment_methods(request):
    context = _context(request, methods=PaymentMethod.objects.all(), section="payment_methods")
    context["form"] = forms.PaymentMethodForm() if context["can_manage"] else None
    return render(request, "billing/staff/payment_methods.html", context)


@require_POST
@portal_permission_required(perm("manage_billing"))
def payment_method_save(request):
    form = forms.PaymentMethodForm(request.POST)

    def action():
        if not form.is_valid():
            raise ServiceError("Enter a valid payment method.")
        data = dict(form.cleaned_data)
        code = data.pop("code")
        services.save_payment_method(request.user, code, request=request, **data)
        return f"Saved '{code}'."

    return run_action(request, action, "billing_staff:payment_methods")


@require_POST
@portal_permission_required(perm("manage_billing"))
def payment_method_status(request, pk):
    method = get_object_or_404(PaymentMethod, pk=pk)
    enabled = request.POST.get("is_active") == "on"

    def action():
        services.set_payment_method_active(request.user, method, enabled, request=request)
        return "Updated."

    return run_action(request, action, "billing_staff:payment_methods")


# --- Tax rules ------------------------------------------------------------------------------

@portal_permission_required(perm("view_billing"))
def tax_rules(request):
    context = _context(request, rules=TaxRule.objects.all(), section="tax_rules")
    context["form"] = forms.TaxRuleForm() if context["can_manage"] else None
    return render(request, "billing/staff/tax_rules.html", context)


@require_POST
@portal_permission_required(perm("manage_billing"))
def tax_rule_save(request):
    form = forms.TaxRuleForm(request.POST)

    def action():
        if not form.is_valid():
            raise ServiceError("Enter a valid tax rule.")
        data = form.cleaned_data
        services.save_tax_rule(request.user, data["country"], name=data["name"], rate=data["rate"],
                               request=request)
        return "Tax rule saved."

    return run_action(request, action, "billing_staff:tax_rules")


@require_POST
@portal_permission_required(perm("manage_billing"))
def tax_rule_status(request, pk):
    rule = get_object_or_404(TaxRule, pk=pk)
    enabled = request.POST.get("is_active") == "on"

    def action():
        services.set_tax_rule_active(request.user, rule, enabled, request=request)
        return "Updated."

    return run_action(request, action, "billing_staff:tax_rules")


# --- Coupons --------------------------------------------------------------------------------

@portal_permission_required(perm("view_billing"))
def coupons(request):
    context = _context(request, coupons=Coupon.objects.all(), section="coupons")
    context["form"] = forms.CouponForm() if context["can_manage"] else None
    return render(request, "billing/staff/coupons.html", context)


@require_POST
@portal_permission_required(perm("manage_billing"))
def coupon_save(request):
    form = forms.CouponForm(request.POST)

    def action():
        if not form.is_valid():
            raise ServiceError("Enter a valid coupon.")
        data = dict(form.cleaned_data)
        code = data.pop("code")
        services.save_coupon(request.user, code, data, request=request)
        return f"Coupon {code.upper()} saved."

    return run_action(request, action, "billing_staff:coupons")


@require_POST
@portal_permission_required(perm("manage_billing"))
def coupon_status(request, pk):
    coupon = get_object_or_404(Coupon, pk=pk)
    enabled = request.POST.get("is_active") == "on"

    def action():
        services.set_coupon_active(request.user, coupon, enabled, request=request)
        return "Updated."

    return run_action(request, action, "billing_staff:coupons")
