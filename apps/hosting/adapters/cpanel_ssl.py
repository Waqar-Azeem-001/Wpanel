"""
SSL on a cPanel server: UAPI ``SSL`` lists the installed certificates and asks AutoSSL for a new one. cPanel's AutoSSL renews by itself and
a certificate for the account is installed by it, so the per-domain auto-renew and "always use HTTPS" switches are not offered here.
"""
from datetime import datetime, timezone

from .base import HostingError
from .capabilities import SslCapability, SslStatus
from .cpanel_api import CpanelApi

EXPIRING_DAYS = 14


def _when(value):
    try:
        return datetime.fromtimestamp(int(float(value)), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


class CpanelSsl(CpanelApi, SslCapability):
    def _installed_certs(self, user):
        """{host name: certificate record} for what is installed on the account's sites."""
        found = {}
        for item in self._uapi(user, "SSL", "installed_hosts") or []:
            if not isinstance(item, dict):
                continue
            cert = item.get("certificate") if isinstance(item.get("certificate"), dict) else {}
            for host in [item.get("servername"), *(item.get("domains") or [])]:
                if isinstance(host, str) and host:
                    found.setdefault(host.lower(), cert)
        return found

    def _status(self, domain, cert):
        if not cert:
            return SslStatus(domain=domain, active=False, status="none")
        expires = _when(cert.get("not_after"))
        now = datetime.now(timezone.utc)
        days = int((expires - now).total_seconds() // 86400) if expires else None
        if expires is None:
            status = "active"
        else:
            status = "expired" if expires <= now else "expiring" if days <= EXPIRING_DAYS else "active"
        issuer = cert.get("issuer") if isinstance(cert.get("issuer"), dict) else {}
        issued = _when(cert.get("not_before"))
        return SslStatus(domain=domain, active=status in ("active", "expiring"), issuer=str(issuer.get("organizationName") or cert.get("issuer_text") or ""),
                         expires_at=expires.isoformat() if expires else None, auto_renew=True, https_redirect=False, status=status,
                         issued_at=issued.isoformat() if issued else None, days_left=days)

    def list_ssl_status(self, username):
        installed = self._installed_certs(username)
        main, addon, subs, parked = self._account_domains(username)
        return [self._status(d, installed.get(d)) for d in dict.fromkeys([main, *addon, *subs, *parked]) if d]

    def get_ssl_status(self, username, domain):
        domain = self._owned_domain(username, domain)
        return self._status(domain, self._installed_certs(username).get(domain))

    def request_ssl(self, username, domain):
        self._owned_domain(username, domain)
        self._uapi(username, "SSL", "start_autossl_check")  # AutoSSL looks at every site of the account and installs what is missing

    def remove_ssl(self, username, domain):
        domain = self._owned_domain(username, domain)
        if domain not in self._installed_certs(username):
            raise HostingError("This domain has no certificate to remove.")
        self._uapi(username, "SSL", "delete_ssl", {"host": domain})

    def set_ssl_auto_renew(self, username, domain, enabled):
        self._unsupported("Turning automatic renewal on or off (AutoSSL renews by itself)")

    def set_https_redirect(self, username, domain, enabled):
        self._unsupported("Forcing HTTPS")
