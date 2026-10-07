"""
The customer's Security Center: one page with the state of SSL and blocked addresses (read from the other tools), a malware scan with
quarantine, folder protection, SSH keys, file-integrity monitoring and the recent security-relevant events from the audit trail.
Every operation goes through the hosting-control gateway. Passwords and keys' private parts never appear here; the audit trail records
paths, logins and fingerprints only.
"""
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.audit.models import AuditEvent
from apps.core import portal
from apps.core.exceptions import ServiceError

from . import dashboard
from .views import _own_account
from .views_domains import _read, _run, _supports

# What the events list shows: operation -> a plain sentence. Anything else the gateway logged is left out.
EVENT_TEXT = {
    "run_malware_scan": "Malware scan run", "quarantine_file": "File moved to quarantine", "restore_quarantined": "File put back from quarantine",
    "protect_directory": "Folder protected", "unprotect_directory": "Folder protection removed", "set_ssh_enabled": "SSH switched",
    "add_ssh_key": "SSH key added", "delete_ssh_key": "SSH key removed", "create_integrity_baseline": "Integrity baseline taken",
    "check_integrity": "Integrity check run", "add_ip_rule": "IP address blocked", "delete_ip_rule": "IP block removed",
    "request_ssl": "Certificate installed or renewed", "remove_ssl": "Certificate removed", "set_https_redirect": "Always use HTTPS changed",
    "restore_backup": "Backup restored", "set_ftp_password": "FTP password changed", "create_ftp_account": "FTP account created",
    "delete_ftp_account": "FTP account deleted", "set_mailbox_password": "Mailbox password changed",
}


def _url(account):
    return reverse("hosting_customer:security", args=[account.pk])


def _when(text):
    return datetime.fromisoformat(text) if text else None


def _events(account):
    rows = AuditEvent.objects.filter(target_type__endswith="hostingaccount", target_id=str(account.pk), action__startswith="hosting.control")
    out = []
    for event in rows[:200]:
        meta = event.metadata or {}
        text = EVENT_TEXT.get(meta.get("operation"))
        if not text:
            continue
        detail = meta.get("path") or meta.get("domain") or meta.get("login") or meta.get("network") or meta.get("backup") or ""
        out.append({"when": event.created_at, "text": text, "detail": detail, "failed": event.action.endswith("failed"), "support": bool(meta.get("admin_access")),
                    "by": event.actor_repr or "system"})
        if len(out) == 15:
            break
    return out


@login_required
def my_hosting_security(request, pk):
    account = _own_account(request, pk)
    sidebar = portal.service_sidebar(request, account)
    if not _supports(account, "security"):
        return render(request, "hosting/customer/section.html", {
            "account": account, "sidebar": sidebar, "section": dashboard.section_page(request.user, account, "security")})
    try:
        scan = _read(request, account, "security", "get_scan_result")
        quarantine = _read(request, account, "security", "list_quarantine")
        protected = _read(request, account, "security", "list_protected_dirs")
        ssh = _read(request, account, "security", "ssh_status")
        integrity = _read(request, account, "security", "integrity_status")
    except ServiceError as exc:
        messages.error(request, exc.message)
        return render(request, "hosting/customer/security.html", {"account": account, "sidebar": sidebar, "failed": True})
    ssl_rows, blocks = [], None
    if _supports(account, "ssl"):
        try:
            ssl_rows = _read(request, account, "ssl", "list_ssl_status")
        except ServiceError:
            ssl_rows = []
    if _supports(account, "access"):
        try:
            blocks = len(_read(request, account, "access", "list_ip_rules"))
        except ServiceError:
            blocks = None
    ssl_bad = [r for r in ssl_rows if r.status in ("none", "expired", "expiring")]
    high = sum(1 for f in scan.findings if f.severity == "high")
    return render(request, "hosting/customer/security.html", {
        "account": account, "sidebar": sidebar, "scan": scan, "scanned": _when(scan.scanned_at), "high": high,
        "quarantine": [{"q": q, "at": _when(q.quarantined_at)} for q in quarantine],
        "protected": protected, "ssh": ssh, "integrity": integrity, "baseline": _when(integrity.baseline_at),
        "checked": _when(integrity.checked_at), "events": _events(account), "blocks": blocks,
        "ssl_total": len(ssl_rows), "ssl_bad": len(ssl_bad),
        "integrity_changes": len(integrity.changed) + len(integrity.added) + len(integrity.removed),
        "ssl_url": reverse("hosting_customer:ssl", args=[account.pk]) if ssl_rows else "",
        "access_url": reverse("hosting_customer:access", args=[account.pk]) if blocks is not None else "",
        "action_url": reverse("hosting_customer:security_action", args=[account.pk])})


@require_POST
@login_required
def my_hosting_security_action(request, pk):
    account = _own_account(request, pk)
    post = request.POST
    action = post.get("action", "")
    try:
        if action == "scan":
            result = _run(request, account, "security", "run_malware_scan", metadata={})
            messages.success(request, f"Scanned {result.files_scanned} files: " + (
                f"{len(result.findings)} to look at." if result.findings else "nothing suspicious found."))
        elif action == "quarantine":
            path = post.get("path", "")
            _run(request, account, "security", "quarantine_file", path, metadata={"path": path})
            messages.success(request, "The file was moved to quarantine. It is not deleted: you can put it back.")
        elif action == "restore":
            item = post.get("item", "")
            _run(request, account, "security", "restore_quarantined", item, metadata={"item": item})
            messages.success(request, "The file was put back where it was.")
        elif action == "protect":
            path, login = post.get("path", ""), post.get("login", "")
            _run(request, account, "security", "protect_directory", path, login, post.get("password", ""),
                 metadata={"path": path, "login": login})
            messages.success(request, "The folder now asks for a login.")
        elif action == "unprotect":
            path = post.get("path", "")
            _run(request, account, "security", "unprotect_directory", path, metadata={"path": path})
            messages.success(request, "The folder is open to visitors again.")
        elif action == "ssh":
            enabled = post.get("enabled") == "1"
            _run(request, account, "security", "set_ssh_enabled", enabled, metadata={"enabled": enabled})
            messages.success(request, "SSH is on." if enabled else "SSH is off.")
        elif action == "add_key":
            label = post.get("label", "")
            fingerprint = _run(request, account, "security", "add_ssh_key", label, post.get("public_key", ""), metadata={"label": label})
            messages.success(request, f"The key was added ({fingerprint}).")
        elif action == "delete_key":
            fingerprint = post.get("fingerprint", "")
            _run(request, account, "security", "delete_ssh_key", fingerprint, metadata={"fingerprint": fingerprint})
            messages.success(request, "The key was removed.")
        elif action == "baseline":
            result = _run(request, account, "security", "create_integrity_baseline", metadata={})
            messages.success(request, f"Baseline taken for {result.files_in_baseline} files.")
        elif action == "check":
            result = _run(request, account, "security", "check_integrity", metadata={})
            changes = len(result.changed) + len(result.added) + len(result.removed)
            messages.success(request, "No file has changed since the baseline." if not changes else f"{changes} difference(s) found since the baseline.")
        else:
            raise ServiceError("That action is not available.", code="unknown_action")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect(_url(account))
