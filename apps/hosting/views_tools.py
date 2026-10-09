"""
The customer's advanced tools: Logs (read-only view of the website's error and access logs), FTP accounts (logins limited to a
folder) and IP blocking. Every operation goes through the hosting-control gateway. Passwords are never shown or audited, the
audit trail records names and folders only, and a block that would cut the customer off from their own panel is refused here.
"""
import ipaddress
from datetime import datetime
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core import portal
from apps.core.exceptions import ServiceError

from . import dashboard
from .adapters.capabilities import LOG_LINE_CHOICES as LOG_LINES
from .views import _own_account
from .views_domains import _read, _run, _supports


def _unsupported(request, account, key):
    return render(request, "hosting/customer/section.html", {
        "account": account, "sidebar": portal.service_sidebar(request, account),
        "section": dashboard.section_page(request.user, account, key)})


def _when(text):
    return datetime.fromisoformat(text) if text else None


# --- Logs ---------------------------------------------------------------------------------------------------------------

@login_required
def my_hosting_logs(request, pk):
    account = _own_account(request, pk)
    if not _supports(account, "logs"):
        return _unsupported(request, account, "logs")
    kind = request.GET.get("kind") if request.GET.get("kind") in ("error", "access") else "error"
    try:
        lines = int(request.GET.get("lines", ""))
    except ValueError:
        lines = 0
    lines = lines if lines in LOG_LINES else LOG_LINES[1]
    query = request.GET.get("q", "").strip()[:100]
    try:
        log = _read(request, account, "logs", "read_log", kind, lines, query)
    except ServiceError as exc:
        messages.error(request, exc.message)
        log = None
    base = reverse("hosting_customer:logs", args=[account.pk])
    return render(request, "hosting/customer/logs.html", {
        "account": account, "sidebar": portal.service_sidebar(request, account), "log": log, "kind": kind, "lines": lines,
        "line_choices": LOG_LINES, "query": query,
        "kind_urls": [(k, label, base + "?" + urlencode({"kind": k, "lines": lines, "q": query}))
                      for k, label in (("error", "Error log"), ("access", "Access log"))],
        "action_url": reverse("hosting_customer:logs_action", args=[account.pk])})


@require_POST
@login_required
def my_hosting_logs_action(request, pk):
    account = _own_account(request, pk)
    kind = request.POST.get("kind", "")
    try:
        if request.POST.get("action") == "clear":
            _run(request, account, "logs", "clear_log", kind, metadata={"kind": kind})
            messages.success(request, "The log was cleared.")
        else:
            raise ServiceError("That action is not available.", code="unknown_action")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect(reverse("hosting_customer:logs", args=[account.pk]) + "?" + urlencode({"kind": kind}))


# --- FTP ----------------------------------------------------------------------------------------------------------------

@login_required
def my_hosting_ftp(request, pk):
    account = _own_account(request, pk)
    if not _supports(account, "ftp"):
        return _unsupported(request, account, "ftp")
    try:
        options = _read(request, account, "ftp", "ftp_options")
        accounts = _read(request, account, "ftp", "list_ftp_accounts")
    except ServiceError as exc:
        messages.error(request, exc.message)
        options, accounts = None, []
    return render(request, "hosting/customer/ftp.html", {
        "account": account, "sidebar": portal.service_sidebar(request, account), "options": options,
        "rows": [{"a": a, "created": _when(a.created_at)} for a in accounts], "count": len(accounts),
        "full": bool(options and options.max_accounts and len(accounts) >= options.max_accounts),  # 0 means no fixed limit
        "files_url": reverse("hosting_customer:files", args=[account.pk]) if _supports(account, "files") else "",
        "action_url": reverse("hosting_customer:ftp_action", args=[account.pk])})


@require_POST
@login_required
def my_hosting_ftp_action(request, pk):
    account = _own_account(request, pk)
    post = request.POST
    action, login = post.get("action", ""), post.get("login", "")
    try:
        if action == "add":
            name, folder = post.get("name", ""), post.get("directory", "")
            created = _run(request, account, "ftp", "create_ftp_account", name, post.get("password", ""), folder,
                           metadata={"name": name.strip().lower(), "directory": folder.strip()})
            messages.success(request, f"The FTP account {created} was created.")
        elif action == "password":
            _run(request, account, "ftp", "set_ftp_password", login, post.get("password", ""), metadata={"login": login})
            messages.success(request, "The password was changed.")
        elif action == "delete":
            _run(request, account, "ftp", "delete_ftp_account", login, metadata={"login": login})
            messages.success(request, "The FTP account was deleted.")
        else:
            raise ServiceError("That action is not available.", code="unknown_action")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect(reverse("hosting_customer:ftp", args=[account.pk]))


# --- IP blocking --------------------------------------------------------------------------------------------------------

def _would_block_me(request, network):
    try:
        mine = ipaddress.ip_address(request.META.get("REMOTE_ADDR", ""))
        return mine in ipaddress.ip_network(network.strip(), strict=False)
    except ValueError:
        return False  # not an address: the provider explains what is wrong with it


@login_required
def my_hosting_access(request, pk):
    account = _own_account(request, pk)
    if not _supports(account, "access"):
        return _unsupported(request, account, "access")
    try:
        rules = _read(request, account, "access", "list_ip_rules")
    except ServiceError as exc:
        messages.error(request, exc.message)
        rules = None
    return render(request, "hosting/customer/access.html", {
        "account": account, "sidebar": portal.service_sidebar(request, account),
        "rows": None if rules is None else [{"r": r, "created": _when(r.created_at)} for r in rules],
        "my_ip": request.META.get("REMOTE_ADDR", ""), "action_url": reverse("hosting_customer:access_action", args=[account.pk])})


@require_POST
@login_required
def my_hosting_access_action(request, pk):
    account = _own_account(request, pk)
    post = request.POST
    action = post.get("action", "")
    try:
        if action == "add":
            network = post.get("network", "")
            if _would_block_me(request, network):
                raise ServiceError("That would block your own connection, so you could not reach your website's panel. "
                                   "Enter a different address.", code="blocks_self")
            _run(request, account, "access", "add_ip_rule", network, post.get("note", ""), metadata={"network": network.strip()})
            messages.success(request, "The block was added.")
        elif action == "delete":
            rule = post.get("rule", "")
            _run(request, account, "access", "delete_ip_rule", rule, metadata={"rule": rule})
            messages.success(request, "The block was removed.")
        else:
            raise ServiceError("That action is not available.", code="unknown_action")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect(reverse("hosting_customer:access", args=[account.pk]))
