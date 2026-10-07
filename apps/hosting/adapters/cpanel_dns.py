"""DNS on a cPanel server, through WHM's zone functions (``dumpzone``, ``addzonerecord``, ``editzonerecord``, ``removezonerecord``)."""
from .base import HostingError
from .capabilities import DnsCapability, DnsRecord
from .cpanel_api import CpanelApi
from .dns_rules import normalize_record

TYPES = {"A": "address", "AAAA": "address", "CNAME": "cname", "TXT": "txtdata"}


def _relative(name, domain):
    name = (name or "").rstrip(".").lower()
    if name in ("", "@", domain):
        return "@"
    return name[: -(len(domain) + 1)] if name.endswith("." + domain) else name


def _text(value):
    return "".join(value) if isinstance(value, list) else str(value or "")


class CpanelDns(CpanelApi, DnsCapability):
    def _dump(self, domain):
        data = self._call("dumpzone", {"domain": domain}).get("data") or {}
        zones = data.get("zone") or []
        return (zones[0].get("record") or []) if zones and isinstance(zones[0], dict) else []

    @staticmethod
    def _to_record(raw, domain):
        kind = str(raw.get("type", "")).upper()
        name = _relative(raw.get("name"), domain)
        ttl = int(raw.get("ttl") or 14400)
        line = raw.get("Line", raw.get("line"))
        record_id = f"{line}:{kind}:{name}"
        priority = None
        if kind in ("A", "AAAA"):
            value = str(raw.get("address", ""))
        elif kind == "CNAME":
            value = str(raw.get("cname", "")).rstrip(".")
        elif kind == "TXT":
            value = _text(raw.get("txtdata"))
        elif kind == "MX":
            value, priority = str(raw.get("exchange", "")).rstrip("."), int(raw.get("preference") or 0)
        elif kind == "SRV":
            value, priority = f"{raw.get('weight')} {raw.get('port')} {str(raw.get('target', '')).rstrip('.')}", int(raw.get("priority") or 0)
        elif kind == "CAA":
            value = f'{raw.get("flag", 0)} {raw.get("tag")} "{raw.get("value")}"'
        else:
            return None
        return DnsRecord(record_id=record_id, type=kind, name=name, value=value, ttl=ttl, priority=priority)

    def list_dns_records(self, username, domain):
        domain = self._owned_domain(username, domain, zones_only=True)
        found = [self._to_record(r, domain) for r in self._dump(domain) if isinstance(r, dict) and r.get("type")]
        return [r for r in found if r is not None]

    def _fields(self, domain, kind, name, value, ttl, priority):
        """The WHM parameters for one record. Names are sent as full names ending in a dot."""
        full = (domain if name == "@" else f"{name}.{domain}") + "."
        params = {"domain": domain, "name": full, "type": kind, "class": "IN", "ttl": ttl}
        if kind in ("A", "AAAA"):
            params["address"] = value
        elif kind == "CNAME":
            params["cname"] = value
        elif kind == "TXT":
            params["txtdata"] = value
        elif kind == "MX":
            params.update(exchange=value, preference=priority)
        elif kind == "SRV":
            weight, port, target = value.split()
            params.update(priority=priority, weight=weight, port=port, target=target)
        elif kind == "CAA":
            flag, tag, quoted = value.split(" ", 2)
            params.update(flag=flag, tag=tag, value=quoted.strip('"'))
        return params

    def _line_of(self, domain, record_id):
        """The zone line a record id points at, after checking the line still holds that same record (lines move when others change)."""
        line, _, rest = (record_id or "").partition(":")
        kind, _, name = rest.partition(":")
        for raw in self._dump(domain):
            if str(raw.get("Line", raw.get("line"))) == line and str(raw.get("type", "")).upper() == kind \
                    and _relative(raw.get("name"), domain) == name:
                return line
        raise HostingError("The DNS zone changed since you opened it. Reload the page and try again.")

    def add_dns_record(self, username, domain, record):
        domain = self._owned_domain(username, domain, zones_only=True)
        kind, name, value, ttl, priority = normalize_record(domain, record)
        self._call("addzonerecord", self._fields(domain, kind, name, value, ttl, priority))
        return ""

    def update_dns_record(self, username, domain, record):
        domain = self._owned_domain(username, domain, zones_only=True)
        kind, name, value, ttl, priority = normalize_record(domain, record)
        line = self._line_of(domain, record.record_id)
        if not record.record_id.split(":", 2)[1:2] == [kind]:
            raise HostingError("The type of a record cannot be changed. Add a new record instead.")
        self._call("editzonerecord", {**self._fields(domain, kind, name, value, ttl, priority), "line": line})

    def delete_dns_record(self, username, domain, record_id):
        domain = self._owned_domain(username, domain, zones_only=True)
        self._call("removezonerecord", {"domain": domain, "line": self._line_of(domain, record_id)})
