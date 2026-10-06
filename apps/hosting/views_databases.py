"""
The customer's Databases screen. Like the File Manager, every operation goes through the hosting-control gateway, so
ownership, the allowlist, the live-account check and the audit trail are shared; the provider enforces the plan limits,
the naming rules and the password rules. Passwords are passed straight to the provider and never stored, logged or put
in an audit record.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core import portal
from apps.core.exceptions import ServiceError

from . import control, dashboard
from .adapters.capabilities import DATABASE_PRIVILEGES
from .views import _own_account


def _back(account):
    return reverse("hosting_customer:databases", args=[account.pk])


def _supports(account):
    try:
        return "databases" in control.capabilities_for(account)
    except ServiceError:
        return False


def _run(request, account, operation, *args, metadata=None, **kwargs):
    return control.perform(request.user, account, "databases", operation, *args, request=request,
                           audit_metadata=metadata, **kwargs)


@login_required
def my_hosting_databases(request, pk):
    account = _own_account(request, pk)
    sidebar = portal.service_sidebar(request, account)
    if not _supports(account):
        return render(request, "hosting/customer/section.html", {
            "account": account, "sidebar": sidebar, "section": dashboard.section_page(request.user, account, "databases")})
    try:
        limits = control.perform(request.user, account, "databases", "database_limits")
        databases = control.perform(request.user, account, "databases", "list_databases")
        users = control.perform(request.user, account, "databases", "list_database_users")
        grants = control.perform(request.user, account, "databases", "list_database_grants")
    except ServiceError as exc:
        messages.error(request, exc.message)
        limits, databases, users, grants = None, [], [], []
    return render(request, "hosting/customer/databases.html", {
        "account": account, "sidebar": sidebar, "limits": limits, "databases": databases, "users": users,
        "grants": grants, "privileges": DATABASE_PRIVILEGES,
        "db_room": limits and limits.max_databases is not None and len(databases) >= limits.max_databases,
        "user_room": limits and limits.max_users is not None and len(users) >= limits.max_users})


@require_POST
@login_required
def my_hosting_database_action(request, pk):
    account = _own_account(request, pk)
    action = request.POST.get("action", "")
    post = request.POST
    try:
        if action == "create_database":
            name = post.get("name", "").strip()
            _run(request, account, "create_database", name, metadata={"database": name})
            messages.success(request, "Database created.")
        elif action == "delete_database":
            name = post.get("database", "")
            _run(request, account, "delete_database", name, metadata={"database": name})
            messages.success(request, "Database deleted.")
        elif action == "create_user":
            name = post.get("name", "").strip()
            _run(request, account, "create_database_user", name, post.get("password", ""),
                 metadata={"user": name})  # the password is deliberately not recorded anywhere
            messages.success(request, "Database user created.")
        elif action == "set_password":
            name = post.get("user", "")
            _run(request, account, "set_database_user_password", name, post.get("password", ""),
                 metadata={"user": name})
            messages.success(request, "Password changed.")
        elif action == "delete_user":
            name = post.get("user", "")
            _run(request, account, "delete_database_user", name, metadata={"user": name})
            messages.success(request, "Database user deleted.")
        elif action in ("grant", "revoke"):
            database, user = post.get("database", ""), post.get("user", "")
            chosen = [] if action == "revoke" else (
                list(DATABASE_PRIVILEGES) if post.get("all") else post.getlist("privileges"))
            if action == "grant" and not chosen:
                raise ServiceError("Choose at least one privilege, or use Remove access.", code="no_privileges")
            _run(request, account, "grant_database_access", database, user, chosen,
                 metadata={"database": database, "user": user, "privileges": chosen})
            messages.success(request, "Access updated." if chosen else "Access removed.")
        else:
            raise ServiceError("That action is not available.", code="unknown_action")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect(_back(account))
