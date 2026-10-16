"""
Changes the AI Agent may propose and the customer approves. The Agent only proposes: the panel stores the request, the customer sees it as a card in the chat with Approve and
Cancel, and only the customer's own click runs it, once, through the same ``control.perform`` as the rest of the panel, on the customer's own services. Every step is audited.
"""
import re
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.audit import services as audit
from apps.core.exceptions import ServiceError
from apps.hosting import care, control
from apps.hosting.adapters.capabilities import Capability

from . import tools
from .models import AssistantSettings, Message, PendingAction, SenderType

LIFETIME = timedelta(minutes=30)
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


# name -> (what the customer is told it is, check the request and describe it, do it)
REGISTRY = {
    "renew_ssl": ("Renew SSL", _renew_ssl, run_renew_ssl),
    "force_https": ("Force HTTPS", _force_https, run_force_https),
    "set_mailbox_quota": ("Mailbox size", _quota, run_quota),
    "set_php_setting": ("PHP setting", _php, run_php),
    "scan_website": ("Malware scan", _scan, run_scan),
    "create_backup": ("Backup now", _backup, run_backup),
}


def changes_on():
    return AssistantSettings.load().changes_enabled


def propose(ctx, name, params):
    """Check a change and store it for the customer's approval. Returns the PendingAction (its event line is in the chat)."""
    if not changes_on():
        raise ServiceError("Changes through the Agent are switched off for now. I can tell you the steps to do it in the panel, or ask our team.", code="changes_off")
    entry = REGISTRY.get(name)
    if entry is None:
        raise ServiceError("That change is not available.", code="unknown_change")
    account, clean, summary = entry[1](ctx, params if isinstance(params, dict) else {})
    action = PendingAction.objects.create(conversation=ctx.conversation, user=ctx.user, tool=name, params=clean, summary=summary[:300], account_id=account.pk)
    Message.objects.create(conversation=ctx.conversation, sender=SenderType.EVENT, body=summary, action=action, tool_call={"tool": name, "kind": "action", "status": "pending", "action": action.pk})
    audit.record("assistant.change_proposed", actor=ctx.user, target=action, metadata={"tool": name}, request=ctx.request)
    return action


def _own(user, pk):
    action = PendingAction.objects.select_related("conversation").filter(pk=pk, user=user).first()
    if action is None:
        raise ServiceError("That request was not found.", code="not_found", status_code=status.HTTP_404_NOT_FOUND)
    return action


def _mark(action, state, text=""):
    Message.objects.filter(action=action).update(tool_call={"tool": action.tool, "kind": "action", "status": state, "action": action.pk})
    return state


def approve(user, pk, request=None):
    """The customer pressed Approve. Runs the change once and returns the PendingAction."""
    action = _own(user, pk)
    if timezone.now() - action.created_at > LIFETIME:
        PendingAction.objects.filter(pk=action.pk, status="pending").update(status="expired")
        _mark(action, "expired")
        raise ServiceError("This request expired. Ask me again and I will prepare it.", code="expired")
    if not changes_on():
        raise ServiceError("Changes through the Agent are switched off for now.", code="changes_off")
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
    Message.objects.create(conversation=action.conversation, sender=SenderType.AI, body=(result if state == "done" else f"That did not work: {result}"))
    audit.record("assistant.change_done" if state == "done" else "assistant.change_failed", actor=user, target=action, metadata={"tool": action.tool, "result": result[:200]}, request=request)
    return action


def cancel(user, pk, request=None):
    action = _own(user, pk)
    if not PendingAction.objects.filter(pk=action.pk, status="pending").update(status="cancelled"):
        raise ServiceError("This request was already handled.", code="handled")
    _mark(action, "cancelled")
    Message.objects.create(conversation=action.conversation, sender=SenderType.AI, body="Cancelled. Nothing was changed.")
    audit.record("assistant.change_cancelled", actor=user, target=action, metadata={"tool": action.tool}, request=request)
    action.refresh_from_db()
    return action
