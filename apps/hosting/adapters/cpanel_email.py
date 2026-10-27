"""Email on a cPanel server, through UAPI's ``Email`` module: mailboxes, quotas, passwords, forwarders and automatic replies. Webmail opens the server's own Roundcube through a one-time link; reading mail inside WHPanel is not offered."""
from urllib.parse import urlsplit

from .base import HostingError
from .capabilities import (AutoresponderInfo, EmailCapability, EmailLimits, ForwarderInfo, MailboxInfo, WebmailAccess)
from .cpanel_api import CpanelApi, plan_limit


def _split(address):
    local, _, domain = (address or "").strip().lower().partition("@")
    if not local or not domain or "@" in domain or any(c in local for c in " /\\\"'<>;,"):
        raise HostingError("Enter a full e-mail address such as info@example.com.")
    return local, domain


def _number(value):
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


class CpanelEmail(CpanelApi, EmailCapability):
    def _mail_domain(self, user, address):
        """(local part, domain), after checking the domain belongs to this account."""
        local, domain = _split(address)
        return local, self._owned_domain(user, domain)

    def email_limits(self, username):
        summary = self._summary(username)

        return EmailLimits(domain=str(summary.get("domain", "")), max_mailboxes=plan_limit(summary.get("maxpop")), max_forwarders=None, default_quota_mb=1024,
                           max_quota_mb=None, password_min_length=12)

    # --- mailboxes -----------------------------------------------------------------------------------------------------

    def list_mailboxes(self, username):
        found = []
        for item in self._uapi(username, "Email", "list_pops_with_disk") or []:
            if not isinstance(item, dict) or not item.get("email"):
                continue
            quota = _number(item.get("diskquota"))
            used = _number(item.get("diskused"))
            found.append(MailboxInfo(address=str(item["email"]).lower(), quota_mb=int(quota) if quota else None,
                                     used_mb=round(used, 2) if used is not None else None))
        return found

    def create_mailbox(self, username, address, password, *, quota_mb=None):
        local, domain = self._mail_domain(username, address)
        self._uapi(username, "Email", "add_pop", {"email": local, "domain": domain, "password": password, "quota": quota_mb or 0}, secret=True)

    def delete_mailbox(self, username, address):
        local, domain = self._mail_domain(username, address)
        self._uapi(username, "Email", "delete_pop", {"email": local, "domain": domain})

    def set_mailbox_password(self, username, address, password):
        local, domain = self._mail_domain(username, address)
        self._uapi(username, "Email", "passwd_pop", {"email": local, "domain": domain, "password": password}, secret=True)

    def set_mailbox_quota(self, username, address, quota_mb):
        local, domain = self._mail_domain(username, address)
        self._uapi(username, "Email", "edit_pop_quota", {"email": local, "domain": domain, "quota": quota_mb or 0})

    # --- forwarders ----------------------------------------------------------------------------------------------------

    def list_forwarders(self, username):
        main, addon, _subs, parked = self._account_domains(username)
        found = []
        for domain in dict.fromkeys([main, *addon, *parked]):
            for item in self._uapi(username, "Email", "list_forwarders", {"domain": domain}) or []:
                if isinstance(item, dict) and item.get("dest") and item.get("forward"):
                    found.append(ForwarderInfo(source=str(item["dest"]).lower(), destination=str(item["forward"]).lower()))
        return found

    def create_forwarder(self, username, source, destination):
        local, domain = self._mail_domain(username, source)
        _split(destination)
        self._uapi(username, "Email", "add_forwarder", {"domain": domain, "email": local, "fwdopt": "fwd", "fwd": destination.strip().lower()})

    def delete_forwarder(self, username, source, destination):
        self._mail_domain(username, source)
        self._uapi(username, "Email", "delete_forwarder", {"address": source.strip().lower(), "forwarder": destination.strip().lower()})

    # --- automatic replies ---------------------------------------------------------------------------------------------

    def list_autoresponders(self, username):
        main, addon, _subs, parked = self._account_domains(username)
        found = []
        for domain in dict.fromkeys([main, *addon, *parked]):
            for item in self._uapi(username, "Email", "list_auto_responders", {"domain": domain}) or []:
                if isinstance(item, dict) and item.get("email"):
                    found.append(AutoresponderInfo(address=str(item["email"]).lower(), subject=str(item.get("subject", "")), body="", enabled=True))
        return found

    def set_autoresponder(self, username, address, subject, body, enabled):
        local, domain = self._mail_domain(username, address)
        if not enabled:
            self._uapi(username, "Email", "delete_auto_responder", {"email": f"{local}@{domain}"})
            return
        self._uapi(username, "Email", "add_auto_responder", {"email": local, "domain": domain, "from": f"{local}@{domain}", "subject": subject,
                                                              "body": body, "interval": 24, "is_html": 0, "start": 0, "stop": 0})

    def delete_autoresponder(self, username, address):
        local, domain = self._mail_domain(username, address)
        self._uapi(username, "Email", "delete_auto_responder", {"email": f"{local}@{domain}"})

    def webmail_access(self, username, address):
        """A one-time sign-in link into the cPanel server's own webmail (Roundcube) for this one mailbox. No password is typed or kept."""
        local, domain = self._mail_domain(username, address)
        data = (self._call("create_user_session", {"user": f"{local}@{domain}", "service": "webmaild"}).get("data") or {})
        url = str(data.get("url") or "")
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.netloc:
            raise HostingError("The server did not give a usable webmail sign-in link.")
        return WebmailAccess(kind="external", url=url)

    # --- not offered on cPanel servers yet -----------------------------------------------------------------------------

    def get_mail_routing(self, username):
        self._unsupported("Mail routing")

    def set_mail_routing(self, username, mode):
        self._unsupported("Mail routing")

    def get_spam_settings(self, username):
        self._unsupported("Spam settings")

    def set_spam_settings(self, username, settings):
        self._unsupported("Spam settings")

    def message_folders(self, username, address):
        self._unsupported("Reading mail")

    def list_messages(self, username, address, folder="inbox"):
        self._unsupported("Reading mail")

    def move_message(self, username, address, message_id, folder):
        self._unsupported("Reading mail")

    def delete_message(self, username, address, message_id):
        self._unsupported("Reading mail")

    def set_message_starred(self, username, address, message_id, starred):
        self._unsupported("Reading mail")

    def save_draft(self, username, address, to, subject, body, html="", draft_id=""):
        self._unsupported("Writing mail")

    def get_mail_profile(self, username, address):
        self._unsupported("Mail settings")

    def set_mail_profile(self, username, address, profile):
        self._unsupported("Mail settings")

    def send_message(self, username, address, to, subject, body, html="", draft_id=""):
        self._unsupported("Writing mail")

    def read_message(self, username, address, message_id):
        self._unsupported("Reading mail")
