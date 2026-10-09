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
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import escape
from django.views.decorators.http import require_POST

from apps.core import portal
from apps.core.exceptions import ServiceError

from . import control, dashboard
from .adapters.capabilities import MAIL_ROUTING_MODES, SPAM_LEVELS, MailProfile, SpamSettings
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
    except ServiceError as exc:
        messages.error(request, exc.message)
        limits = None
        mailboxes = forwarders = []

    def optional(operation, default, *args):
        """A part some servers do not offer (automatic replies, routing, spam settings, webmail): left out of the page instead of failing it."""
        try:
            return _read(request, account, operation, *args)
        except ServiceError:
            return default

    responders = {r.address: r for r in optional("list_autoresponders", [])}
    routing = optional("get_mail_routing", "")
    spam = optional("get_spam_settings", None)
    webmail = bool(mailboxes) and optional("webmail_access", None, mailboxes[0].address) is not None
    rows = []
    for box in mailboxes:
        percent = round(box.used_mb * 100 / box.quota_mb) if box.quota_mb and box.used_mb is not None else None
        state = "danger" if percent is not None and percent >= 95 else "warning" if percent is not None and percent >= 80 else "ok"
        responder = responders.get(box.address)
        rows.append({"box": box, "percent": percent, "state": state, "responder": responder,
                     "webmail": reverse("hosting_customer:webmail", args=[account.pk]) + "?" + urlencode({"address": box.address})})
    return render(request, "hosting/customer/email.html", {
        "account": account, "sidebar": sidebar, "limits": limits, "rows": rows, "forwarders": forwarders,
        "routing": routing, "spam": spam, "has_webmail": webmail, "has_responders": bool(responders) or optional("list_autoresponders", None) is not None,
        "spam_levels": SPAM_LEVELS, "routing_modes": MAIL_ROUTING_MODES,
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
FOLDER_LABELS = {"inbox": ("Inbox", "bi-inbox"), "starred": ("Starred", "bi-star"), "drafts": ("Drafts", "bi-file-earmark-text"),
                 "sent": ("Sent", "bi-send"), "archive": ("Archive", "bi-archive"), "spam": ("Spam", "bi-shield-exclamation"),
                 "trash": ("Trash", "bi-trash3")}
MOVE_TARGETS = ("inbox", "archive", "spam", "trash")


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


def _signature_block(profile, *, for_reply):
    """The signature as HTML to put in the editor (below what the person will write), or an empty string."""
    wanted = profile.signature_reply if for_reply else profile.signature_new
    if not wanted:
        return ""
    sig = sanitize_html(profile.signature_html) or escape(profile.signature_text).replace(NEWLINE, "<br>")
    return f"<div>-- <br>{sig}</div>" if sig else ""


def _new_draft(profile, **extra):
    sig = _signature_block(profile, for_reply=False)
    return {"to": "", "subject": "", "body": "", "html": f"<br><br>{sig}" if sig else "", "draft_id": "", **extra}


def _reply_draft(original, profile):
    """A reply: to the sender, "Re: subject", the signature, then the original quoted underneath (escaped, line by line)."""
    when = _moment(original.received_at)[1]
    quoted = "<br>".join(escape(line) for line in _text_of(original).splitlines())
    subject = original.subject if original.subject.lower().startswith("re:") else f"Re: {original.subject}"
    sig = _signature_block(profile, for_reply=True)
    return {"to": original.sender, "subject": subject, "body": "", "draft_id": "", "reply_to_id": original.message_id,
            "html": f"<br><br>{sig}<br><blockquote>On {escape(when)}, {escape(original.sender)} wrote:<br>{quoted}</blockquote>"}


def _webmail(request, account, address, *, folder="inbox", message_id="", compose=None, reply=None, query=""):
    """
    The mail app, as its own full-screen page (no website menu or footer): folders, the message list, the reading pane (with
    an inline reply) and a compose window. A provider with Roundcube sends the person there instead (one-time https address).
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
    profile = _read(request, account, "get_mail_profile", address)
    box = next((b for b in _read(request, account, "list_mailboxes") if b.address == address), None)
    usage = None
    if box and box.quota_mb:
        usage = {"used": box.used_mb, "quota": box.quota_mb, "percent": min(100, round((box.used_mb or 0) * 100 / box.quota_mb))}
    items = []
    for m in listed:
        short, _long = _moment(m.received_at)
        who = (m.to if folder in ("sent", "drafts") else m.sender) or "-"
        link = _webmail_url(account, address=address, folder=folder, q=query,
                            **({"draft": m.message_id} if folder == "drafts" else {"message": m.message_id}))
        items.append({"m": m, "url": link, "current": bool(message and message.message_id == m.message_id), "who": who,
                      "when": short, "initial": who[:1].upper(), "tone": sum(map(ord, who)) % 6})
    context = {
        "account": account, "address": address, "folder": folder, "folder_label": FOLDER_LABELS[folder][0],
        "message": message, "compose": compose, "reply": reply, "query": query, "usage": usage, "back_url": _back(account),
        "profile": profile, "from_label": f"{profile.display_name} <{address}>" if profile.display_name else address,
        "folders": [{"key": key, "label": label, "icon": icon, "total": counts.get(key, (0, 0))[0],
                     "unread": counts.get(key, (0, 0))[1], "url": _webmail_url(account, address=address, folder=key),
                     "active": key == folder} for key, (label, icon) in FOLDER_LABELS.items()],
        "items": items, "total_unread": counts["inbox"][1],
        "compose_url": _webmail_url(account, address=address, folder=folder, compose=1),
        "action_url": reverse("hosting_customer:webmail_action", args=[account.pk]),
        "settings_url": reverse("hosting_customer:webmail_settings", args=[account.pk]) + "?" + urlencode({"address": address}),
        "logout_url": reverse("accounts:logout"),
        "list_url": _webmail_url(account, address=address, folder=folder, q=query),
        "reply_url": "", "message_html": "", "sender_initial": "", "message_when": "", "sender_tone": 0,
    }
    context["reply_open"] = reply is not None
    context["reply_form"] = reply if reply is not None else (_reply_draft(message, profile) if message else None)
    if message:
        context["reply_url"] = _webmail_url(account, address=address, folder=folder, reply=message.message_id)
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
    compose = reply = None
    message_id = get.get("message", "")
    try:
        if get.get("reply"):  # an inline reply, under the message it answers
            original = _read(request, account, "read_message", address, get["reply"])
            reply = _reply_draft(original, _read(request, account, "get_mail_profile", address))
            message_id = get["reply"]
        elif get.get("draft"):  # a saved draft opens in the compose window
            draft = _read(request, account, "read_message", address, get["draft"])
            compose = {"to": draft.to, "subject": "" if draft.subject == "(no subject)" else draft.subject, "body": "",
                       "html": sanitize_html(draft.html) or escape(draft.body).replace(NEWLINE, "<br>"), "draft_id": draft.message_id}
        elif get.get("compose"):
            compose = _new_draft(_read(request, account, "get_mail_profile", address), to=get.get("to", ""),
                                 subject=get.get("subject", ""))
        return _webmail(request, account, address, folder=get.get("folder", "inbox"), message_id=message_id,
                        compose=compose, reply=reply, query=get.get("q", ""))
    except ServiceError as exc:
        messages.error(request, exc.message)
        return redirect(_back(account))


def _clean_post_body(post):
    clean = sanitize_html(post.get("html", ""))
    return clean, (post.get("body", "").strip() or html_to_text(clean))


@require_POST
@login_required
def my_hosting_webmail_action(request, pk):
    """Send, save a draft, star, delete or move one message, then return to the folder the person was in."""
    account = _own_account(request, pk)
    post = request.POST
    address, folder, action = post.get("address", ""), post.get("folder", "inbox"), post.get("action", "")
    quiet = request.headers.get("X-Requested-With") == "fetch"  # an automatic save: no page, no message
    try:
        if action in ("send", "reply"):
            clean, text = _clean_post_body(post)
            try:
                _run(request, account, "send_message", address, post.get("to", ""), post.get("subject", ""), text, clean,
                     post.get("draft_id", ""), metadata={"address": address, "recipients": len(post.get("to", "").split(","))})
            except ServiceError as exc:
                messages.error(request, exc.message)  # keep what was typed so it can be fixed
                draft = {"to": post.get("to", ""), "subject": post.get("subject", ""), "body": "",
                         "html": clean or escape(text), "draft_id": post.get("draft_id", "")}
                if action == "reply":
                    draft["reply_to_id"] = post.get("reply_to_id", "")
                    return _webmail(request, account, address, folder=folder, message_id=post.get("reply_to_id", ""), reply=draft)
                return _webmail(request, account, address, folder=folder, compose=draft)
            messages.success(request, "Message sent.")
            folder = "sent" if action == "send" else folder
        elif action == "save_draft":
            clean, text = _clean_post_body(post)
            try:
                draft_id = _run(request, account, "save_draft", address, post.get("to", ""), post.get("subject", ""), text, clean,
                                post.get("draft_id", ""), metadata={"address": address})
            except ServiceError as exc:
                if quiet:
                    return HttpResponse(status=204)
                messages.error(request, exc.message)
                return _webmail(request, account, address, folder=folder, compose={
                    "to": post.get("to", ""), "subject": post.get("subject", ""), "body": "", "html": clean,
                    "draft_id": post.get("draft_id", "")})
            if quiet:
                return JsonResponse({"draft_id": draft_id})
            messages.success(request, "Draft saved.")
            folder = "drafts"
        elif action == "star":
            starred = post.get("starred") == "1"
            _run(request, account, "set_message_starred", address, post.get("message", ""), starred, metadata={"address": address})
            messages.success(request, "Starred." if starred else "Star removed.")
            return redirect(_webmail_url(account, address=address, folder=folder, message=post.get("message", "")))
        elif action == "delete":
            _run(request, account, "delete_message", address, post.get("message", ""), metadata={"address": address})
            if quiet:
                return HttpResponse(status=204)
            messages.success(request, "Deleted." if folder in ("trash", "drafts") else "Moved to the trash.")
        elif action == "move":
            target = post.get("to_folder", "")
            if target not in MOVE_TARGETS:
                raise ServiceError("A message can be moved to the inbox, archive, spam or trash.", code="bad_folder")
            _run(request, account, "move_message", address, post.get("message", ""), target,
                 metadata={"address": address, "folder": target})
            messages.success(request, "Moved to " + FOLDER_LABELS[target][0] + ".")
        else:
            raise ServiceError("That action is not available.", code="unknown_action")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect(_webmail_url(account, address=address, folder=folder))


@login_required
def my_hosting_webmail_settings(request, pk):
    """The mailbox's own settings: name, reply-to, signature, automatic reply and password."""
    account = _own_account(request, pk)
    if not _supports(account):
        return redirect(_back(account))
    address = request.GET.get("address") or request.POST.get("address", "")
    here = reverse("hosting_customer:webmail_settings", args=[account.pk]) + "?" + urlencode({"address": address})
    if request.method == "POST":
        post, action = request.POST, request.POST.get("action", "")
        try:
            if action == "profile":
                clean, text = _clean_post_body({"html": post.get("signature_html", ""), "body": post.get("signature_text", "")})
                profile = MailProfile(display_name=post.get("display_name", ""), reply_to=post.get("reply_to", ""),
                                      signature_html=clean, signature_text=text, signature_new=bool(post.get("signature_new")),
                                      signature_reply=bool(post.get("signature_reply")))
                _run(request, account, "set_mail_profile", address, profile, metadata={"address": address, "signature": bool(text)})
                messages.success(request, "Mail settings saved.")
            elif action == "password":
                _run(request, account, "set_mailbox_password", address, post.get("password", ""), metadata={"address": address})
                messages.success(request, "Password changed. Update your mail apps with the new password.")
            elif action == "autoresponder":
                if post.get("remove"):
                    _run(request, account, "delete_autoresponder", address, metadata={"address": address})
                    messages.success(request, "Automatic reply removed.")
                else:
                    _run(request, account, "set_autoresponder", address, post.get("subject", ""), post.get("body", ""),
                         bool(post.get("enabled")), metadata={"address": address, "enabled": bool(post.get("enabled"))})
                    messages.success(request, "Automatic reply saved.")
            else:
                raise ServiceError("That action is not available.", code="unknown_action")
        except ServiceError as exc:
            messages.error(request, exc.message)
        return redirect(here)
    try:
        profile = _read(request, account, "get_mail_profile", address)
        responder = next((r for r in _read(request, account, "list_autoresponders") if r.address == address), None)
        limits = _read(request, account, "email_limits")
        box = next((b for b in _read(request, account, "list_mailboxes") if b.address == address), None)
    except ServiceError as exc:
        messages.error(request, exc.message)
        return redirect(_back(account))
    if box is None:
        messages.error(request, "That mailbox does not exist.")
        return redirect(_back(account))
    return render(request, "hosting/customer/email_settings.html", {
        "account": account, "address": address, "profile": profile, "responder": responder, "box": box, "limits": limits,
        "form_url": here, "back_url": _back(account), "mail_url": _webmail_url(account, address=address),
        "logout_url": reverse("accounts:logout"), "settings_url": here,
        "usage_percent": min(100, round((box.used_mb or 0) * 100 / box.quota_mb)) if box.quota_mb else None})
