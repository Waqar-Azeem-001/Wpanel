"""
The demo provider's email service: a JSON file per account that behaves like a shared-hosting mail service - the
account's own domain only, plan limits, quotas, forwarders that cannot loop, autoresponders, routing, spam settings,
passwords kept only as hashes, and a small sample inbox per mailbox so the webmail screen has something to show.
No mail is ever sent or received. A real provider (the cPanel adapter) implements the same
``EmailCapability`` contract.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from .base import HostingError
from .capabilities import (MAIL_FOLDERS, MAIL_ROUTING_MODES, MAIL_VIRTUAL_FOLDERS, SPAM_LEVELS, MailProfile, AutoresponderInfo, EmailLimits, ForwarderInfo, MailboxInfo,
                           MessageInfo, SpamSettings)
from .demo_databases import check_password

MAX_MAILBOXES = 10
MAX_FORWARDERS = 20
MAX_RECIPIENTS = 5
MAX_STORED_MESSAGES = 300
MAX_HTML = 60_000
DEFAULT_QUOTA_MB = 1024
MAX_QUOTA_MB = 5120
LOCAL_RE = re.compile(r"^[a-z0-9]([a-z0-9._-]{0,38}[a-z0-9])?$")

SAMPLE_MESSAGES = [
    ("Welcome to your new mailbox", "support@example.com",
     "Your mailbox is ready.\n\nYou can read and send mail with any mail app, or from the webmail link in your control panel."),
    ("Your invoice is ready", "billing@example.com",
     "A new invoice has been issued for your hosting plan. You can view and pay it from your client area."),
    ("Tip: set up your email on your phone", "tips@example.com",
     "Use your full address as the user name and the password you chose when creating the mailbox."),
]


def _now():
    return datetime.now(timezone.utc)


class DemoEmail:
    def __init__(self, directory, domain):
        self.file = Path(directory) / "email.json"
        self.domain = (domain or "").lower()

    # --- storage --------------------------------------------------------------------------------------------------

    def _load(self):
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("mailboxes", {})
        data.setdefault("forwarders", [])
        data.setdefault("autoresponders", {})
        data.setdefault("routing", "local")
        data.setdefault("spam", {"enabled": True, "level": "medium", "delete_spam": False})
        data.setdefault("messages", {})
        data.setdefault("profiles", {})
        return data

    def _save(self, data):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(data), encoding="utf-8")

    def _address(self, typed, what="address"):
        typed = (typed or "").strip().lower()
        local, _, domain = typed.partition("@")
        if not domain:
            local, domain = typed, self.domain  # a bare name means "at this account's domain"
        if domain != self.domain:
            raise HostingError(f"Only addresses at {self.domain} can be used here.")
        if not LOCAL_RE.match(local) or ".." in local:
            raise HostingError(f"The {what} can use letters, numbers, dots, hyphens and underscores, and must start and "
                               "end with a letter or number.")
        return f"{local}@{domain}"

    def _mailbox(self, data, address):
        address = self._address(address)
        if address not in data["mailboxes"]:
            raise HostingError("That mailbox does not exist.")
        return address

    # --- reading --------------------------------------------------------------------------------------------------

    def limits(self):
        return EmailLimits(domain=self.domain, max_mailboxes=MAX_MAILBOXES, max_forwarders=MAX_FORWARDERS,
                           default_quota_mb=DEFAULT_QUOTA_MB, max_quota_mb=MAX_QUOTA_MB, password_min_length=12)

    def _used_mb(self, data, address):
        # sample figure: grows a little with the messages in the mailbox
        return round((sum(map(ord, address)) % 40) / 10 + 0.05 * len(data["messages"].get(address, [])), 2)

    def mailboxes(self):
        data = self._load()
        return [MailboxInfo(address=a, quota_mb=m["quota_mb"], used_mb=self._used_mb(data, a))
                for a, m in sorted(data["mailboxes"].items())]

    def forwarders(self):
        return [ForwarderInfo(source=f["source"], destination=f["destination"])
                for f in sorted(self._load()["forwarders"], key=lambda f: (f["source"], f["destination"]))]

    def autoresponders(self):
        return [AutoresponderInfo(address=a, subject=r["subject"], body=r["body"], enabled=r["enabled"])
                for a, r in sorted(self._load()["autoresponders"].items())]

    def routing(self):
        return self._load()["routing"]

    def spam(self):
        return SpamSettings(**self._load()["spam"])

    # --- mailboxes ------------------------------------------------------------------------------------------------

    def create_mailbox(self, address, password, quota_mb):
        data = self._load()
        address = self._address(address)
        if address in data["mailboxes"]:
            raise HostingError("That mailbox already exists.")
        if len(data["mailboxes"]) >= MAX_MAILBOXES:
            raise HostingError(f"Your plan allows {MAX_MAILBOXES} mailboxes. Delete one or upgrade your plan.")
        check_password(password)
        quota = self._quota(quota_mb)
        data["mailboxes"][address] = {"password_hash": make_password(password), "quota_mb": quota,
                                      "created": _now().isoformat()}
        data["messages"][address] = [
            {"id": f"m{i + 1}", "folder": "inbox", "sender": sender, "to": address, "subject": subject, "body": body,
             "unread": i == 0, "received_at": (_now() - timedelta(days=i, hours=i * 3)).isoformat()}
            for i, (subject, sender, body) in enumerate(SAMPLE_MESSAGES)]
        self._save(data)

    def _quota(self, quota_mb):
        if quota_mb in (None, ""):
            return DEFAULT_QUOTA_MB
        try:
            quota = int(quota_mb)
        except (TypeError, ValueError):
            raise HostingError("The quota must be a number of megabytes.")
        if not 10 <= quota <= MAX_QUOTA_MB:
            raise HostingError(f"The quota must be between 10 and {MAX_QUOTA_MB} MB.")
        return quota

    def delete_mailbox(self, address):
        data = self._load()
        address = self._mailbox(data, address)
        del data["mailboxes"][address]
        data["messages"].pop(address, None)
        data["autoresponders"].pop(address, None)
        data["forwarders"] = [f for f in data["forwarders"] if f["source"] != address]
        self._save(data)

    def set_password(self, address, password):
        data = self._load()
        address = self._mailbox(data, address)
        check_password(password)
        data["mailboxes"][address]["password_hash"] = make_password(password)
        self._save(data)

    def set_quota(self, address, quota_mb):
        data = self._load()
        address = self._mailbox(data, address)
        quota = self._quota(quota_mb)
        if quota < self._used_mb(data, address):
            raise HostingError("That is less than the mailbox already uses. Choose a larger quota.")
        data["mailboxes"][address]["quota_mb"] = quota
        self._save(data)

    def password_matches(self, address, password):
        """For tests only: proves the password is checked against a hash, not kept in the clear."""
        from django.contrib.auth.hashers import check_password as _check

        box = self._load()["mailboxes"].get(address)
        return bool(box) and _check(password, box["password_hash"])

    # --- forwarders -----------------------------------------------------------------------------------------------

    def create_forwarder(self, source, destination):
        data = self._load()
        source = self._address(source, "source address")
        destination = (destination or "").strip().lower()
        try:
            validate_email(destination)
        except ValidationError:
            raise HostingError("Enter a valid email address to forward to.")
        if destination == source:
            raise HostingError("A forwarder cannot send mail to its own address.")
        pair = {"source": source, "destination": destination}
        if pair in data["forwarders"]:
            raise HostingError("That forwarder already exists.")
        if len(data["forwarders"]) >= MAX_FORWARDERS:
            raise HostingError(f"Your plan allows {MAX_FORWARDERS} forwarders.")
        if self._loops(data["forwarders"] + [pair], source):
            raise HostingError("That would send mail round in a circle.")
        data["forwarders"].append(pair)
        self._save(data)

    @staticmethod
    def _loops(forwarders, start):
        seen, frontier = set(), [start]
        while frontier:
            current = frontier.pop()
            for f in forwarders:
                if f["source"] == current:
                    if f["destination"] == start:
                        return True
                    if f["destination"] not in seen:
                        seen.add(f["destination"])
                        frontier.append(f["destination"])
        return False

    def delete_forwarder(self, source, destination):
        data = self._load()
        pair = {"source": (source or "").strip().lower(), "destination": (destination or "").strip().lower()}
        if pair not in data["forwarders"]:
            raise HostingError("That forwarder does not exist.")
        data["forwarders"].remove(pair)
        self._save(data)

    # --- autoresponders, routing, spam ----------------------------------------------------------------------------

    def set_autoresponder(self, address, subject, body, enabled):
        data = self._load()
        address = self._mailbox(data, address)
        subject, body = (subject or "").strip(), (body or "").strip()
        if not subject or not body:
            raise HostingError("An automatic reply needs a subject and a message.")
        if len(subject) > 120 or len(body) > 2000:
            raise HostingError("That reply is too long (subject up to 120 characters, message up to 2000).")
        data["autoresponders"][address] = {"subject": subject, "body": body, "enabled": bool(enabled)}
        self._save(data)

    def delete_autoresponder(self, address):
        data = self._load()
        address = self._mailbox(data, address)
        if address not in data["autoresponders"]:
            raise HostingError("That mailbox has no automatic reply.")
        del data["autoresponders"][address]
        self._save(data)

    def set_routing(self, mode):
        if mode not in MAIL_ROUTING_MODES:
            raise HostingError("Choose where your mail is delivered.")
        data = self._load()
        data["routing"] = mode
        self._save(data)

    def set_spam(self, settings):
        if settings.level not in SPAM_LEVELS:
            raise HostingError("Choose low, medium or high.")
        data = self._load()
        data["spam"] = {"enabled": bool(settings.enabled), "level": settings.level, "delete_spam": bool(settings.delete_spam)}
        self._save(data)

    # --- the mail viewer: folders, reading, sending, moving ---------------------------------------------------------

    @staticmethod
    def _info(m, with_body=False):
        return MessageInfo(message_id=m["id"], sender=m["sender"], subject=m["subject"], received_at=m["received_at"],
                           unread=m["unread"], body=m["body"] if with_body else "", to=m.get("to", ""),
                           folder=m.get("folder", "inbox"), snippet=" ".join(m["body"].split())[:100], starred=bool(m.get("starred")),
                           html=m.get("html", "") if with_body else "")

    @staticmethod
    def _folder(m):
        return m.get("folder", "inbox")  # messages saved before folders existed are in the inbox

    def folders(self, address):
        data = self._load()
        address = self._mailbox(data, address)
        counts = {f: [0, 0] for f in MAIL_FOLDERS + MAIL_VIRTUAL_FOLDERS}
        for m in data["messages"].get(address, []):
            keys = [self._folder(m)] + (["starred"] if m.get("starred") else [])
            for key in keys:
                box = counts.get(key)
                if box is not None:
                    box[0] += 1
                    box[1] += 1 if m["unread"] else 0
        return {f: (c[0], c[1]) for f, c in counts.items()}

    def messages(self, address, folder="inbox"):
        data = self._load()
        address = self._mailbox(data, address)
        if folder not in MAIL_FOLDERS + MAIL_VIRTUAL_FOLDERS:
            raise HostingError("That folder does not exist.")
        if folder == "starred":
            found = [m for m in data["messages"].get(address, []) if m.get("starred") and self._folder(m) != "trash"]
        else:
            found = [m for m in data["messages"].get(address, []) if self._folder(m) == folder]
        return [self._info(m) for m in sorted(found, key=lambda m: m["received_at"], reverse=True)]

    def read_message(self, address, message_id):
        data = self._load()
        address = self._mailbox(data, address)
        for m in data["messages"].get(address, []):
            if m["id"] == message_id:
                m["unread"] = False
                self._save(data)
                return self._info(m, with_body=True)
        raise HostingError("That message does not exist.")

    def move_message(self, address, message_id, folder):
        if folder not in ("inbox", "archive", "spam", "trash"):
            raise HostingError("A message can be moved to the inbox, archive, spam or trash.")
        data = self._load()
        address = self._mailbox(data, address)
        for m in data["messages"].get(address, []):
            if m["id"] == message_id:
                m["folder"] = folder
                self._save(data)
                return
        raise HostingError("That message does not exist.")

    def delete_message(self, address, message_id):
        data = self._load()
        address = self._mailbox(data, address)
        box = data["messages"].get(address, [])
        for m in box:
            if m["id"] == message_id:
                if self._folder(m) in ("trash", "drafts"):
                    box.remove(m)  # already in the trash, or a draft: gone for good
                else:
                    m["folder"] = "trash"
                self._save(data)
                return
        raise HostingError("That message does not exist.")

    def send_message(self, address, to, subject, body, html="", draft_id=""):
        data = self._load()
        address = self._mailbox(data, address)
        recipients = [r.strip().lower() for r in (to or "").replace(";", ",").split(",") if r.strip()]
        if not recipients:
            raise HostingError("Add at least one address to send to.")
        if len(recipients) > MAX_RECIPIENTS:
            raise HostingError(f"You can send to up to {MAX_RECIPIENTS} addresses at once.")
        for r in recipients:
            try:
                validate_email(r)
            except ValidationError:
                raise HostingError(f"'{r}' is not a valid email address.")
        subject, body = (subject or "").strip(), (body or "").strip()
        if not subject:
            raise HostingError("Add a subject.")
        if len(subject) > 200 or len(body) > 10000 or len(html or "") > MAX_HTML:
            raise HostingError("That message is too long (subject up to 200 characters, text up to 10000).")
        if not body and not html:
            raise HostingError("Write a message first.")
        now = _now().isoformat()
        stamp = _now().strftime("%Y%m%d%H%M%S%f")

        def put(box_address, folder, unread, n):
            box = data["messages"].setdefault(box_address, [])
            box.append({"id": f"s{stamp}{n}", "folder": folder, "sender": address, "to": ", ".join(recipients),
                        "subject": subject, "body": body, "html": html or "", "unread": unread, "received_at": now})
            del box[:-MAX_STORED_MESSAGES]  # keep the demo state small

        put(address, "sent", False, 0)
        if draft_id:
            data["messages"][address] = [m for m in data["messages"][address]
                                         if not (m["id"] == draft_id and self._folder(m) == "drafts")]
        for n, r in enumerate(recipients, start=1):
            if r in data["mailboxes"] and r != address:
                put(r, "inbox", True, n)  # mail between mailboxes of this account really arrives
        self._save(data)

    def save_draft(self, address, to, subject, body, html="", draft_id=""):
        data = self._load()
        address = self._mailbox(data, address)
        to, subject, body = (to or "").strip(), (subject or "").strip(), (body or "").strip()
        if not (to or subject or body or html):
            raise HostingError("There is nothing to save yet.")
        if len(to) > 500 or len(subject) > 200 or len(body) > 10000 or len(html or "") > MAX_HTML:
            raise HostingError("That draft is too long.")
        box = data["messages"].setdefault(address, [])
        if draft_id:
            box[:] = [m for m in box if not (m["id"] == draft_id and self._folder(m) == "drafts")]
        draft_id = f"d{_now().strftime('%Y%m%d%H%M%S%f')}"
        box.append({"id": draft_id, "folder": "drafts", "sender": address, "to": to, "subject": subject or "(no subject)",
                    "body": body, "html": html or "", "unread": False, "received_at": _now().isoformat()})
        del box[:-MAX_STORED_MESSAGES]
        self._save(data)
        return draft_id

    def set_starred(self, address, message_id, starred):
        data = self._load()
        address = self._mailbox(data, address)
        for m in data["messages"].get(address, []):
            if m["id"] == message_id:
                m["starred"] = bool(starred)
                self._save(data)
                return
        raise HostingError("That message does not exist.")

    def profile(self, address):
        data = self._load()
        address = self._mailbox(data, address)
        return MailProfile(**{**MailProfile().__dict__, **data["profiles"].get(address, {})})

    def set_profile(self, address, profile):
        data = self._load()
        address = self._mailbox(data, address)
        name, reply_to = (profile.display_name or "").strip(), (profile.reply_to or "").strip().lower()
        if len(name) > 80 or any(ord(c) < 32 for c in name):
            raise HostingError("The name can be up to 80 characters, without line breaks.")
        if reply_to:
            try:
                validate_email(reply_to)
            except ValidationError:
                raise HostingError("The reply-to address is not a valid email address.")
        if len(profile.signature_html or "") > 5000 or len(profile.signature_text or "") > 2000:
            raise HostingError("That signature is too long (about 2000 characters).")
        data["profiles"][address] = {"display_name": name, "reply_to": reply_to, "signature_html": profile.signature_html or "",
                                     "signature_text": profile.signature_text or "",
                                     "signature_new": bool(profile.signature_new), "signature_reply": bool(profile.signature_reply)}
        self._save(data)
