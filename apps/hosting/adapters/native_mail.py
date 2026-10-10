"""
Email on native hosting (N7): the same ``EmailCapability`` the customer's Email and webmail screens already use, served by a separate MAIL node (ADR 06), not by the web node.

The first time a native account uses mail, WHPanel picks a healthy mail node for it, sets up the domain there (limits, DKIM key) and, when native DNS is configured, publishes the
mail records: MX to the mail node, SPF for its address, the DKIM key and a DMARC policy. After that every operation is a typed command to that mail node.

Webmail is WHPanel's own simple viewer (``embedded``): there is no Roundcube. Attachments are not shown or sent. Never run against a real Postfix/Dovecot/Rspamd.
"""
import ipaddress

from apps.agent import services as agent
from apps.core.exceptions import ServiceError

from . import native_dns
from .base import HostingError
from .capabilities import (AutoresponderInfo, EmailCapability, EmailLimits, ForwarderInfo, MailboxInfo, MailProfile, MessageInfo, SpamSettings, WebmailAccess)

MAIL_PLAN_KEYS = {"email_accounts": ("max_mailboxes", 0), "forwarders": ("max_forwarders", 0), "mailbox_quota_mb": ("max_quota_mb", 1), "emails_per_hour": ("emails_per_hour", 1)}  # (node name, smallest allowed)
DMARC_POLICY = "v=DMARC1; p=none; rua=mailto:postmaster@{domain}"  # monitor first: a stricter policy can lose a customer's own legitimate mail


def to_message(m, full=False):
    return MessageInfo(message_id=m["message_id"], sender=m["sender"], subject=m["subject"], received_at=m["received_at"], unread=m["unread"], body=m.get("body", "") if full else "",
                       to=m["to"], folder=m["folder"], snippet=m["snippet"], starred=m["starred"], html=m.get("html", "") if full else "")


class NativeEmail(EmailCapability):
    # --- finding and preparing the mail node ----------------------------------------------------------------------------------------
    def _mail_account(self, username):
        account = self._account(username)
        if account is None:
            raise HostingError("That account no longer exists.")
        return account

    def _mail_node(self, username, create=True):
        """The Server that holds this account's mail. Picks one and sets the domain up on first use."""
        from apps.agent.placement import pick_mail_node

        account = self._mail_account(username)
        if account.mail_server_id:
            return account.mail_server
        if not create:
            raise HostingError("Mail is not set up for this account yet.")
        server = pick_mail_node()
        if server is None:
            raise HostingError("No mail server is available right now. Please try again later.")
        account.mail_server = server
        account.save(update_fields=["mail_server", "updated_at"])
        try:
            self._setup_domain(account, server)
        except Exception:
            account.mail_server = None  # not set up: try again next time, from a clean start
            account.save(update_fields=["mail_server", "updated_at"])
            raise
        return server

    def _plan_limits(self, account, package=None):
        from apps.products.models import Product

        product = Product.objects.filter(whm_package_name=package or account.package_name).first()
        plan = product.resource_limits if product and isinstance(product.resource_limits, dict) else {}
        out = {}
        for plan_key, (node_key, smallest) in MAIL_PLAN_KEYS.items():
            value = plan.get(plan_key, "absent")
            if value is None:
                out[node_key] = 100_000  # unlimited
            elif isinstance(value, int) and not isinstance(value, bool) and value >= smallest:
                out[node_key] = value
        return out

    def _mcall(self, username, command, params, wait=30, create=True):
        server = self._mail_node(username, create=create)
        try:
            return agent.call(server, command, {"account": username, **params}, wait=wait)
        except ServiceError as exc:
            raise HostingError(exc.message) from exc

    def _setup_domain(self, account, server):
        domain = account.domain.lower()
        try:
            data = agent.call(server, "MAIL_DOMAIN_ADD", {"account": account.username, "domain": domain, **self._plan_limits(account)}, wait=120)
        except ServiceError as exc:
            raise HostingError(exc.message) from exc
        self._publish_mail_dns(account, server, data)

    def sync_mail_limits(self, username, package=None):
        """After a plan change: the mail node learns the new limits (queued, not waited for). Does nothing for an account that has not used mail."""
        account = self._mail_account(username)
        if account.mail_server_id:
            agent.dispatch(None, account.mail_server, "MAIL_DOMAIN_ADD", {"account": username, "domain": account.domain.lower(), **self._plan_limits(account, package)},
                           subject=f"hosting:{account.pk}")

    def _publish_mail_dns(self, account, server, data):
        """MX, SPF, DKIM and DMARC into the domain's native zone. Existing TXT records at the same names are kept; only an old SPF record is replaced."""
        client = native_dns.get_dns()
        domain = account.domain.lower()
        if client is None or not client.zone_exists(domain):
            return False
        from apps.agent.models import AgentNode

        node = AgentNode.objects.filter(server=server).first()
        ips = (server.ip_address, *(node.ip_addresses if node else []))
        v4 = next((i for i in ips if i and ipaddress.ip_address(i).version == 4), None)
        spf = "v=spf1 " + (f"ip4:{v4} " if v4 else "") + "-all"

        def merge(name, add, drop_prefix):
            current = next((c for n, t, ttl, c in client.rrsets(domain) if n == name and t == "TXT"), [])
            keep = [c for c in current if not native_dns.from_content("TXT", c)[0].lower().startswith(drop_prefix)]
            client.set_rrset(domain, name, "TXT", 3600, keep + [native_dns.to_content("TXT", add, None)])

        client.set_rrset(domain, domain + ".", "MX", 3600, [f"10 {server.hostname.rstrip('.')}."])
        merge(domain + ".", spf, "v=spf1")
        merge(f"{data['selector']}._domainkey.{domain}.", data["dkim_txt"], "v=dkim1")
        merge(f"_dmarc.{domain}.", DMARC_POLICY.format(domain=domain), "v=dmarc1")
        return True

    # --- the plan's view -------------------------------------------------------------------------------------------------------------
    def _listing(self, username):
        return self._mcall(username, "MAIL_LIST", {"domain": self._mail_account(username).domain.lower()})

    def email_limits(self, username):
        data = self._listing(username)
        lim = data["limits"]
        return EmailLimits(domain=data["domain"], max_mailboxes=None if lim["max_mailboxes"] >= 100_000 else lim["max_mailboxes"],
                           max_forwarders=None if lim["max_forwarders"] >= 100_000 else lim["max_forwarders"], default_quota_mb=min(1024, lim["max_quota_mb"]),
                           max_quota_mb=lim["max_quota_mb"], password_min_length=12)

    def list_mailboxes(self, username):
        return [MailboxInfo(address=b["address"], quota_mb=b["quota_mb"], used_mb=b["used_mb"]) for b in self._listing(username)["mailboxes"]]

    def create_mailbox(self, username, address, password, *, quota_mb=None):
        local, _, domain = (address or "").strip().lower().partition("@")
        if domain != self._mail_account(username).domain.lower() or not local:
            raise HostingError("Use an address on your own domain.")
        if quota_mb is None:
            quota_mb = min(1024, self.email_limits(username).max_quota_mb)
        self._mcall(username, "CREATE_MAILBOX", {"domain": domain, "local_part": local, "quota_mb": int(quota_mb), "password": password})

    def delete_mailbox(self, username, address):
        self._mcall(username, "MAIL_BOX_DELETE", {"address": address.strip().lower()}, wait=120)

    def set_mailbox_password(self, username, address, password):
        self._mcall(username, "MAIL_BOX_PASSWORD", {"address": address.strip().lower(), "password": password})

    def set_mailbox_quota(self, username, address, quota_mb):
        self._mcall(username, "MAIL_BOX_QUOTA", {"address": address.strip().lower(), "quota_mb": 0 if quota_mb is None else int(quota_mb)})

    def list_forwarders(self, username):
        return [ForwarderInfo(source=f["source"], destination=f["destination"]) for f in self._listing(username)["forwarders"]]

    def create_forwarder(self, username, source, destination):
        self._mcall(username, "MAIL_FORWARD_ADD", {"source": source.strip().lower(), "destination": destination.strip().lower()})

    def delete_forwarder(self, username, source, destination):
        self._mcall(username, "MAIL_FORWARD_DELETE", {"source": source.strip().lower(), "destination": destination.strip().lower()})

    def list_autoresponders(self, username):
        return [AutoresponderInfo(address=b["address"], subject=b["autoreply"]["subject"], body=b["autoreply"]["body"], enabled=b["autoreply"]["enabled"])
                for b in self._listing(username)["mailboxes"] if b.get("autoreply")]

    def set_autoresponder(self, username, address, subject, body, enabled):
        self._mcall(username, "MAIL_AUTOREPLY_SET", {"address": address.strip().lower(), "subject": subject, "body": body, "enabled": "yes" if enabled else "no"})

    def delete_autoresponder(self, username, address):
        self._mcall(username, "MAIL_AUTOREPLY_DELETE", {"address": address.strip().lower()})

    def get_mail_routing(self, username):
        return self._listing(username)["routing"]

    def set_mail_routing(self, username, mode):
        self._mcall(username, "MAIL_ROUTING_SET", {"domain": self._mail_account(username).domain.lower(), "mode": mode})

    def get_spam_settings(self, username):
        spam = self._listing(username)["spam"]
        return SpamSettings(enabled=spam["enabled"], level=spam["level"], delete_spam=spam["delete_spam"])

    def set_spam_settings(self, username, settings):
        yes = lambda flag: "yes" if flag else "no"  # noqa: E731
        self._mcall(username, "MAIL_SPAM_SET", {"domain": self._mail_account(username).domain.lower(), "enabled": yes(settings.enabled), "level": settings.level,
                                                "delete_spam": yes(settings.delete_spam)})

    # --- webmail (WHPanel's own viewer) ----------------------------------------------------------------------------------------------
    def webmail_access(self, username, address):
        return WebmailAccess(kind="embedded")

    def message_folders(self, username, address):
        return {k: tuple(v) for k, v in self._mcall(username, "MAIL_FOLDERS", {"address": address.strip().lower()})["folders"].items()}

    def list_messages(self, username, address, folder="inbox"):
        return [to_message(m) for m in self._mcall(username, "MAIL_MESSAGES", {"address": address.strip().lower(), "folder": folder})["messages"]]

    def read_message(self, username, address, message_id):
        return to_message(self._mcall(username, "MAIL_READ", {"address": address.strip().lower(), "message_id": message_id}), full=True)

    def move_message(self, username, address, message_id, folder):
        self._mcall(username, "MAIL_MOVE", {"address": address.strip().lower(), "message_id": message_id, "folder": folder})

    def delete_message(self, username, address, message_id):
        self._mcall(username, "MAIL_TRASH", {"address": address.strip().lower(), "message_id": message_id})

    def set_message_starred(self, username, address, message_id, starred):
        self._mcall(username, "MAIL_STAR", {"address": address.strip().lower(), "message_id": message_id, "starred": "yes" if starred else "no"})

    def save_draft(self, username, address, to, subject, body, html="", draft_id=""):
        return self._mcall(username, "MAIL_DRAFT", {"address": address.strip().lower(), "to": to, "subject": subject, "body": body, "html": html or "", "draft_id": draft_id or ""})["draft_id"]

    def get_mail_profile(self, username, address):
        p = self._mcall(username, "MAIL_PROFILE", {"address": address.strip().lower()})["profile"]
        return MailProfile(display_name=p["display_name"], reply_to=p["reply_to"], signature_html=p["signature_html"], signature_text=p["signature_text"],
                           signature_new=p["signature_new"], signature_reply=p["signature_reply"])

    def set_mail_profile(self, username, address, profile):
        yes = lambda flag: "yes" if flag else "no"  # noqa: E731
        self._mcall(username, "MAIL_PROFILE_SET", {"address": address.strip().lower(), "display_name": profile.display_name, "reply_to": profile.reply_to,
                                                   "signature_html": profile.signature_html, "signature_text": profile.signature_text,
                                                   "signature_new": yes(profile.signature_new), "signature_reply": yes(profile.signature_reply)})

    def send_message(self, username, address, to, subject, body, html="", draft_id=""):
        self._mcall(username, "MAIL_SEND", {"address": address.strip().lower(), "to": to, "subject": subject, "body": body, "html": html or "", "draft_id": draft_id or ""}, wait=60)
