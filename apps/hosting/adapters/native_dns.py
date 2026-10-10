"""
DNS for native hosting (N5): the same ``DnsCapability`` the customer's DNS screen already uses, served from PowerDNS (ADR 07). Also makes the zone when a
website is created and removes it when the website is deleted.

Without ``POWERDNS_URL`` and ``POWERDNS_API_KEY`` configured the DNS tool is simply not offered on native accounts.
"""
import hashlib
import re

from django.conf import settings

from .base import HostingError
from .capabilities import DnsCapability, DnsRecord
from .dns_rules import normalize_record
from .powerdns import PowerDnsClient, fqdn

HIDDEN_TYPES = ("SOA", "NS")  # the zone's own records are managed by WHPanel, not shown for editing


def get_dns():
    """The PowerDNS client from settings, or None when native DNS is not set up."""
    if not (getattr(settings, "POWERDNS_URL", "") and getattr(settings, "POWERDNS_API_KEY", "")):
        return None
    return PowerDnsClient(settings.POWERDNS_URL, settings.POWERDNS_API_KEY, [n for n in settings.POWERDNS_NAMESERVERS.split(",") if n.strip()])


def to_content(kind, value, priority):
    if kind in ("CNAME",):
        return value.rstrip(".") + "."
    if kind == "MX":
        return f"{priority} {value.rstrip('.')}."
    if kind == "TXT":  # a TXT string is at most 255 characters: longer text (a DKIM key) is sent as several quoted pieces
        pieces = [value[i:i + 255] for i in range(0, len(value), 255)] or [""]
        return " ".join('"' + piece.replace("\\", "\\\\").replace('"', '\\"') + '"' for piece in pieces)
    if kind == "SRV":
        weight, port, target = value.split()
        return f"{priority} {weight} {port} {target.rstrip('.')}."
    return value  # A, AAAA, CAA are the same text


def from_content(kind, content):
    """(value, priority) for display."""
    if kind == "CNAME":
        return content.rstrip("."), None
    if kind == "MX":
        priority, host = content.split(" ", 1)
        return host.rstrip("."), int(priority)
    if kind == "TXT":
        pieces = re.findall(r'"((?:[^"\\]|\\.)*)"', content)
        text = "".join(pieces) if pieces else content
        return text.replace('\\"', '"').replace("\\\\", "\\"), None
    if kind == "SRV":
        priority, weight, port, target = content.split(" ", 3)
        return f"{weight} {port} {target.rstrip('.')}", int(priority)
    return content, None


def record_id(kind, name, content):
    return f"{kind}|{name}|{hashlib.sha1(content.encode()).hexdigest()[:10]}"


class NativeDns(DnsCapability):
    def _dns(self):
        client = get_dns()
        if client is None:
            raise HostingError("DNS is not set up for this server yet.")
        return client

    def _zone(self, username, domain):
        """The zone's name, only if it is this account's own domain."""
        account = self._account(username)
        domain = (domain or "").strip().lower().rstrip(".")
        if account is None or domain != account.domain.lower():
            raise HostingError("That domain does not belong to this account.")
        return domain

    @staticmethod
    def _relative(absolute, zone):
        name = absolute.rstrip(".")
        return "@" if name == zone else name[: -(len(zone) + 1)] if name.endswith("." + zone) else name

    def list_dns_records(self, username, domain):
        zone = self._zone(username, domain)
        found = []
        for name, kind, ttl, contents in self._dns().rrsets(zone):
            if kind in HIDDEN_TYPES or kind not in ("A", "AAAA", "CNAME", "MX", "TXT", "SRV", "CAA"):
                continue
            relative = self._relative(name, zone)
            for content in contents:
                value, priority = from_content(kind, content)
                found.append(DnsRecord(record_id=record_id(kind, relative, content), type=kind, name=relative, value=value, ttl=ttl, priority=priority))
        return sorted(found, key=lambda r: (r.type, r.name, r.value))

    def _current(self, zone, kind, name):
        for n, k, ttl, contents in self._dns().rrsets(zone):
            if k == kind and n == fqdn(name, zone):
                return ttl, list(contents)
        return None, []

    def add_dns_record(self, username, domain, record):
        zone = self._zone(username, domain)
        kind, name, value, ttl, priority = normalize_record(zone, record)
        content = to_content(kind, value, priority)
        _, existing = self._current(zone, kind, name)
        if content in existing:
            raise HostingError("That record already exists.")
        if kind == "CNAME" and existing:
            raise HostingError("A name can have only one CNAME.")
        self._dns().set_rrset(zone, fqdn(name, zone), kind, ttl, existing + [content])
        return record_id(kind, name, content)

    def delete_dns_record(self, username, domain, record_id_):
        zone = self._zone(username, domain)
        try:
            kind, name, digest = record_id_.split("|", 2)
        except ValueError:
            raise HostingError("That record no longer exists.") from None
        if kind in HIDDEN_TYPES:
            raise HostingError("That record is managed by the hosting service and cannot be removed.")
        ttl, contents = self._current(zone, kind, name)
        keep = [c for c in contents if hashlib.sha1(c.encode()).hexdigest()[:10] != digest]
        if len(keep) == len(contents):
            raise HostingError("That record no longer exists.")
        self._dns().set_rrset(zone, fqdn(name, zone), kind, ttl, keep)

    def update_dns_record(self, username, domain, record):
        zone = self._zone(username, domain)
        normalize_record(zone, record)  # refuse a bad new value BEFORE the old record is touched
        self.delete_dns_record(username, domain, record.record_id)  # a record's identity is its content, so a change is a replace
        return self.add_dns_record(username, domain, record)

    # --- the zone's life, driven by the website's life ---------------------------------------------------------------------------------------
    def create_zone_for(self, domain, ip):
        client = get_dns()
        if client is None or client.zone_exists(domain):
            return False
        records = [(fqdn("@", domain), "A", 3600, [ip]), (fqdn("www", domain), "CNAME", 3600, [domain + "."])]
        client.create_zone(domain, records)
        return True

    def delete_zone_for(self, domain):
        client = get_dns()
        if client is None:
            return False
        client.delete_zone(domain)
        return True
