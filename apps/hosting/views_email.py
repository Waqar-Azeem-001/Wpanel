"""
The customer's Email screen: mailboxes, forwarders, automatic replies, mail routing, spam settings and webmail. Like the
other management screens, every operation goes through the hosting-control gateway (ownership, the allowlist, the
live-account check and the audit trail are shared) and the provider enforces the plan limits, the address rules and
the password rules. Passwords are handed straight to the provider: never stored, logged, shown or put in an audit record.
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
from .adapters.capabilities import MAIL_ROUTING_MODES, SPAM_LEVELS, SpamSettings
from .views import _own_account


def _back(account):
    return reverse("hosting_customer:email", args=[account.pk])


def _supports(account):
    try:
        return "email" in control.capabilities_for(account)
    except ServiceError:
        return False


def _run(request, account, operation, *args, metadata=None, **kwargs):
    return control.perform(request.user, account, "email", operation, *args, request=request, audit_metadata=metadata,
                           **kwargs)


def _read(request, account, operation, *args):
    return control.perform(request.user, account, "email", operation, *args)


@login_required
def my_hosting_email(request, pk):
    account = _own_account(request, pk)
    sidebar = portal.service_sidebar(request, account)
    if not _supports(account):
        return render(request, "hosting/customer/section.html", {
            "account": account, "sidebar": sidebar, "section": dashboard.section_page(request.user, account, "email")})
    try:
        limits = _read(request, account, "email_limits")
        mailboxes = _read(request, account, "list_mailboxes")
        forwarders = _read(request, account, "list_forwarders")
        responders = {r.address: r for r in _read(request, account, "list_autoresponders")}
        routing = _read(request, account, "get_mail_routing")
        spam = _read(request, account, "get_spam_settings")
    except ServiceError as exc:
        messages.error(request, exc.message)
        limits = None
        mailboxes = forwarders = []
        responders, routing, spam = {}, "local", None
    rows = []
    for box in mailboxes:
        percent = round(box.used_mb * 100 / box.quota_mb) if box.quota_mb and box.used_mb is not None else None
        state = "danger" if percent is not None and percent >= 95 else "warning" if percent is not None and percent >= 80 else "ok"
        responder = responders.get(box.address)
        rows.append({"box": box, "percent": percent, "state": state, "responder": responder,
                     "webmail": reverse("hosting_customer:webmail", args=[account.pk]) + "?" + urlencode({"address": box.address})})
    return render(request, "hosting/customer/email.html", {
        "account": account, "sidebar": sidebar, "limits": limits, "rows": rows, "forwarders": forwarders,
        "routing": routing, "spam": spam, "spam_levels": SPAM_LEVELS, "routing_modes": MAIL_ROUTING_MODES,
        "mailbox_full": bool(limits and limits.max_mailboxes is not None and len(mailboxes) >= limits.max_mailboxes),
        "forwarder_full": bool(limits and limits.max_forwarders is not None and len(forwarders) >= limits.max_forwarders),
        "quota_choices": [c for c in (256, 512, 1024, 2048, 5120) if limits and (limits.max_quota_mb is None or c <= limits.max_quota_mb)]})


def _address(request, account, local):
    domain = _read(request, account, "email_limits").domain
    local = (local or "").strip().lower()
    return local if "@" in local else f"{local}@{domain}"


@require_POST
@login_required
def my_hosting_email_action(request, pk):
    account = _own_account(request, pk)
    post = request.POST
    action = post.get("action", "")
    try:
        if action == "create_mailbox":
            address = _address(request, account, post.get("local"))
            quota = post.get("quota") or None
            _run(request, account, "create_mailbox", address, post.get("password", ""), quota_mb=quota,
                 metadata={"address": address, "quota_mb": quota})  # the password is deliberately not recorded anywhere
            messages.success(request, f"Mailbox {address} created.")
        elif action == "delete_mailbox":
            address = post.get("address", "")
            _run(request, account, "delete_mailbox", address, metadata={"address": address})
            messages.success(request, "Mailbox deleted, with its mail, forwarders and automatic reply.")
        elif action == "set_password":
            address = post.get("address", "")
            _run(request, account, "set_mailbox_password", address, post.get("password", ""), metadata={"address": address})
            messages.success(request, "Password changed. Update your mail apps with the new password.")
        elif action == "set_quota":
            address = post.get("address", "")
            _run(request, account, "set_mailbox_quota", address, post.get("quota") or None,
                 metadata={"address": address, "quota_mb": post.get("quota")})
            messages.success(request, "Quota changed.")
        elif action == "create_forwarder":
            source = _address(request, account, post.get("source"))
            destination = post.get("destination", "").strip()
            _run(request, account, "create_forwarder", source, destination,
                 metadata={"source": source, "destination": destination})
            messages.success(request, f"Mail for {source} now goes to {destination}.")
        elif action == "delete_forwarder":
            source, destination = post.get("source", ""), post.get("destination", "")
            _run(request, account, "delete_forwarder", source, destination,
                 metadata={"source": source, "destination": destination})
            messages.success(request, "Forwarder removed.")
        elif action == "set_autoresponder":
            address = post.get("address", "")
            _run(request, account, "set_autoresponder", address, post.get("subject", ""), post.get("body", ""),
                 bool(post.get("enabled")), metadata={"address": address, "enabled": bool(post.get("enabled"))})
            messages.success(request, "Automatic reply saved.")
        elif action == "delete_autoresponder":
            address = post.get("address", "")
            _run(request, account, "delete_autoresponder", address, metadata={"address": address})
            messages.success(request, "Automatic reply removed.")
        elif action == "set_routing":
            mode = post.get("mode", "")
            _run(request, account, "set_mail_routing", mode, metadata={"mode": mode})
            messages.success(request, "Mail routing saved.")
        elif action == "set_spam":
            settings = SpamSettings(enabled=bool(post.get("enabled")), level=post.get("level", ""),
                                    delete_spam=bool(post.get("delete_spam")))
            _run(request, account, "set_spam_settings", settings,
                 metadata={"enabled": settings.enabled, "level": settings.level, "delete_spam": settings.delete_spam})
            messages.success(request, "Spam settings saved.")
        else:
            raise ServiceError("That action is not available.", code="unknown_action")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect(_back(account))


@login_required
def my_hosting_webmail(request, pk):
    """Webmail: a provider with Roundcube (or similar) sends the person there with a one-time https address; otherwise
    WHPanel's own simple inbox viewer shows the mailbox."""
    account = _own_account(request, pk)
    if not _supports(account):
        return redirect(_back(account))
    address = request.GET.get("address", "")
    try:
        access = _read(request, account, "webmail_access", address)
        if access.kind == "external":
            target = urlsplit(access.url)
            if target.scheme != "https" or not target.netloc:
                raise ServiceError("The webmail address was not valid.", code="bad_webmail_url")
            return redirect(access.url)
        inbox = _read(request, account, "list_messages", address)
        message = None
        chosen = request.GET.get("message", "")
        if chosen:
            message = _read(request, account, "read_message", address, chosen)
            inbox = _read(request, account, "list_messages", address)  # reading marks it as read
    except ServiceError as exc:
        messages.error(request, exc.message)
        return redirect(_back(account))
    base = reverse("hosting_customer:webmail", args=[account.pk])
    return render(request, "hosting/customer/email_webmail.html", {
        "account": account, "sidebar": portal.service_sidebar(request, account), "address": address, "inbox": [
            (m, base + "?" + urlencode({"address": address, "message": m.message_id})) for m in inbox],
        "message": message, "back_url": _back(account), "unread": sum(1 for m in inbox if m.unread)})
