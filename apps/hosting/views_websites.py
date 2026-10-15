"""
The customer's Websites: every website across their hosting plans in one list (open, visit, staging, delete), the delete confirmation, and the staging page.
Deleting asks the customer to type the website's address; it removes the website's files (and the database WHPanel made for it) and leaves the hosting plan, the domain and
email alone.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core import portal
from apps.core.exceptions import ServiceError

from . import dashboard, plan_sites, services, site_apps, staging
from .models import HostingAccount, HostingStatus
from .views import _own_account


@login_required
def websites(request):
    accounts = HostingAccount.objects.filter(client__contacts__user=request.user).select_related("product", "server").distinct()
    cards = dashboard.website_cards(request.user, accounts)
    groups = []
    for card in cards:
        account = card["account"]
        live = account.status == HostingStatus.ACTIVE
        state = plan_sites.summary(request.user, account) if live else {"allowed": 1, "created": 1 if card["app"] else 0, "attached": 1, "free": 0, "sites": [], "can_add": False, "reason": ""}
        rows = []
        # the plan's own website first, exactly as before; then the websites on domains added to the plan
        stage = staging.current(account) if card["app"] is not None else None
        rows.append({**card, "domain": account.domain, "extra": False, "staging": stage, "staging_url": reverse("hosting_customer:staging", args=[account.pk]),
                     "delete_url": reverse("hosting_customer:site_delete", args=[account.pk]) if live else "",
                     "can_stage": card["app"] is not None and card["app"].kind == "wordpress" and card["app"].state == "ready",
                     "visit_url": account.site_url})
        for site in state["sites"]:
            if site["primary"]:
                continue
            name = site["domain"]
            rows.append({"account": account, "app": site["app"], "domain": name, "extra": True, "plan": card["plan"], "needs_setup": site["app"] is None,
                         "manage_url": reverse("hosting_customer:site_setup_status", args=[account.pk, name]) if site["app"] else reverse("hosting_customer:site_setup", args=[account.pk, name]),
                         "setup_url": reverse("hosting_customer:site_setup", args=[account.pk, name]), "delete_url": reverse("hosting_customer:site_remove", args=[account.pk, name]),
                         "staging": None, "can_stage": False, "visit_url": site["url"], "upgrade_url": card.get("upgrade_url", "")})
        groups.append({"account": account, "plan": card["plan"], "state": state, "rows": rows, "plan_url": reverse("hosting_customer:plan", args=[account.pk]),
                       "expires_at": account.expires_at})
    return render(request, "hosting/customer/websites.html", {"groups": groups, "sidebar": []})


@login_required
def site_delete(request, pk, domain=""):
    account = _own_account(request, pk)
    domain = "" if (domain or account.domain).lower() == account.domain.lower() else domain.lower()
    name = site_apps.site_domain(account, domain)
    app = site_apps.current(account, domain)
    if request.method == "POST":
        try:
            report = site_apps.delete_website(request.user, account, request.POST.get("confirm", ""), domain=domain, request=request)
        except ServiceError as exc:
            messages.error(request, exc.message)
        else:
            text = f"The website {name} was deleted ({report['files']} items removed)."
            text += " Its domain was taken off the plan, which has a free place again." if domain else " Your hosting plan is still yours: you can create a new website on it."
            if report["left"]:
                text += " Left in place: " + ", ".join(report["left"]) + "."
            messages.success(request, text)
            return redirect("hosting_customer:plan", pk=account.pk) if domain else redirect("hosting_customer:websites")
    return render(request, "hosting/customer/site_delete.html", {
        "account": account, "app": app, "site_domain": name, "entries": site_apps.folder_entries(request.user, account, domain)[:30],
        "staging": staging.current(account) if app and not domain else None,
        "sidebar": portal.service_sidebar(request, account),
        "backups_url": reverse("hosting_customer:section", args=[account.pk, "backups"])})


@login_required
def staging_page(request, pk):
    account = _own_account(request, pk)
    stage = staging.poll(request.user, account)
    reason = staging.unavailable_reason(account)
    return render(request, "hosting/customer/staging.html", {
        "account": account, "staging": stage, "reason": reason, "domain": staging.staging_domain(account), "sidebar": portal.service_sidebar(request, account),
        "action_url": reverse("hosting_customer:staging_action", args=[account.pk]), "live_url": account.site_url})


@require_POST
@login_required
def staging_action(request, pk):
    account = _own_account(request, pk)
    action = request.POST.get("action", "")
    try:
        if action == "create":
            result = staging.create(request.user, account, request=request)
            messages.success(request, "The staging website is ready." if result.state == "ready" else "The staging website is being created. This page updates when it is ready.")
        elif action == "push":
            if request.POST.get("confirm", "").strip().lower() != "publish":
                raise ServiceError("Type PUBLISH to confirm.", code="confirm_mismatch")
            result = staging.push(request.user, account, acknowledged=request.POST.get("own_backup") == "1", request=request)
            messages.success(request, "The staging website was published to your live website." if result.state == "ready" else "Publishing has started. This page updates when it is done.")
        elif action == "delete":
            staging.delete(request.user, account, request=request)
            messages.success(request, "The staging website was deleted.")
        else:
            raise ServiceError("That action is not available.", code="unknown_action")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect("hosting_customer:staging", pk=account.pk)
