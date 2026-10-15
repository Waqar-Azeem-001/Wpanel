"""Import website: the three honest ways to bring an existing website onto a plan (we move it, you move it, or restore a backup). Explanation only; nothing is copied here."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.core import portal

from .views import _own_account


@login_required
def import_site(request, pk):
    account = _own_account(request, pk)
    if account.status != "active":
        return redirect("hosting_customer:detail", pk=account.pk)
    return render(request, "hosting/customer/import_site.html", {
        "account": account, "sidebar": portal.service_sidebar(request, account), "ticket_url": reverse("support_customer:new"),
        "files_url": reverse("hosting_customer:section", args=[account.pk, "files"]), "databases_url": reverse("hosting_customer:section", args=[account.pk, "databases"])})
