"""
The customer's Databases screen. Like the File Manager, every operation goes through the hosting-control gateway, so
ownership, the allowlist, the live-account check and the audit trail are shared; the provider enforces the plan limits,
the naming rules and the password rules. Passwords are passed straight to the provider and never stored, logged or put
in an audit record.
"""
from urllib.parse import urlencode, urlsplit

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
    total_mb = round(sum(d.size_mb or 0 for d in databases), 1)
    biggest = max([d.size_mb or 0 for d in databases] or [0]) or 1
    usage = [{"name": d.name, "size_mb": d.size_mb, "percent": round((d.size_mb or 0) * 100 / biggest)} for d in databases]
    admin_kind = ""
    if databases:
        try:
            admin_kind = control.perform(request.user, account, "databases", "database_admin_access",
                                         databases[0].name).kind
        except ServiceError:
            admin_kind = ""
    return render(request, "hosting/customer/databases.html", {
        "account": account, "sidebar": sidebar, "limits": limits, "databases": databases, "users": users,
        "grants": grants, "privileges": DATABASE_PRIVILEGES, "total_mb": total_mb, "usage": usage,
        "admin_kind": admin_kind, "browse_url": reverse("hosting_customer:database_browse", args=[account.pk]),
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


PAGE_SIZE = 20
MAX_CELL = 120


@login_required
def my_hosting_database_browse(request, pk):
    """phpMyAdmin for a provider that offers it (we send the person there), otherwise WHPanel's own read-only browser."""
    account = _own_account(request, pk)
    if not _supports(account):
        return redirect(_back(account))
    database = request.GET.get("database", "")
    try:
        access = control.perform(request.user, account, "databases", "database_admin_access", database)
        if access.kind == "external":
            target = urlsplit(access.url)
            if target.scheme != "https" or not target.netloc:
                raise ServiceError("The database manager address was not valid.", code="bad_admin_url")
            return redirect(access.url)
        tables = control.perform(request.user, account, "databases", "list_tables", database)
        table = request.GET.get("table") or (tables[0].name if tables else "")
        try:
            page = max(1, int(request.GET.get("page", "1")))
        except ValueError:
            page = 1
        data = control.perform(request.user, account, "databases", "browse_table", database, table,
                               (page - 1) * PAGE_SIZE, PAGE_SIZE) if table else None
    except ServiceError as exc:
        messages.error(request, exc.message)
        return redirect(_back(account))
    pages = max(1, -(-data.total // PAGE_SIZE)) if data else 1
    rows = [[(cell if len(cell) <= MAX_CELL else cell[:MAX_CELL] + "...") for cell in row] for row in data.rows] if data else []

    def link(**changes):
        query = {"database": database, "table": table, "page": page, **changes}
        return reverse("hosting_customer:database_browse", args=[account.pk]) + "?" + urlencode(query)

    return render(request, "hosting/customer/database_browse.html", {
        "account": account, "sidebar": portal.service_sidebar(request, account), "database": database, "tables": tables,
        "table": table, "data": data, "rows": rows, "page": page, "pages": pages, "back_url": _back(account),
        "table_links": [(t, link(table=t.name, page=1)) for t in tables],
        "prev_url": link(page=page - 1) if page > 1 else "", "next_url": link(page=page + 1) if page < pages else ""})
