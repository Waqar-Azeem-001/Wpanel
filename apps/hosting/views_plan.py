"""
One hosting plan's Websites: how many the plan allows and how many exist, each website with its actions, and a form to add another (a new domain on the plan, then the usual set-up).
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core import portal
from apps.core.exceptions import ServiceError

from . import plan_sites
from .views import _own_account


@login_required
def plan(request, pk):
    account = _own_account(request, pk)
    state = plan_sites.summary(request.user, account)
    for site in state["sites"]:
        domain = "" if site["primary"] else site["domain"]
        if site["primary"]:
            site["manage_url"] = reverse("hosting_customer:detail", args=[account.pk])
            site["setup_url"] = reverse("hosting_customer:setup", args=[account.pk])
            site["delete_url"] = reverse("hosting_customer:site_delete", args=[account.pk])
        else:
            site["manage_url"] = reverse("hosting_customer:site_setup_status", args=[account.pk, domain]) if site["app"] else reverse("hosting_customer:site_setup", args=[account.pk, domain])
            site["setup_url"] = reverse("hosting_customer:site_setup", args=[account.pk, domain])
            site["delete_url"] = reverse("hosting_customer:site_remove", args=[account.pk, domain])
        site["files_url"] = reverse("hosting_customer:section", args=[account.pk, "files"])
    return render(request, "hosting/customer/plan.html", {
        "account": account, "state": state, "sidebar": portal.service_sidebar(request, account),
        "add_url": reverse("hosting_customer:site_add", args=[account.pk]),
        "upgrade_url": reverse("renewals_customer:hosting_upgrade", args=[account.pk])})


@require_POST
@login_required
def site_add(request, pk):
    account = _own_account(request, pk)
    try:
        domain = plan_sites.add_domain(request.user, account, request.POST.get("domain", ""), request=request)
    except ServiceError as exc:
        messages.error(request, exc.message)
        return redirect("hosting_customer:plan", pk=account.pk)
    messages.success(request, f"{domain} was added to this plan. Now choose what to create on it.")
    return redirect("hosting_customer:site_setup", pk=account.pk, domain=domain)
