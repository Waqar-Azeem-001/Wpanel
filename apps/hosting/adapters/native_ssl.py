"""
SSL on a native node (N5): the same ``SslCapability`` the customer's SSL screen already uses. Before asking for a certificate WHPanel checks that the domain really
points at the node, so the customer gets a clear instruction instead of a validation failure from the certificate authority. The node issues over HTTP-01 and renews
by itself 30 days before expiry (see ``apps/agent/node/ssl.py``).
"""
import ipaddress
import socket

from .base import HostingError
from .capabilities import SslCapability, SslStatus

ISSUE_WAIT = 150  # seconds the screen waits for the certificate authority


def resolve(host):
    """The IPv4/IPv6 addresses a public name points at now (a separate function so tests can control it)."""
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)})
    except OSError:
        return []


def to_status(row):
    return SslStatus(domain=row["domain"], active=row["status"] in ("active", "expiring"), issuer=row["issuer"], expires_at=row["expires_at"],
                     auto_renew=row["auto_renew"], https_redirect=row["https_redirect"], status=row["status"], issued_at=row["issued_at"], days_left=row["days_left"])


class NativeSsl(SslCapability):
    def _own_domain(self, username, domain):
        account = self._account(username)
        domain = (domain or "").strip().lower().rstrip(".")
        if account is None or domain != account.domain.lower():
            raise HostingError("That domain does not belong to this account.")
        return domain

    def _rows(self, username):
        return self._call("SSL_STATUS", {"account": username})["certificates"]

    def list_ssl_status(self, username):
        return [to_status(r) for r in self._rows(username)]

    def get_ssl_status(self, username, domain):
        domain = self._own_domain(username, domain)
        for row in self._rows(username):
            if row["domain"] == domain:
                return to_status(row)
        return SslStatus(domain=domain, active=False, status="none")

    def _node_addresses(self):
        from apps.agent.models import AgentNode

        node = AgentNode.objects.filter(server=self.server).first()
        found = set(node.ip_addresses if node else [])
        if self.server.ip_address:
            found.add(self.server.ip_address)
        return {str(ipaddress.ip_address(a)) for a in found}

    def request_ssl(self, username, domain):
        domain = self._own_domain(username, domain)
        mine = self._node_addresses()
        if mine:
            for name in (domain, "www." + domain):
                points = set(resolve(name))
                if not points & mine:
                    raise HostingError(f"{name} does not point at this server yet. Set its A record to {next((ip for ip in sorted(mine) if ":" not in ip), sorted(mine)[0])}, wait a few minutes, then try again.")
        self._call("ISSUE_SSL", {"account": username, "domain": domain, "method": "http-01"}, wait=ISSUE_WAIT)

    def remove_ssl(self, username, domain):
        self._call("REMOVE_SSL", {"account": username, "domain": self._own_domain(username, domain)})

    def _set(self, username, domain, **change):
        domain = self._own_domain(username, domain)
        current = self.get_ssl_status(username, domain)
        if current.status == "none":
            raise HostingError("Get a certificate for this domain first.")
        wanted = {"auto_renew": current.auto_renew, "https_redirect": current.https_redirect, **change}
        self._call("SET_SSL_OPTIONS", {"account": username, "domain": domain, "auto_renew": "yes" if wanted["auto_renew"] else "no",
                                       "https_redirect": "yes" if wanted["https_redirect"] else "no"})

    def set_ssl_auto_renew(self, username, domain, enabled):
        self._set(username, domain, auto_renew=bool(enabled))

    def set_https_redirect(self, username, domain, enabled):
        self._set(username, domain, https_redirect=bool(enabled))
