"""
Emails: every mailbox of every hosting plan in one place (the rail's Emails entry). Each plan shows how many mailboxes it has of how many it allows, the mailboxes with their
use, and the way to the full email screen. Only what a plan's server reports is shown.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse

from apps.core.exceptions import ServiceError

from . import control, services
from .models import HostingStatus
from .views_domains import _supports

MAX_PLANS = 12


@login_required
def emails(request):
    accounts = (services.visible_hosting_accounts_for_user(request.user).filter(status__in=(HostingStatus.ACTIVE, HostingStatus.SUSPENDED))
                .select_related("product", "server").order_by("domain")[:MAX_PLANS])
    plans = []
    for account in accounts:
        row = {"account": account, "supported": _supports(account, "email"), "boxes": [], "limit": None, "count": None, "error": "",
               "manage_url": reverse("hosting_customer:email", args=[account.pk]), "can_add": account.status == HostingStatus.ACTIVE}
        if row["supported"]:
            try:
                row["limit"] = control.perform(request.user, account, "email", "email_limits").max_mailboxes
                boxes = control.perform(request.user, account, "email", "list_mailboxes")
                row["count"] = len(boxes)
                for box in boxes[:6]:
                    percent = round(box.used_mb * 100 / box.quota_mb) if box.quota_mb and box.used_mb is not None else None
                    row["boxes"].append({"address": box.address, "percent": percent, "used": box.used_mb, "quota": box.quota_mb})
                row["more"] = max(0, len(boxes) - 6)
            except ServiceError as exc:
                row["error"] = exc.message
        plans.append(row)
    return render(request, "hosting/customer/emails.html", {"plans": plans, "sidebar": []})
