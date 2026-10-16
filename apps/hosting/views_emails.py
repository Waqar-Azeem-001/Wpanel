"""
Emails: every mailbox of every hosting plan in one place (the rail's Emails entry). Each plan shows how many mailboxes it has of how many it allows, the mailboxes with their
use, and the way to the full email screen. Only what a plan's server reports is shown.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from urllib.parse import urlencode

from django.urls import reverse

from apps.core.exceptions import ServiceError

from . import control, services
from .models import HostingStatus
from .views_domains import _supports

MAX_PLANS = 12
MAX_SHOWN = 40


def _guide(slug, search):
    """The help article when it is published, otherwise a search of the help centre, so a link on this page never leads nowhere."""
    from apps.support import kb

    article = kb.public_articles().filter(category__slug="email", slug=slug).first()
    if article is not None:
        return reverse("help:article", args=[article.category.slug, article.slug])
    return reverse("help:index") + "?" + urlencode({"q": search})


@login_required
def emails(request):
    accounts = (services.visible_hosting_accounts_for_user(request.user).filter(status__in=(HostingStatus.ACTIVE, HostingStatus.SUSPENDED))
                .select_related("product", "server").order_by("domain")[:MAX_PLANS])
    plans = []
    for account in accounts:
        row = {"account": account, "supported": _supports(account, "email"), "boxes": [], "limit": None, "count": None, "error": "",
               "manage_url": reverse("hosting_customer:email", args=[account.pk]), "can_add": account.status == HostingStatus.ACTIVE, "webmail": False}
        if row["supported"]:
            try:
                row["limit"] = control.perform(request.user, account, "email", "email_limits").max_mailboxes
                boxes = control.perform(request.user, account, "email", "list_mailboxes")
                row["count"] = len(boxes)
                try:  # a server without a webmail simply gets no "Open webmail" button
                    row["webmail"] = bool(boxes) and control.perform(request.user, account, "email", "webmail_access", boxes[0].address) is not None
                except ServiceError:
                    row["webmail"] = False
                for box in boxes[:MAX_SHOWN]:
                    percent = round(box.used_mb * 100 / box.quota_mb) if box.quota_mb and box.used_mb is not None else None
                    query = urlencode({"address": box.address})
                    row["boxes"].append({"address": box.address, "percent": percent, "used": box.used_mb, "quota": box.quota_mb,
                                         "webmail_url": reverse("hosting_customer:webmail", args=[account.pk]) + "?" + query,
                                         "settings_url": reverse("hosting_customer:webmail_settings", args=[account.pk]) + "?" + query})
                row["more"] = max(0, len(boxes) - MAX_SHOWN)
            except ServiceError as exc:
                row["error"] = exc.message
        plans.append(row)
    return render(request, "hosting/customer/emails.html", {"plans": plans, "sidebar": [], "help_url": _guide("how-to-set-up-email-on-your-phone-or-outlook", "set up email phone"),
                                                             "help_create_url": _guide("how-to-create-a-mailbox-and-open-webmail", "create mailbox")})
