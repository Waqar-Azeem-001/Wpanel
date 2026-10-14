"""
The demo provider's SSL: a JSON file per account that behaves like a free, automatic certificate service. A certificate can be
issued only for a domain that is on the account and whose DNS points at this server (the check a real authority makes); it lasts
90 days; with auto-renew on it is renewed on its own once 30 days or fewer are left; "always use HTTPS" needs a working
certificate; removing a certificate switches it off. Status and days left are worked out from the dates every time. No real
certificate exists - a real provider (the cPanel adapter) implements the same ``SslCapability`` contract.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .base import HostingError
from .capabilities import SslStatus

VALID_DAYS = 90
RENEW_WITHIN_DAYS = 30
EXPIRING_DAYS = 14
ISSUER = "Free SSL (sample data)"


def _now():
    return datetime.now(timezone.utc)


def _iso(when):
    return when.isoformat()


class DemoSsl:
    def __init__(self, directory, domains, points_here):
        self.file = Path(directory) / "ssl.json"
        self.domains = domains  # callback: the names of every domain on the account
        self.points_here = points_here  # callback: does this name's DNS lead to this server?

    # --- storage ---------------------------------------------------------------------------------------------------

    def _load(self):
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("certs", {})
        return data

    def _save(self, data):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(data), encoding="utf-8")

    def _known(self, domain):
        domain = (domain or "").strip().lower().rstrip(".")
        if domain not in self.domains():
            raise HostingError("That domain is not on this account.")
        return domain

    # --- reading ---------------------------------------------------------------------------------------------------

    @staticmethod
    def _status(domain, cert):
        if not cert:
            return SslStatus(domain=domain, active=False, status="none")
        expires = datetime.fromisoformat(cert["expires_at"])
        days = int((expires - _now()).total_seconds() // 86400)
        status = "expired" if expires <= _now() else ("expiring" if days <= EXPIRING_DAYS else "active")
        return SslStatus(domain=domain, active=status in ("active", "expiring"), issuer=cert["issuer"], expires_at=cert["expires_at"],
                         auto_renew=cert["auto_renew"], https_redirect=cert["https_redirect"] and status != "expired",
                         status=status, issued_at=cert["issued_at"], days_left=days)

    def _auto_renew(self, data):
        """What a real automatic service does on its own: renew a certificate that is close to its end, if its DNS still points here."""
        changed = False
        for domain, cert in data["certs"].items():
            days = (datetime.fromisoformat(cert["expires_at"]) - _now()).total_seconds() / 86400
            if cert["auto_renew"] and days <= RENEW_WITHIN_DAYS and self.points_here(domain):
                now = _now()
                cert["issued_at"], cert["expires_at"] = _iso(now), _iso(now + timedelta(days=VALID_DAYS))
                changed = True
        if changed:
            self._save(data)

    def list_status(self):
        data = self._load()
        self._auto_renew(data)
        data = self._load()
        return [self._status(d, data["certs"].get(d)) for d in self.domains()]

    def status(self, domain):
        domain = self._known(domain)
        data = self._load()
        self._auto_renew(data)
        return self._status(domain, self._load()["certs"].get(domain))

    # --- changing --------------------------------------------------------------------------------------------------

    def request(self, domain):
        """Issue a certificate, or renew the one in place. Needs the domain's DNS to lead here."""
        domain = self._known(domain)
        if not self.points_here(domain):
            raise HostingError(f"{domain} does not point to this server yet, so a certificate cannot be issued. Add an A record "
                               "for it under DNS records, wait a few minutes, then try again.")
        data = self._load()
        now = _now()
        cert = data["certs"].get(domain) or {"auto_renew": True, "https_redirect": False}
        cert.update(issuer=ISSUER, issued_at=_iso(now), expires_at=_iso(now + timedelta(days=VALID_DAYS)))
        data["certs"][domain] = cert
        self._save(data)

    def remove(self, domain):
        domain = self._known(domain)
        data = self._load()
        if domain not in data["certs"]:
            raise HostingError("This domain has no certificate to remove.")
        del data["certs"][domain]
        self._save(data)

    def set_auto_renew(self, domain, enabled):
        domain = self._known(domain)
        data = self._load()
        if domain not in data["certs"]:
            raise HostingError("Install a certificate first.")
        data["certs"][domain]["auto_renew"] = bool(enabled)
        self._save(data)

    def set_redirect(self, domain, enabled):
        domain = self._known(domain)
        data = self._load()
        cert = data["certs"].get(domain)
        if enabled:
            if not cert or not self._status(domain, cert).active:
                raise HostingError("Install a working certificate first: sending visitors to HTTPS without one would show them a "
                                   "security warning.")
        elif not cert:
            return
        cert["https_redirect"] = bool(enabled)
        self._save(data)
