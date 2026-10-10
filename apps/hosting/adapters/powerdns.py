"""
PowerDNS Authoritative, through its HTTP API (N5, ADR 07). WHPanel talks to the nameservers itself; the web node's agent is not involved.

``PowerDnsClient`` is the real client: it has been written from PowerDNS's documented API and **never run against a real PowerDNS**. ``FakePowerDns`` keeps zones
in memory with the same methods, for tests and for trying the screens without nameservers.

A zone is a set of *rrsets* (all records of one name and type). ``contents`` below are PowerDNS's own text forms: ``10 mail.example.com.`` for MX, a quoted string for TXT.
"""
import requests

from .base import HostingError

TIMEOUT = 15


def fqdn(name, zone):
    """The absolute name PowerDNS wants, with the trailing dot: ``@`` is the zone itself."""
    return zone + "." if name in ("", "@") else f"{name}.{zone}."


class PowerDnsClient:
    def __init__(self, base_url, api_key, nameservers=(), server_id="localhost", session=None):
        self.base, self.key, self.nameservers, self.server_id = base_url.rstrip("/"), api_key, list(nameservers), server_id
        self.session = session or requests.Session()

    def _call(self, method, path, body=None):
        try:
            reply = self.session.request(method, f"{self.base}/api/v1/servers/{self.server_id}{path}", json=body, headers={"X-API-Key": self.key}, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise HostingError("The DNS servers could not be reached. Try again in a moment.") from exc
        if reply.status_code == 404:
            return None
        if reply.status_code >= 400:
            try:
                detail = reply.json().get("error", "")
            except ValueError:
                detail = ""
            raise HostingError(f"The DNS servers refused that: {detail[:200] or reply.status_code}")
        return reply.json() if reply.content else {}

    def ping(self):
        """Is the DNS server answering? Raises HostingError if not."""
        if self._call("GET", "") is None:
            raise HostingError("The DNS server did not know its own server id.")

    def zone_exists(self, zone):
        return self._call("GET", f"/zones/{zone}.") is not None

    def create_zone(self, zone, rrsets):
        """A new zone with its nameservers and first records. ``rrsets`` are (name, type, ttl, [contents])."""
        body = {"name": zone + ".", "kind": "Native", "nameservers": [n.rstrip(".") + "." for n in self.nameservers],
                "rrsets": [{"name": n, "type": t, "ttl": ttl, "records": [{"content": c, "disabled": False} for c in contents]} for n, t, ttl, contents in rrsets]}
        self._call("POST", "/zones", body)

    def delete_zone(self, zone):
        self._call("DELETE", f"/zones/{zone}.")

    def rrsets(self, zone):
        """[(name, type, ttl, [contents])] for every rrset in the zone."""
        data = self._call("GET", f"/zones/{zone}.")
        if data is None:
            raise HostingError("That domain has no DNS zone yet.")
        return [(r["name"], r["type"], r.get("ttl", 3600), [rec["content"] for rec in r.get("records", []) if not rec.get("disabled")]) for r in data.get("rrsets", [])]

    def set_rrset(self, zone, name, kind, ttl, contents):
        """Make this name+type hold exactly these contents (none removes it)."""
        change = {"name": name, "type": kind}
        if contents:
            change.update(changetype="REPLACE", ttl=ttl, records=[{"content": c, "disabled": False} for c in contents])
        else:
            change["changetype"] = "DELETE"
        self._call("PATCH", f"/zones/{zone}.", {"rrsets": [change]})


class FakePowerDns:
    """Zones in memory. Mirrors PowerDNS's behaviour that matters here: a zone starts with SOA and NS, and a CNAME cannot share a name with other records."""

    def __init__(self, nameservers=("ns1.example.net", "ns2.example.net")):
        self.nameservers = list(nameservers)
        self.zones = {}  # zone -> {(name, type): (ttl, [contents])}
        self.down = False

    def _zone(self, zone):
        if self.down:
            raise HostingError("The DNS servers could not be reached. Try again in a moment.")
        if zone not in self.zones:
            raise HostingError("That domain has no DNS zone yet.")
        return self.zones[zone]

    def ping(self):
        if self.down:
            raise HostingError("The DNS servers could not be reached. Try again in a moment.")

    def zone_exists(self, zone):
        if self.down:
            raise HostingError("The DNS servers could not be reached. Try again in a moment.")
        return zone in self.zones

    def create_zone(self, zone, rrsets):
        if self.down:
            raise HostingError("The DNS servers could not be reached. Try again in a moment.")
        data = {(zone + ".", "SOA"): (3600, [f"{self.nameservers[0]}. hostmaster.{zone}. 1 10800 3600 604800 3600"]),
                (zone + ".", "NS"): (3600, [n + "." for n in self.nameservers])}
        for name, kind, ttl, contents in rrsets:
            data[(name, kind)] = (ttl, list(contents))
        self.zones[zone] = data

    def delete_zone(self, zone):
        self.zones.pop(zone, None)

    def rrsets(self, zone):
        return [(n, t, ttl, list(c)) for (n, t), (ttl, c) in self._zone(zone).items()]

    def set_rrset(self, zone, name, kind, ttl, contents):
        data = self._zone(zone)
        if contents:
            if kind == "CNAME" and any(n == name and t not in ("CNAME",) for (n, t) in data) or kind != "CNAME" and (name, "CNAME") in data:
                raise HostingError("The DNS servers refused that: a CNAME cannot share its name with other records.")
            data[(name, kind)] = (ttl, list(contents))
        else:
            data.pop((name, kind), None)
