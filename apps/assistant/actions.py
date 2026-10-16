"""
Changes the AI Agent may propose and the customer approves. The Agent only proposes: the panel stores the request, the customer sees it as a card in the chat with Approve and
Cancel, and only the customer's own click runs it, once, through the same ``control.perform`` as the rest of the panel, on the customer's own services. Every step is audited.
"""
import re
import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.audit import services as audit
from apps.core.exceptions import ServiceError
from apps.hosting import care, control
from apps.hosting.adapters.capabilities import Capability, DnsRecord
from apps.notifications import services as notifications

from . import tools
from .models import AssistantSettings, Message, PendingAction, SenderType, VerificationCode

LIFETIME = timedelta(minutes=30)
CODE_LIFETIME = timedelta(minutes=5)
RESEND_AFTER = timedelta(seconds=30)
MAX_ATTEMPTS = 3
DNS_TYPES = ("A", "AAAA", "CNAME", "MX", "TXT")
PHP_LIMITS = {"memory_limit": ("memory limit", 64, 2048, "M"), "upload_max_filesize": ("upload size limit", 2, 2048, "M"), "max_execution_time": ("maximum execution time", 30, 600, "")}


def _domain(ctx, params):
    return tools._resolve(ctx.user, params.get("domain", ""))


def _renew_ssl(ctx, params):
    account, name = _domain(ctx, params)
    return account, {"domain": name}, f"Request or renew the SSL certificate of {name}"


def _force_https(ctx, params):
    account, name = _domain(ctx, params)
    enabled = str(params.get("enabled", "true")).lower() not in ("false", "0", "no", "off")
    return account, {"domain": name, "enabled": enabled}, f"{'Switch on' if enabled else 'Switch off'} the redirect from HTTP to HTTPS for {name}"


def _quota(ctx, params):
    address = (params.get("address") or "").strip().lower()
    try:
        mb = int(params.get("quota_mb"))
    except (TypeError, ValueError):
        raise ServiceError("Say the new size in MB or GB.", code="quota_needed") from None
    if not 50 <= mb <= 51200:
        raise ServiceError("A mailbox size must be between 50 MB and 50 GB.", code="quota_range")
    for account in tools.accounts(ctx.user):
        try:
            boxes = control.perform(ctx.user, account, Capability.EMAIL, "list_mailboxes")
        except ServiceError:
            continue
        if any(b.address.lower() == address for b in boxes):
            return account, {"address": address, "quota_mb": mb}, f"Set the size of the mailbox {address} to {mb} MB"
    raise ServiceError("That mailbox is not on your account.", code="not_yours")


def _php(ctx, params):
    account, name = _domain(ctx, params)
    key = params.get("key", "")
    if key not in PHP_LIMITS:
        raise ServiceError("I can change the memory limit, the upload size limit or the maximum execution time.", code="php_key")
    label, low, high, unit = PHP_LIMITS[key]
    digits = re.sub(r"[^0-9]", "", str(params.get("value", "")))
    if not digits:
        raise ServiceError(f"Say the new {label}.", code="php_value")
    number = int(digits)
    if not low <= number <= high:
        raise ServiceError(f"The {label} must be between {low}{unit} and {high}{unit}.", code="php_range")
    return account, {"domain": name, "key": key, "value": f"{number}{unit}" if unit else number}, f"Set the PHP {label} of {name} to {number}{unit or ' seconds'}"


def _scan(ctx, params):
    account, name = _domain(ctx, params)
    return account, {"domain": name}, f"Start a malware scan of {name}"


def _backup(ctx, params):
    account, name = _domain(ctx, params)
    return account, {"domain": name}, f"Take a backup of your hosting account ({account.domain}) now"


def run_renew_ssl(user, account, p):
    control.perform(user, account, Capability.SSL, "request_ssl", p["domain"], audit_metadata={"domain": p["domain"], "via": "ai_agent"})
    return f"The SSL request for {p['domain']} was sent. It can take a few minutes to be installed."


def run_force_https(user, account, p):
    control.perform(user, account, Capability.SSL, "set_https_redirect", p["domain"], p["enabled"], audit_metadata={"domain": p["domain"], "enabled": p["enabled"], "via": "ai_agent"})
    return f"HTTPS redirect is now {'on' if p['enabled'] else 'off'} for {p['domain']}."


def run_quota(user, account, p):
    control.perform(user, account, Capability.EMAIL, "set_mailbox_quota", p["address"], p["quota_mb"], audit_metadata={"address": p["address"], "quota_mb": p["quota_mb"], "via": "ai_agent"})
    return f"The mailbox {p['address']} now has {p['quota_mb']} MB."


def run_php(user, account, p):
    control.perform(user, account, Capability.PHP, "set_php_settings", p["domain"], {p["key"]: p["value"]}, audit_metadata={"domain": p["domain"], "key": p["key"], "via": "ai_agent"})
    return f"PHP {PHP_LIMITS[p['key']][0]} of {p['domain']} is now {p['value']}."


def run_scan(user, account, p):
    domain = "" if p["domain"] == account.domain.lower() else p["domain"]
    care.start_scan(user, account, domain=domain)
    return f"The scan of {p['domain']} started. Open Website care to see what it finds."


def run_backup(user, account, p):
    options = control.perform(user, account, Capability.BACKUPS, "backup_options")
    kind = options.kinds[0][0] if options.kinds else "full"
    control.perform(user, account, Capability.BACKUPS, "create_backup", kind, audit_metadata={"kind": kind, "via": "ai_agent"})
    return "The backup was started. You will find it in Hosting plan > Backups when it is ready."


# --- the changes that need the customer's security code ------------------------------------------------------------------------------------

def _dns_add(ctx, params):
    account, name = _domain(ctx, params)
    kind = str(params.get("type", "")).upper()
    if kind not in DNS_TYPES:
        raise ServiceError("I can add A, AAAA, CNAME, MX and TXT records.", code="dns_type")
    host, value = str(params.get("name", "")).strip().lower() or "@", str(params.get("value", "")).strip()
    if not value or len(value) > 400 or any(c in value for c in "\r\n"):
        raise ServiceError("Say the value of the record.", code="dns_value")
    if kind in ("A", "AAAA", "CNAME", "MX") and " " in value:
        raise ServiceError("That value is not valid for this record type.", code="dns_value")
    try:
        priority = int(params["priority"]) if kind == "MX" else None
    except (KeyError, TypeError, ValueError):
        priority = 10 if kind == "MX" else None
    return account, {"domain": name, "type": kind, "name": host, "value": value, "priority": priority}, f"Add a {kind} record to {name}: {host} -> {value}"


def _dns_delete(ctx, params):
    account, name = _domain(ctx, params)
    kind, host = str(params.get("type", "")).upper(), str(params.get("name", "")).strip().lower()
    rows = control.perform(ctx.user, account, Capability.DNS, "list_dns_records", name)
    found = [r for r in rows if r.type.upper() == kind and r.name.lower().rstrip(".") in (host, host + "." + name, host + "." + name + ".") and r.type.upper() not in ("SOA", "NS")]
    if not found:
        raise ServiceError(f"I could not find a {kind or 'matching'} record named {host or '?'} on {name}.", code="dns_missing")
    record = found[0]
    return account, {"domain": name, "record_id": record.record_id, "type": record.type, "name": record.name, "value": record.value}, f"Delete the {record.type} record {record.name} -> {record.value} of {name}"


def _restore(ctx, params):
    wanted = str(params.get("backup_id", "")).strip()
    for account in tools.accounts(ctx.user):
        try:
            rows = control.perform(ctx.user, account, Capability.BACKUPS, "list_backups")
        except ServiceError:
            continue
        for row in rows:
            if row.backup_id == wanted:
                return account, {"backup_id": wanted}, f"Restore the {row.kind} backup {wanted} ({row.created_at or 'date unknown'}) of {account.domain}. Current files and data it covers are replaced"
    raise ServiceError("That backup was not found on your account.", code="backup_missing")


def _mailbox_delete(ctx, params):
    address = (params.get("address") or "").strip().lower()
    for account in tools.accounts(ctx.user):
        try:
            boxes = control.perform(ctx.user, account, Capability.EMAIL, "list_mailboxes")
        except ServiceError:
            continue
        if any(b.address.lower() == address for b in boxes):
            return account, {"address": address}, f"Delete the mailbox {address} and every message in it"
    raise ServiceError("That mailbox is not on your account.", code="not_yours")


def run_dns_add(user, account, p):
    record = DnsRecord(record_id="", type=p["type"], name=p["name"], value=p["value"], ttl=3600, priority=p.get("priority"))
    control.perform(user, account, Capability.DNS, "add_dns_record", p["domain"], record, audit_metadata={"domain": p["domain"], "type": p["type"], "name": p["name"], "via": "ai_agent"})
    return f"The {p['type']} record {p['name']} was added to {p['domain']}. DNS changes can take a while to be seen everywhere."


def run_dns_delete(user, account, p):
    control.perform(user, account, Capability.DNS, "delete_dns_record", p["domain"], p["record_id"], audit_metadata={"domain": p["domain"], "type": p["type"], "name": p["name"], "via": "ai_agent"})
    return f"The {p['type']} record {p['name']} was deleted from {p['domain']}."


def run_restore(user, account, p):
    control.perform(user, account, Capability.BACKUPS, "restore_backup", p["backup_id"], audit_metadata={"backup": p["backup_id"], "via": "ai_agent"})
    return f"The restore of backup {p['backup_id']} was started. It can take a few minutes; a safety copy of what it replaces is kept where the server can."


def run_mailbox_delete(user, account, p):
    control.perform(user, account, Capability.EMAIL, "delete_mailbox", p["address"], audit_metadata={"address": p["address"], "via": "ai_agent"})
    return f"The mailbox {p['address']} was deleted."


# name -> (what the customer is told it is, check the request and describe it, do it, what the customer must do first: "approve" or "code")
REGISTRY = {
    "renew_ssl": ("Renew SSL", _renew_ssl, run_renew_ssl, "approve"),
    "force_https": ("Force HTTPS", _force_https, run_force_https, "approve"),
    "set_mailbox_quota": ("Mailbox size", _quota, run_quota, "approve"),
    "set_php_setting": ("PHP setting", _php, run_php, "approve"),
    "scan_website": ("Malware scan", _scan, run_scan, "approve"),
    "create_backup": ("Backup now", _backup, run_backup, "approve"),
    "add_dns_record": ("Add a DNS record", _dns_add, run_dns_add, "code"),
    "delete_dns_record": ("Delete a DNS record", _dns_delete, run_dns_delete, "code"),
    "restore_backup": ("Restore a backup", _restore, run_restore, "code"),
    "delete_mailbox": ("Delete a mailbox", _mailbox_delete, run_mailbox_delete, "code"),
}


def changes_on():
    return AssistantSettings.load().changes_enabled


def mask(email):
    name, _, host = (email or "").partition("@")
    return (name[:1] + "*" * max(2, len(name) - 1) + "@" + host) if host else "your email"


def _meta(action, state):
    return {"tool": action.tool, "kind": "action", "status": state, "action": action.pk, "needs": action.needs}


def propose(ctx, name, params):
    """Check a change and store it for the customer. A change that needs the security code gets its code emailed now. Returns the PendingAction (its card is in the chat)."""
    if not changes_on():
        raise ServiceError("Changes through the Agent are switched off for now. I can tell you the steps to do it in the panel, or ask our team.", code="changes_off")
    entry = REGISTRY.get(name)
    if entry is None:
        raise ServiceError("That change is not available.", code="unknown_change")
    account, clean, summary = entry[1](ctx, params if isinstance(params, dict) else {})
    action = PendingAction.objects.create(conversation=ctx.conversation, user=ctx.user, tool=name, params=clean, summary=summary[:300], account_id=account.pk, needs=entry[3])
    Message.objects.create(conversation=ctx.conversation, sender=SenderType.EVENT, body=summary, action=action, tool_call=_meta(action, "pending"))
    if action.needs == "code":
        issue_code(action)
    audit.record("assistant.change_proposed", actor=ctx.user, target=action, metadata={"tool": name, "needs": action.needs}, request=ctx.request)
    return action


def _own(user, pk):
    action = PendingAction.objects.select_related("conversation", "user").filter(pk=pk, user=user).first()
    if action is None:
        raise ServiceError("That request was not found.", code="not_found", status_code=status.HTTP_404_NOT_FOUND)
    return action


def _mark(action, state, text=""):
    Message.objects.filter(action=action).update(tool_call=_meta(action, state))
    return state


def _say(action, text):
    Message.objects.create(conversation=action.conversation, sender=SenderType.AI, body=text)


# --- the security code -------------------------------------------------------------------------------------------------------------------------

def issue_code(action):
    """A new six-digit code for this change, sent to the address on the customer's account (never one typed in the chat). Only its hash is stored."""
    code = f"{secrets.randbelow(10 ** 6):06d}"
    now = timezone.now()
    VerificationCode.objects.update_or_create(action=action, defaults={"code_hash": make_password(code), "sent_to": mask(action.user.email), "expires_at": now + CODE_LIFETIME, "attempts": 0, "last_sent_at": now})
    notifications.dispatch("assistant.code", user=action.user, context={"code": code, "summary": action.summary, "minutes": int(CODE_LIFETIME.total_seconds() // 60)})


def resend_code(user, pk):
    action = _own(user, pk)
    if action.needs != "code" or action.status != "pending":
        raise ServiceError("This request is no longer active.", code="not_active")
    vc = action.verification
    wait = RESEND_AFTER - (timezone.now() - vc.last_sent_at)
    if wait.total_seconds() > 0:
        raise ServiceError(f"Please wait {int(wait.total_seconds()) + 1} seconds before asking for another code.", code="cooldown")
    issue_code(action)
    audit.record("assistant.code_resent", actor=user, target=action)
    return action


def verify(user, pk, entered, request=None):
    """The customer typed the code on the verification page. Returns the PendingAction; raises ServiceError (with the attempts left) for a wrong or stale code."""
    action = _own(user, pk)
    if action.needs != "code":
        raise ServiceError("This request does not need a code.", code="no_code")
    if action.status != "pending":
        raise ServiceError("This request is no longer active.", code="not_active")
    vc = action.verification
    if timezone.now() > vc.expires_at or timezone.now() - action.created_at > LIFETIME:
        PendingAction.objects.filter(pk=action.pk, status="pending").update(status="expired")
        _mark(action, "expired")
        raise ServiceError("The code expired. Request a new code.", code="code_expired")
    if vc.attempts >= MAX_ATTEMPTS:
        raise ServiceError("Too many wrong codes.", code="locked")
    if not re.fullmatch(r"\d{6}", (entered or "").strip()) or not check_password(entered.strip(), vc.code_hash):
        vc.attempts += 1
        vc.save(update_fields=["attempts", "updated_at"])
        left = MAX_ATTEMPTS - vc.attempts
        audit.record("assistant.code_wrong", actor=user, target=action, metadata={"left": left}, request=request)
        if left <= 0:
            PendingAction.objects.filter(pk=action.pk, status="pending").update(status="locked")
            _mark(action, "locked")
            _say(action, "That was the third wrong code, so I locked this request. Nothing was changed. Ask me again, or press Talk to a person.")
            notifications.dispatch("assistant.locked", user=user, context={"summary": action.summary})
            notifications.notify_team("assistant.locked_team", "manage_support", title=f"{action.conversation.client}: a security check was locked", body=action.summary, link=f"/staff/chats/{action.conversation_id}/")
            audit.record("assistant.code_locked", actor=user, target=action, request=request)
            raise ServiceError("Too many wrong codes. This request is locked and nothing was changed.", code="locked")
        raise ServiceError(f"Incorrect code. {left} attempt{'s' if left != 1 else ''} left.", code="code_wrong")
    audit.record("assistant.code_verified", actor=user, target=action, request=request)
    return _execute(action, user, request)


# --- running a change ----------------------------------------------------------------------------------------------------------------------------

def _execute(action, user, request=None):
    with transaction.atomic():
        won = PendingAction.objects.filter(pk=action.pk, status="pending").update(status="running")
    if not won:
        raise ServiceError("This request was already handled.", code="handled")
    from apps.hosting.models import HostingAccount

    try:
        account = tools.accounts(user).get(pk=action.account_id)
        result = REGISTRY[action.tool][2](user, account, action.params)
        state = "done"
    except (ServiceError, HostingAccount.DoesNotExist) as exc:
        result, state = getattr(exc, "message", "That account is no longer available."), "failed"
    action.status, action.result, action.executed_at = state, result[:500], timezone.now()
    action.save(update_fields=["status", "result", "executed_at", "updated_at"])
    _mark(action, state)
    _say(action, result if state == "done" else f"That did not work: {result}")
    audit.record("assistant.change_done" if state == "done" else "assistant.change_failed", actor=user, target=action, metadata={"tool": action.tool, "result": result[:200]}, request=request)
    return action


def approve(user, pk, request=None):
    """The customer pressed Approve. Runs the change once and returns the PendingAction."""
    action = _own(user, pk)
    if action.needs == "code":
        raise ServiceError("This change needs your security code. Use the Verify link on the card.", code="needs_code")
    if timezone.now() - action.created_at > LIFETIME:
        PendingAction.objects.filter(pk=action.pk, status="pending").update(status="expired")
        _mark(action, "expired")
        raise ServiceError("This request expired. Ask me again and I will prepare it.", code="expired")
    if not changes_on():
        raise ServiceError("Changes through the Agent are switched off for now.", code="changes_off")
    return _execute(action, user, request)


def cancel(user, pk, request=None):
    action = _own(user, pk)
    if not PendingAction.objects.filter(pk=action.pk, status="pending").update(status="cancelled"):
        raise ServiceError("This request was already handled.", code="handled")
    _mark(action, "cancelled")
    _say(action, "Cancelled. Nothing was changed.")
    audit.record("assistant.change_cancelled", actor=user, target=action, metadata={"tool": action.tool}, request=request)
    action.refresh_from_db()
    return action
