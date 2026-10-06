"""
The customer's Email screen: mailboxes, forwarders, automatic replies, mail routing, spam settings and webmail. Like the
other management screens, every operation goes through the hosting-control gateway (ownership, the allowlist, the
live-account check and the audit trail are shared) and the provider enforces the plan limits, the address rules and
the password rules. Passwords are handed straight to the provider: never stored, logged, shown or put in an audit record.
"""
from datetime import datetime
from urllib.parse import urlencode, urlsplit

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import escape
from django.views.decorators.http import require_POST

from apps.core import portal
from apps.core.exceptions import ServiceError

from . import control, dashboard
from .adapters.capabilities import MAIL_ROUTING_MODES, SPAM_LEVELS, SpamSettings
from .mailhtml import html_to_text, sanitize_html
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


NEWLINE = chr(10)
FOLDER_LABELS = {"inbox": ("Inbox", "bi-inbox"), "sent": ("Sent", "bi-send"), "spam": ("Spam", "bi-shield-exclamation"),
                 "trash": ("Trash", "bi-trash3")}


def _webmail_url(account, **query):
    query = {k: v for k, v in query.items() if v not in ("", None)}
    return reverse("hosting_customer:webmail", args=[account.pk]) + ("?" + urlencode(query) if query else "")


def _moment(value):
    """(short, long) ways to write a message time: a time of day for today, otherwise the date."""
    try:
        when = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return "", ""
    now = datetime.now(when.tzinfo)
    short = when.strftime("%H:%M") if when.date() == now.date() else when.strftime("%d %b")
    return short, when.strftime("%d %b %Y, %H:%M")


def _text_of(message):
    return message.body or html_to_text(message.html)


def _reply_draft(original):
    """The start of a reply: to the sender, "Re: subject", and the original quoted underneath (escaped, line by line)."""
    when = _moment(original.received_at)[1]
    quoted = "<br>".join(escape(line) for line in _text_of(original).splitlines())
    subject = original.subject if original.subject.lower().startswith("re:") else f"Re: {original.subject}"
    return {"to": original.sender, "subject": subject, "body": "",
            "html": f"<br><br><blockquote>On {escape(when)}, {escape(original.sender)} wrote:<br>{quoted}</blockquote>"}


def _webmail(request, account, address, *, folder="inbox", message_id="", compose=None, query=""):
    """
    The mail app, as its own full-screen page (no website menu or footer): folders, the message list, the reading pane and a
    compose window. A provider with Roundcube sends the person there instead, with a one-time https address.
    """
    access = _read(request, account, "webmail_access", address)
    if access.kind == "external":
        target = urlsplit(access.url)
        if target.scheme != "https" or not target.netloc:
            raise ServiceError("The webmail address was not valid.", code="bad_webmail_url")
        return redirect(access.url)
    if folder not in FOLDER_LABELS:
        folder = "inbox"
    message = _read(request, account, "read_message", address, message_id) if message_id else None
    counts = _read(request, account, "message_folders", address)  # after reading: opening a message marks it read
    listed = _read(request, account, "list_messages", address, folder)
    needle = query.strip().lower()
    if needle:
        listed = [m for m in listed if needle in " ".join([m.subject, m.sender, m.to, m.snippet]).lower()]
    box = next((b for b in _read(request, account, "list_mailboxes") if b.address == address), None)
    usage = None
    if box and box.quota_mb:
        percent = min(100, round((box.used_mb or 0) * 100 / box.quota_mb))
        usage = {"used": box.used_mb, "quota": box.quota_mb, "percent": percent}
    items = []
    for m in listed:
        short, _long = _moment(m.received_at)
        who = m.to if folder == "sent" else m.sender
        items.append({"m": m, "url": _webmail_url(account, address=address, folder=folder, message=m.message_id, q=query),
                      "current": bool(message and message.message_id == m.message_id), "who": who or "-", "when": short,
                      "initial": (who or "?")[:1].upper(), "tone": sum(map(ord, who or "?")) % 6})
    context = {
        "account": account, "address": address, "folder": folder, "folder_label": FOLDER_LABELS[folder][0],
        "message": message, "compose": compose, "query": query, "usage": usage, "back_url": _back(account),
        "folders": [{"key": key, "label": label, "icon": icon, "total": counts[key][0], "unread": counts[key][1],
                     "url": _webmail_url(account, address=address, folder=key), "active": key == folder}
                    for key, (label, icon) in FOLDER_LABELS.items()],
        "items": items, "total_unread": counts["inbox"][1],
        "compose_url": _webmail_url(account, address=address, folder=folder, compose=1),
        "action_url": reverse("hosting_customer:webmail_action", args=[account.pk]),
        "list_url": _webmail_url(account, address=address, folder=folder, q=query),
        "reply_url": "", "message_html": "", "sender_initial": "", "message_when": "",
    }
    if message:
        context["reply_url"] = _webmail_url(account, address=address, folder=folder, compose=1, reply=message.message_id)
        context["message_html"] = sanitize_html(message.html) if message.html else ""
        context["sender_initial"] = (message.sender or "?")[:1].upper()
        context["sender_tone"] = sum(map(ord, message.sender or "?")) % 6
        context["message_when"] = _moment(message.received_at)[1]
    return render(request, "hosting/customer/email_webmail.html", context)


@login_required
def my_hosting_webmail(request, pk):
    account = _own_account(request, pk)
    if not _supports(account):
        return redirect(_back(account))
    get = request.GET
    address = get.get("address", "")
    compose = None
    if get.get("compose"):
        compose = {"to": get.get("to", ""), "subject": get.get("subject", ""), "body": "", "html": ""}
        if get.get("reply"):
            try:
                compose = _reply_draft(_read(request, account, "read_message", address, get["reply"]))
            except ServiceError:
                pass  # an unknown message: a blank new message
    try:
        return _webmail(request, account, address, folder=get.get("folder", "inbox"),
                        message_id="" if compose else get.get("message", ""), compose=compose, query=get.get("q", ""))
    except ServiceError as exc:
        messages.error(request, exc.message)
        return redirect(_back(account))


@require_POST
@login_required
def my_hosting_webmail_action(request, pk):
    """Send, delete or move one message, then return to the folder the person was in."""
    account = _own_account(request, pk)
    post = request.POST
    address, folder, action = post.get("address", ""), post.get("folder", "inbox"), post.get("action", "")
    try:
        if action == "send":
            clean = sanitize_html(post.get("html", ""))
            text = post.get("body", "").strip() or html_to_text(clean)
            try:
                _run(request, account, "send_message", address, post.get("to", ""), post.get("subject", ""), text, clean,
                     metadata={"address": address, "recipients": len(post.get("to", "").split(","))})
            except ServiceError as exc:
                messages.error(request, exc.message)  # keep what was typed so it can be fixed
                return _webmail(request, account, address, folder=folder, compose={
                    "to": post.get("to", ""), "subject": post.get("subject", ""), "body": "", "html": clean or escape(text)})
            messages.success(request, "Message sent.")
            folder = "sent"
        elif action == "delete":
            _run(request, account, "delete_message", address, post.get("message", ""), metadata={"address": address})
            messages.success(request, "Deleted." if folder == "trash" else "Moved to the trash.")
        elif action == "move":
            target = post.get("to_folder", "")
            _run(request, account, "move_message", address, post.get("message", ""), target,
                 metadata={"address": address, "folder": target})
            messages.success(request, "Moved to " + target + ".")
        else:
            raise ServiceError("That action is not available.", code="unknown_action")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect(_webmail_url(account, address=address, folder=folder))
