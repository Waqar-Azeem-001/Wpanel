"""
The customer's Databases screen. Like the File Manager, every operation goes through the hosting-control gateway, so
ownership, the allowlist, the live-account check and the audit trail are shared; the provider enforces the plan limits,
the naming rules and the password rules. Passwords are passed straight to the provider and never stored, logged or put
in an audit record.
"""
import json
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
    # The "Browse data" button leads to WHPanel's own table browser on every server (a provider-supplied manager is followed when the adapter says "external" at open time).
    admin_kind = "embedded" if databases else ""
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
TABS = ("browse", "structure", "sql")


def _manager_url(account, **query):
    query = {k: v for k, v in query.items() if v not in ("", None)}
    return reverse("hosting_customer:database_browse", args=[account.pk]) + ("?" + urlencode(query) if query else "")


def _manager(request, account, database, *, table="", page=1, tab="browse", sql="", result=None):
    """
    The database manager for one database: browse and edit rows, see the structure, run SQL. For a provider that offers
    phpMyAdmin we send the person there instead (a one-time secure address, https only).
    """
    access = control.perform(request.user, account, "databases", "database_admin_access", database)
    if access.kind == "external":
        target = urlsplit(access.url)
        if target.scheme != "https" or not target.netloc:
            raise ServiceError("The database manager address was not valid.", code="bad_admin_url")
        return redirect(access.url)
    tables = control.perform(request.user, account, "databases", "list_tables", database)
    names = [t.name for t in tables]
    if table and table not in names:
        raise ServiceError("That table does not exist.", code="no_table")
    table = table or (names[0] if names else "")
    tab = tab if tab in TABS else "browse"
    data = columns = None
    pages, rows = 1, []
    if table and tab in ("browse", "structure"):
        columns = control.perform(request.user, account, "databases", "describe_table", database, table)
    if table and tab == "browse":
        data = control.perform(request.user, account, "databases", "browse_table", database, table,
                               (page - 1) * PAGE_SIZE, PAGE_SIZE)
        pages = max(1, -(-data.total // PAGE_SIZE))
        key_idx = [data.columns.index(k) for k in data.key_columns]
        for row in data.rows:
            rows.append({
                "cells": [(c if c is None or len(c) <= MAX_CELL else c[:MAX_CELL] + "...") for c in row],
                "values_json": json.dumps(dict(zip(data.columns, row))),
                "key_json": json.dumps({data.columns[i]: row[i] for i in key_idx}) if key_idx else "",
                "keys": [(data.columns[i], row[i]) for i in key_idx],
            })
    columns_json = json.dumps([{"name": c.name, "type": c.type, "nullable": c.nullable, "pk": c.primary_key}
                               for c in (columns or [])])

    def tab_url(name, **extra):
        return _manager_url(account, database=database, table=table, tab=name, **extra)

    context = {
        "account": account, "sidebar": portal.service_sidebar(request, account), "database": database, "tables": tables,
        "table": table, "tab": tab, "data": data, "columns": columns, "columns_json": columns_json, "rows": rows,
        "page": page, "pages": pages, "sql": sql, "result": result, "back_url": _back(account),
        "editable": bool(data and data.key_columns),
        "table_links": [(t, _manager_url(account, database=database, table=t.name, tab=tab if tab != "sql" else "browse"))
                        for t in tables],
        "tab_links": [("browse", tab_url("browse")), ("structure", tab_url("structure")), ("sql", tab_url("sql"))],
        "prev_url": tab_url("browse", page=page - 1) if page > 1 else "",
        "next_url": tab_url("browse", page=page + 1) if page < pages else "",
        "row_action_url": reverse("hosting_customer:database_row", args=[account.pk]),
        "sql_url": reverse("hosting_customer:database_sql", args=[account.pk]),
    }
    return render(request, "hosting/customer/database_browse.html", context)


def _page_number(raw):
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


@login_required
def my_hosting_database_browse(request, pk):
    account = _own_account(request, pk)
    if not _supports(account):
        return redirect(_back(account))
    try:
        return _manager(request, account, request.GET.get("database", ""), table=request.GET.get("table", ""),
                        page=_page_number(request.GET.get("page")), tab=request.GET.get("tab", "browse"))
    except ServiceError as exc:
        messages.error(request, exc.message)
        return redirect(_back(account))


@require_POST
@login_required
def my_hosting_database_sql(request, pk):
    """Run one SQL statement and show its result on the SQL tab. The statement text is never written to the audit trail."""
    account = _own_account(request, pk)
    database, table = request.POST.get("database", ""), request.POST.get("table", "")
    sql = request.POST.get("sql", "")
    result = None
    try:
        result = control.perform(request.user, account, "databases", "run_query", database, sql, request=request,
                                 audit_metadata={"database": database, "kind": (sql.split() or [""])[0].upper()[:12],
                                                 "length": len(sql)})
    except ServiceError as exc:
        messages.error(request, exc.message)
    try:
        return _manager(request, account, database, table=table, tab="sql", sql=sql, result=result)
    except ServiceError as exc:
        messages.error(request, exc.message)
        return redirect(_back(account))


@require_POST
@login_required
def my_hosting_database_row(request, pk):
    """Add, change or remove one row of a table. Values are passed as data (never glued into SQL)."""
    account = _own_account(request, pk)
    post = request.POST
    database, table, action = post.get("database", ""), post.get("table", ""), post.get("action", "")
    values = {k[5:]: v for k, v in post.items() if k.startswith("val__")}
    key = {k[5:]: v for k, v in post.items() if k.startswith("key__")}
    try:
        meta = {"database": database, "table": table, "columns": sorted(values)}
        if action == "insert":
            control.perform(request.user, account, "databases", "insert_row", database, table, values,
                            request=request, audit_metadata=meta)
            messages.success(request, "Row added.")
        elif action == "update":
            control.perform(request.user, account, "databases", "update_row", database, table, key, values,
                            request=request, audit_metadata={**meta, "key": key})
            messages.success(request, "Row saved.")
        elif action == "delete":
            control.perform(request.user, account, "databases", "delete_row", database, table, key,
                            request=request, audit_metadata={**meta, "key": key})
            messages.success(request, "Row deleted.")
        else:
            raise ServiceError("That action is not available.", code="unknown_action")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect(_manager_url(account, database=database, table=table, tab="browse",
                                 page=_page_number(post.get("page"))))
