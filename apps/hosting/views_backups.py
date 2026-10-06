"""
The customer's Backups screen: take a backup of the files, databases, email or the whole account, see which ones have been checked,
check one, restore one, delete one, and choose an automatic schedule. Every operation goes through the hosting-control gateway.
A restore replaces current data, so it asks first and is audited; the provider refuses to restore a backup that fails its
integrity check and keeps a safety copy of what it replaces.
"""
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core import portal
from apps.core.exceptions import ServiceError

from . import dashboard
from .views import _own_account
from .views_domains import _read, _run, _supports


def _url(account):
    return reverse("hosting_customer:backups", args=[account.pk])


def _when(text):
    return datetime.fromisoformat(text) if text else None


@login_required
def my_hosting_backups(request, pk):
    account = _own_account(request, pk)
    sidebar = portal.service_sidebar(request, account)
    if not _supports(account, "backups"):
        return render(request, "hosting/customer/section.html", {
            "account": account, "sidebar": sidebar, "section": dashboard.section_page(request.user, account, "backups")})
    try:
        options = _read(request, account, "backups", "backup_options")
        backups = _read(request, account, "backups", "list_backups")
        options = _read(request, account, "backups", "backup_options")  # a due scheduled backup updates the last-run time
    except ServiceError as exc:
        messages.error(request, exc.message)
        options, backups = None, []
    labels = {k: label for k, label, _ in (options.kinds if options else [])}
    rows = [{"b": b, "label": labels.get(b.kind, b.kind), "made": _when(b.created_at), "checked": _when(b.verified_at)} for b in backups]
    return render(request, "hosting/customer/backups.html", {
        "account": account, "sidebar": sidebar, "options": options, "rows": rows, "count": len(backups),
        "last_scheduled": _when(options.last_scheduled_at) if options else None,
        "action_url": reverse("hosting_customer:backup_action", args=[account.pk])})


@require_POST
@login_required
def my_hosting_backup_action(request, pk):
    account = _own_account(request, pk)
    post = request.POST
    action, backup = post.get("action", ""), post.get("backup", "")
    try:
        if action == "create":
            kind = post.get("kind", "")
            _run(request, account, "backups", "create_backup", kind, metadata={"kind": kind})
            messages.success(request, "The backup was made. Check it to be sure it can be restored.")
        elif action == "verify":
            _run(request, account, "backups", "verify_backup", backup, metadata={"backup": backup})
            messages.success(request, "The backup passed its check: it is complete and can be restored.")
        elif action == "restore":
            _run(request, account, "backups", "restore_backup", backup, metadata={"backup": backup})
            messages.success(request, "The backup was restored. A copy of what it replaced was saved in this list as 'before restore'.")
        elif action == "delete":
            _run(request, account, "backups", "delete_backup", backup, metadata={"backup": backup})
            messages.success(request, "The backup was deleted.")
        elif action == "schedule":
            schedule = post.get("schedule", "")
            _run(request, account, "backups", "set_backup_schedule", schedule, metadata={"schedule": schedule})
            messages.success(request, "The schedule was saved.")
        else:
            raise ServiceError("That action is not available.", code="unknown_action")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect(_url(account))
