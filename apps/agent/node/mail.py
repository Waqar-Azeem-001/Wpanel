"""
What a MAIL NODE does (N7, ADR 06): Postfix receives and sends, Dovecot stores and serves, Rspamd filters and signs (DKIM). A mail node is a separate server from the web nodes, so
a hacked website or a spammer on one web node cannot spoil the reputation of the address mail leaves from.

This module keeps each mail domain's state in a small file, renders the whole configuration from those states (Postfix maps, the Dovecot password file, Rspamd spam and DKIM
settings, per-mailbox Sieve auto-replies), reloads the services only after the maps are rebuilt, and reads/writes the mailboxes themselves as Maildir folders. Only the Maildir
part works on real folders; everything else goes through ``system``.

Never run on a real mail node: the generated Postfix, Dovecot and Rspamd settings are written from their documentation and are untested. See docs/n7-native-mail.md.
"""
import base64
import json
import re
import time
import uuid
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .. import catalog
from .crypt512 import hash_password
from .system import SystemError_
from .website import HandlerError

import os

FLAG_SEP = ":2," if os.name == "posix" else ";2,"  # Maildir puts a colon before the flags; Windows file names cannot, so the (test-only) Windows stand-in uses a semicolon
SELECTOR = "whp"
VMAIL_UID = 5000
FOLDER_DIRS = {"inbox": "", "drafts": ".Drafts", "sent": ".Sent", "archive": ".Archive", "spam": ".Junk", "trash": ".Trash"}
SPAM_ACTIONS = {"low": (10, 20), "medium": (6, 15), "high": (4, 10)}  # (mark as spam, reject) scores
DEFAULT_LIMITS = {"max_mailboxes": 10, "max_forwarders": 10, "max_quota_mb": 5120, "emails_per_hour": 200}
STATE = "/var/lib/whp/mail/{domain}.json"
EMAIL = re.compile(catalog.EMAIL.pattern)


def _sieve(text):
    return text.replace("\\", "\\\\").replace('"', '\\"')


class Mail:
    def __init__(self, system, mail_root, now=time.time):
        self.s, self.mail_root, self.now = system, Path(mail_root), now

    # --- state ----------------------------------------------------------------------------------------------------------------------------
    def _state(self, domain):
        raw = self.s.read(STATE.format(domain=domain))
        return json.loads(raw) if raw else None

    def _need(self, account, domain):
        state = self._state(domain)
        if state is None or state["account"] != account:
            raise HandlerError("not_found", "Mail is not set up for that domain on this account.")
        return state

    def _all(self):
        found = []
        for path in (p for p in self.s.list("/var/lib/whp/mail/") if p.endswith(".json")):
            found.append(json.loads(self.s.read(path)))
        return found

    def _owner_of(self, account, address):
        """(domain state, local part) of an address that belongs to this account, or an error."""
        local, _, domain = address.partition("@")
        return self._need(account, domain), local

    def _maildir(self, domain, local):
        return self.mail_root / domain / local

    # --- configuration -------------------------------------------------------------------------------------------------------------------
    def _render(self, states):
        domains, boxes, aliases, blocked, passwd, spam = [], [], [], [], [], []
        for st in states:
            d = st["domain"]
            if st["routing"] == "local":
                domains.append(f"{d} OK")
            for local, box in sorted(st["mailboxes"].items()):
                if st["routing"] == "local":
                    boxes.append(f"{local}@{d} {d}/{local}/")
                quota = box["quota_mb"] or st["limits"]["max_quota_mb"]
                passwd.append(f"{local}@{d}:{box['hash']}:{VMAIL_UID}:{VMAIL_UID}::/var/mail/vhosts/{d}/{local}::userdb_quota_rule=*:storage={quota}M")
                if box.get("blocked"):
                    blocked.append(f"{local}@{d} REJECT Sending from this address is blocked")
            targets = {}
            for fw in st["forwarders"]:
                targets.setdefault(fw["source"], []).append(fw["destination"])
            for source, dests in sorted(targets.items()):
                keep = [source] if source.split("@")[0] in st["mailboxes"] else []  # a mailbox that is also forwarded keeps its own copy
                aliases.append(f"{source} {','.join(keep + dests)}")
            sp = st["spam"]
            mark, reject = SPAM_ACTIONS[sp["level"]]
            if sp["delete_spam"]:
                reject = mark  # no Spam folder: what would be marked is rejected
            body = ("want_spam = yes;" if not sp["enabled"] else f'apply {{ actions {{ "add header" = {mark}; "reject" = {reject}; }} }}')
            spam.append(f'whp_{re.sub("[^a-z0-9]", "_", d)} {{ priority = 10; rcpt = "@{d}"; {body} }}')
        return {"/etc/postfix/whp/virtual_domains": domains, "/etc/postfix/whp/virtual_mailboxes": boxes, "/etc/postfix/whp/virtual_aliases": aliases,
                "/etc/postfix/whp/blocked_senders": blocked, "/etc/dovecot/whp/passwd": passwd, "/etc/rspamd/local.d/whp_spam.conf": spam}

    def _apply(self, states):
        rendered = self._render(states)
        changed = []
        for path, lines in rendered.items():
            text = "# managed by WH Agent - do not edit by hand\n" + "\n".join(lines) + ("\n" if lines else "")
            if self.s.read(path) != text:
                self.s.write(path, text, 0o640)
                changed.append(path)
        maps = [p for p in changed if p.startswith("/etc/postfix/whp/")]
        for path in maps:
            self.s.run(["postmap", path])
        if maps:
            self.s.run(["postfix", "reload"])
        if "/etc/rspamd/local.d/whp_spam.conf" in changed:
            self.s.run(["systemctl", "reload", "rspamd"])

    def _commit(self, new_state, old_all):
        """Write the new state of one domain and the configuration built from it; if the services refuse, put the old configuration back."""
        others = [s for s in old_all if s["domain"] != new_state["domain"]]
        try:
            self._apply(others + [new_state])
        except Exception:
            try:
                self._apply(old_all)
            except Exception:
                pass
            raise
        self.s.write(STATE.format(domain=new_state["domain"]), json.dumps(new_state, sort_keys=True), 0o600)

    # --- commands --------------------------------------------------------------------------------------------------------------------------
    def run(self, command, params):
        try:
            p = catalog.validate(command, params)
            handler = self.HANDLERS.get(command)
            if handler is None:
                raise HandlerError("not_implemented", f"{command} is not implemented on this node yet.")
            return getattr(self, handler)(p)
        except catalog.InvalidCommand as exc:
            raise HandlerError("invalid", str(exc)) from None
        except SystemError_ as exc:
            raise HandlerError("system_error", str(exc)[:300]) from None

    HANDLERS = {"MAIL_DOMAIN_ADD": "domain_add", "MAIL_DOMAIN_REMOVE": "domain_remove", "MAIL_LIST": "listing", "CREATE_MAILBOX": "box_create", "MAIL_BOX_DELETE": "box_delete",
                "MAIL_BOX_PASSWORD": "box_password", "MAIL_BOX_QUOTA": "box_quota", "MAIL_FORWARD_ADD": "forward_add", "MAIL_FORWARD_DELETE": "forward_delete",
                "MAIL_AUTOREPLY_SET": "autoreply_set", "MAIL_AUTOREPLY_DELETE": "autoreply_delete", "MAIL_SPAM_SET": "spam_set", "MAIL_ROUTING_SET": "routing_set",
                "MAIL_FOLDERS": "folders", "MAIL_MESSAGES": "messages", "MAIL_READ": "read", "MAIL_MOVE": "move", "MAIL_TRASH": "trash", "MAIL_STAR": "star",
                "MAIL_DRAFT": "draft", "MAIL_PROFILE": "profile", "MAIL_PROFILE_SET": "profile_set", "MAIL_SEND": "send"}

    def domain_add(self, p):
        account, domain = p["account"], p["domain"]
        everything = self._all()
        state = self._state(domain)
        limits = {k: p[k] for k in DEFAULT_LIMITS if k in p}
        if state:
            if state["account"] != account:
                raise HandlerError("conflict", "That domain already has mail on this node for another account.")
            state["limits"] = {**state["limits"], **limits}
            self._commit(state, everything)
            return "updated", {"dkim_txt": state["dkim_txt"], "selector": SELECTOR}
        key = rsa.generate_private_key(65537, 2048)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        state = {"account": account, "domain": domain, "limits": {**DEFAULT_LIMITS, **limits}, "routing": "local", "spam": {"enabled": True, "level": "medium", "delete_spam": False},
                 "mailboxes": {}, "forwarders": [], "dkim_txt": "v=DKIM1; k=rsa; p=" + base64.b64encode(der).decode()}
        key_path = f"/etc/rspamd/dkim/{domain}.{SELECTOR}.key"
        self.s.write(key_path, pem, 0o600, "_rspamd")
        if self.s.read("/etc/rspamd/local.d/dkim_signing.conf") is None:
            self.s.write("/etc/rspamd/local.d/dkim_signing.conf", f'# managed by WH Agent\nselector = "{SELECTOR}";\npath = "/etc/rspamd/dkim/$domain.$selector.key";\n'
                                                                  "allow_username_mismatch = false;\nsign_authenticated = true;\n")
        try:
            self._commit(state, everything)
        except Exception:
            self.s.remove(key_path)
            raise
        return "created", {"dkim_txt": state["dkim_txt"], "selector": SELECTOR}

    def domain_remove(self, p):
        state = self._state(p["domain"])
        if state is None:
            return "absent", {}
        if state["account"] != p["account"]:
            raise HandlerError("not_found", "Mail is not set up for that domain on this account.")
        everything = self._all()
        self._apply([s for s in everything if s["domain"] != p["domain"]])
        self.s.remove(STATE.format(domain=p["domain"]))
        self.s.remove(f"/etc/rspamd/dkim/{p['domain']}.{SELECTOR}.key")
        self.s.remove(f"/var/mail/vhosts/{p['domain']}")
        return "removed", {}

    def listing(self, p):
        st = self._need(p["account"], p["domain"])
        boxes = [{"address": f"{local}@{st['domain']}", "quota_mb": box["quota_mb"] or None, "used_mb": self._used_mb(st["domain"], local), "blocked": bool(box.get("blocked")),
                  "autoreply": box.get("autoreply")} for local, box in sorted(st["mailboxes"].items())]
        return "listed", {"domain": st["domain"], "limits": st["limits"], "routing": st["routing"], "spam": st["spam"], "dkim_txt": st["dkim_txt"], "selector": SELECTOR,
                          "mailboxes": boxes, "forwarders": st["forwarders"]}

    def _used_mb(self, domain, local):
        root = self._maildir(domain, local)
        return round(sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) / 1048576, 2) if root.exists() else 0.0

    def box_create(self, p):
        st = self._need(p["account"], p["domain"])
        local = p["local_part"]
        if local in st["mailboxes"]:
            raise HandlerError("exists", "That mailbox already exists.")
        if len(st["mailboxes"]) >= st["limits"]["max_mailboxes"]:
            raise HandlerError("limit_reached", "Your plan's mailbox limit has been reached.")
        if p["quota_mb"] > st["limits"]["max_quota_mb"]:
            raise HandlerError("quota_too_big", f"A mailbox can have at most {st['limits']['max_quota_mb']} MB.")
        everything = self._all()
        st["mailboxes"][local] = {"quota_mb": p["quota_mb"], "hash": hash_password(p["password"]), "blocked": False, "sent": []}
        self._commit(st, everything)
        for folder in ("", ".Drafts", ".Sent", ".Archive", ".Junk", ".Trash"):
            for sub in ("cur", "new", "tmp"):
                (self._maildir(st["domain"], local) / folder / sub).mkdir(parents=True, exist_ok=True)
        self.s.run(["chown", "-R", f"{VMAIL_UID}:{VMAIL_UID}", str(self._maildir(st["domain"], local))])  # the agent runs as root; Dovecot runs as the mail user and must be able to write here
        return "created", {"address": f"{local}@{st['domain']}"}

    def box_delete(self, p):
        st, local = self._owner_of(p["account"], p["address"])
        if local not in st["mailboxes"]:
            return "absent", {}
        everything = self._all()
        del st["mailboxes"][local]
        st["forwarders"] = [f for f in st["forwarders"] if f["source"] != p["address"]]
        self._commit(st, everything)
        import shutil

        shutil.rmtree(self._maildir(st["domain"], local), ignore_errors=True)
        return "deleted", {}

    def _box(self, p):
        st, local = self._owner_of(p["account"], p["address"])
        if local not in st["mailboxes"]:
            raise HandlerError("not_found", "That mailbox does not exist.")
        return st, local, st["mailboxes"][local]

    def box_password(self, p):
        st, local, box = self._box(p)
        everything = self._all()
        box["hash"] = hash_password(p["password"])
        self._commit(st, everything)
        return "changed", {}

    def box_quota(self, p):
        st, local, box = self._box(p)
        if p["quota_mb"] > st["limits"]["max_quota_mb"]:
            raise HandlerError("quota_too_big", f"A mailbox can have at most {st['limits']['max_quota_mb']} MB.")
        everything = self._all()
        box["quota_mb"] = p["quota_mb"]
        self._commit(st, everything)
        return "changed", {}

    def forward_add(self, p):
        st, _ = self._owner_of(p["account"], p["source"])
        fw = {"source": p["source"], "destination": p["destination"]}
        if fw in st["forwarders"]:
            return "exists", {}
        if p["source"] == p["destination"]:
            raise HandlerError("invalid", "An address cannot be forwarded to itself.")
        if len(st["forwarders"]) >= st["limits"]["max_forwarders"]:
            raise HandlerError("limit_reached", "Your plan's forwarder limit has been reached.")
        everything = self._all()
        st["forwarders"].append(fw)
        self._commit(st, everything)
        return "created", {}

    def forward_delete(self, p):
        st, _ = self._owner_of(p["account"], p["source"])
        fw = {"source": p["source"], "destination": p["destination"]}
        if fw not in st["forwarders"]:
            return "absent", {}
        everything = self._all()
        st["forwarders"].remove(fw)
        self._commit(st, everything)
        return "deleted", {}

    def _write_sieve(self, domain, local, reply):
        path = f"/var/mail/vhosts/{domain}/{local}/.dovecot.sieve"
        if not reply or not reply["enabled"]:
            self.s.remove(path)
            return
        self.s.write(path, f'require ["vacation"];\nvacation :days 1 :subject "{_sieve(reply["subject"])}" "{_sieve(reply["body"])}";\n', 0o640)

    def autoreply_set(self, p):
        st, local, box = self._box(p)
        everything = self._all()
        box["autoreply"] = {"subject": p["subject"], "body": p["body"], "enabled": p["enabled"] == "yes"}
        self._commit(st, everything)
        self._write_sieve(st["domain"], local, box["autoreply"])
        return "set", {}

    def autoreply_delete(self, p):
        st, local, box = self._box(p)
        if not box.get("autoreply"):
            return "absent", {}
        everything = self._all()
        box["autoreply"] = None
        self._commit(st, everything)
        self._write_sieve(st["domain"], local, None)
        return "deleted", {}

    def spam_set(self, p):
        st = self._need(p["account"], p["domain"])
        everything = self._all()
        st["spam"] = {"enabled": p["enabled"] == "yes", "level": p["level"], "delete_spam": p["delete_spam"] == "yes"}
        self._commit(st, everything)
        return "set", {}

    def routing_set(self, p):
        st = self._need(p["account"], p["domain"])
        everything = self._all()
        st["routing"] = p["mode"]
        self._commit(st, everything)
        return "set", {}

    # --- abuse control ---------------------------------------------------------------------------------------------------------------------
    def _may_send(self, st, box):
        horizon = self.now() - 3600
        box["sent"] = [t for t in box.get("sent", []) if t > horizon]
        if box.get("blocked"):
            raise HandlerError("blocked", "Sending from this address has been blocked because of unusual activity. Contact support.")
        if len(box["sent"]) >= st["limits"]["emails_per_hour"]:
            raise HandlerError("rate_limited", f"You can send {st['limits']['emails_per_hour']} messages an hour. Try again later.")

    def scan_logs(self, lines):
        """The daily/hourly abuse check, run by the agent (not a command). Counts recipients per sender in the last hour from Postfix's queue-manager log lines and
        blocks any mailbox over its domain's hourly limit. Returns [address] newly blocked."""
        counts = {}
        for line in lines:
            m = re.search(r"postfix/qmgr\[\d+\]: [0-9A-F]+: from=<([^>]+)>, size=\d+, nrcpt=(\d+)", line)
            if m:
                counts[m.group(1)] = counts.get(m.group(1), 0) + int(m.group(2))
        blocked, everything = [], self._all()
        for st in everything:
            changed = False
            for local, box in st["mailboxes"].items():
                address = f"{local}@{st['domain']}"
                if counts.get(address, 0) > st["limits"]["emails_per_hour"] and not box.get("blocked"):
                    box["blocked"], changed = True, True
                    blocked.append(address)
            if changed:
                self._commit(st, everything)
        return blocked

    # --- reading and writing mail (Maildir) -----------------------------------------------------------------------------------------------
    def deliver(self, domain, local, raw, folder="inbox", seen=False):
        """Put a message into a mailbox (what Dovecot's delivery does). Used by tests and by the draft/sent copies. Returns the message id."""
        base = self._maildir(domain, local) / FOLDER_DIRS[folder]
        for sub in ("cur", "new", "tmp"):
            (base / sub).mkdir(parents=True, exist_ok=True)
        message_id = f"{int(self.now())}.{uuid.uuid4().hex[:12]}.mail"
        target = base / ("cur" if seen else "new") / (message_id + (FLAG_SEP + "S" if seen else ""))
        target.write_bytes(raw if isinstance(raw, bytes) else raw.encode())
        self.s.run(["chown", "-R", f"{VMAIL_UID}:{VMAIL_UID}", str(self._maildir(domain, local))])
        return message_id

    def _find(self, domain, local, message_id):
        root = self._maildir(domain, local)
        for folder, directory in FOLDER_DIRS.items():
            for sub in ("new", "cur"):
                folder_path = root / directory / sub
                if folder_path.is_dir():
                    for f in folder_path.iterdir():
                        if f.name.split(FLAG_SEP)[0] == message_id:
                            return folder, f
        raise HandlerError("not_found", "That message no longer exists.")

    @staticmethod
    def _flags(path):
        return path.name.split(FLAG_SEP)[1] if FLAG_SEP in path.name else ""

    def _set_flags(self, path, flags, to_cur=True):
        base = path.name.split(FLAG_SEP)[0]
        folder = path.parent.parent
        target = folder / ("cur" if to_cur else path.parent.name) / (f"{base}{FLAG_SEP}{''.join(sorted(set(flags)))}")
        path.rename(target)
        return target

    def _info(self, folder, path, body=False):
        msg = message_from_bytes(path.read_bytes(), policy=policy.default)
        flags = self._flags(path)
        text = msg.get_body(preferencelist=("plain",))
        html = msg.get_body(preferencelist=("html",))
        plain = text.get_content() if text else ""
        info = {"message_id": path.name.split(FLAG_SEP)[0], "sender": str(msg.get("From", "")), "subject": str(msg.get("Subject", "")),
                "received_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(path.stat().st_mtime)), "unread": "S" not in flags, "to": str(msg.get("To", "")), "folder": folder,
                "snippet": " ".join(plain.split())[:140], "starred": "F" in flags}
        if body:
            info.update(body=plain, html=html.get_content() if html else "")
        return info

    def _walk(self, domain, local, folder):
        root = self._maildir(domain, local)
        if folder == "starred":
            wanted = [(f, root / d) for f, d in FOLDER_DIRS.items()]
        else:
            wanted = [(folder, root / FOLDER_DIRS[folder])]
        for name, base in wanted:
            for sub in ("new", "cur"):
                if (base / sub).is_dir():
                    for f in (base / sub).iterdir():
                        if folder != "starred" or "F" in self._flags(f):
                            yield name, f

    def folders(self, p):
        st, local, _ = self._box(p)
        out = {}
        for name in catalog.MAIL_FOLDER_NAMES:
            files = list(self._walk(st["domain"], local, name))
            out[name] = [len(files), sum(1 for _, f in files if "S" not in self._flags(f))]
        return "listed", {"folders": out}

    def messages(self, p):
        st, local, _ = self._box(p)
        rows = [self._info(name, f) for name, f in self._walk(st["domain"], local, p["folder"])]
        return "listed", {"messages": sorted(rows, key=lambda r: r["received_at"], reverse=True)[:200]}

    def read(self, p):
        st, local, _ = self._box(p)
        folder, path = self._find(st["domain"], local, p["message_id"])
        info = self._info(folder, path, body=True)
        if info["unread"]:
            self._set_flags(path, self._flags(path) + "S")
            info["unread"] = False
        return "read", info

    def move(self, p):
        st, local, _ = self._box(p)
        folder, path = self._find(st["domain"], local, p["message_id"])
        target = self._maildir(st["domain"], local) / FOLDER_DIRS[p["folder"]]
        for sub in ("cur", "new", "tmp"):
            (target / sub).mkdir(parents=True, exist_ok=True)
        path.rename(target / "cur" / f"{p['message_id']}{FLAG_SEP}{''.join(sorted(set(self._flags(path))))}")
        return "moved", {}

    def trash(self, p):
        st, local, _ = self._box(p)
        folder, path = self._find(st["domain"], local, p["message_id"])
        if folder in ("trash", "drafts"):
            path.unlink()
            return "deleted", {}
        return self.move({**p, "folder": "trash"})

    def star(self, p):
        st, local, _ = self._box(p)
        folder, path = self._find(st["domain"], local, p["message_id"])
        flags = set(self._flags(path))
        flags.add("F") if p["starred"] == "yes" else flags.discard("F")
        self._set_flags(path, "".join(flags), to_cur=True)
        return "set", {}

    def profile(self, p):
        st, local, box = self._box(p)
        return "read", {"profile": box.get("profile") or {"display_name": "", "reply_to": "", "signature_html": "", "signature_text": "", "signature_new": True, "signature_reply": True}}

    def profile_set(self, p):
        st, local, box = self._box(p)
        everything = self._all()
        box["profile"] = {"display_name": p["display_name"], "reply_to": p["reply_to"], "signature_html": p["signature_html"], "signature_text": p["signature_text"],
                          "signature_new": p["signature_new"] == "yes", "signature_reply": p["signature_reply"] == "yes"}
        self._commit(st, everything)
        return "set", {}

    def _compose(self, st, box, p, strict):
        recipients = [a.strip() for a in p["to"].split(",") if a.strip()]
        if strict and not recipients:
            raise HandlerError("invalid", "Add at least one recipient.")
        bad = [r for r in recipients if not EMAIL.match(r.lower())]
        if bad:
            raise HandlerError("invalid", f"{bad[0][:60]} is not a valid email address.")
        profile = box.get("profile") or {}
        msg = EmailMessage()
        msg["From"] = f'"{profile["display_name"].replace(chr(34), "")}" <{p["address"]}>' if profile.get("display_name") else p["address"]
        if recipients:
            msg["To"] = ", ".join(recipients)
        msg["Subject"] = p["subject"].replace("\r", " ").replace("\n", " ")
        msg["Date"] = formatdate(self.now(), localtime=False)
        msg["Message-ID"] = make_msgid(domain=st["domain"])
        if profile.get("reply_to"):
            msg["Reply-To"] = profile["reply_to"].replace("\r", "").replace("\n", "")
        msg.set_content(p["body"] or " ")
        if p["html"]:
            msg.add_alternative(p["html"], subtype="html")
        return msg, recipients

    def draft(self, p):
        st, local, box = self._box(p)
        msg, _ = self._compose(st, box, p, strict=False)
        if p["draft_id"]:
            try:
                self._find(st["domain"], local, p["draft_id"])[1].unlink()
            except HandlerError:
                pass
        return "saved", {"draft_id": self.deliver(st["domain"], local, msg.as_bytes(), "drafts", seen=True)}

    def send(self, p):
        st, local, box = self._box(p)
        self._may_send(st, box)
        msg, recipients = self._compose(st, box, p, strict=True)
        everything = self._all()
        self.s.run(["sendmail", "-i", "-f", p["address"], "--", *recipients], input=msg.as_string())  # an argument list; the recipients were validated above
        box["sent"] = box.get("sent", []) + [self.now()] * len(recipients)
        self._commit(st, everything)
        self.deliver(st["domain"], local, msg.as_bytes(), "sent", seen=True)
        if p["draft_id"]:
            try:
                self._find(st["domain"], local, p["draft_id"])[1].unlink()
            except HandlerError:
                pass
        return "sent", {"recipients": len(recipients)}


_ = (parseaddr,)
