"""
The demo provider's domains and DNS: a JSON file per account that behaves like a shared-hosting domain and DNS service - extra
domains, subdomains and aliases under plan limits, a website folder for each, redirects, and one editable DNS zone per domain
with per-type record validation. No real DNS server is behind it; a real provider (the cPanel adapter, a native DNS node)
implements the same ``DomainsCapability`` and ``DnsCapability`` contracts.
"""
import json
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from .base import HostingError
from .capabilities import DNS_RECORD_TYPES, DOMAIN_KINDS, DnsRecord, DomainInfo, DomainLimits
from .dns_rules import clean_domain, normalize_record
from .sandbox import clean_relative

MAX_ADDON_DOMAINS = 5
MAX_SUBDOMAINS = 20
MAX_RECORDS = 200
SERVER_IP = "203.0.113.10"  # a documentation address: this demo server's sample IP
NAMESERVERS = ["ns1.demo-hosting.test", "ns2.demo-hosting.test"]
class DemoDomains:
    def __init__(self, directory, primary, make_folder):
        self.file = Path(directory) / "domains.json"
        self.primary = (primary or "").lower()
        self.make_folder = make_folder  # callback: create a website folder (and a placeholder page) for a new domain

    # --- storage -----------------------------------------------------------------------------------------------------

    def _load(self):
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("domains", [])
        data.setdefault("zones", {})
        if self.primary and self.primary not in data["zones"]:
            data["zones"][self.primary] = self._default_zone(self.primary)
        return data

    def _save(self, data):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(data), encoding="utf-8")

    @staticmethod
    def _default_zone(domain):
        def rec(kind, name, value, priority=None):
            return {"id": uuid.uuid4().hex[:10], "type": kind, "name": name, "value": value, "ttl": 14400, "priority": priority}

        return [rec("A", "@", SERVER_IP), rec("CNAME", "www", domain), rec("A", "mail", SERVER_IP),
                rec("MX", "@", f"mail.{domain}", 10), rec("TXT", "@", "v=spf1 +a +mx ~all")]

    def _all(self, data):
        return [self.primary] + [d["name"] for d in data["domains"]]

    # --- limits and listing --------------------------------------------------------------------------------------------

    def limits(self):
        return DomainLimits(max_addon_domains=MAX_ADDON_DOMAINS, max_subdomains=MAX_SUBDOMAINS, nameservers=list(NAMESERVERS))

    def domains(self):
        data = self._load()
        main = data.get("primary_redirect") or {}
        rows = [DomainInfo(name=self.primary, kind="primary", document_root="public_html", redirect_to=main.get("redirect_to", ""),
                           redirect_permanent=main.get("permanent", True))]
        for d in sorted(data["domains"], key=lambda d: (d["kind"] != "addon", d["name"])):
            rows.append(DomainInfo(name=d["name"], kind=d["kind"], document_root=d["document_root"],
                                   redirect_to=d.get("redirect_to", ""), redirect_permanent=d.get("permanent", True)))
        return rows

    def _find(self, data, name):
        name = (name or "").strip().lower().rstrip(".")
        if name == self.primary:
            return None, name
        for d in data["domains"]:
            if d["name"] == name:
                return d, name
        raise HostingError("That domain is not on this account.")

    # --- adding and removing ---------------------------------------------------------------------------------------------

    def add_domain(self, name, kind, document_root):
        data = self._load()
        name = clean_domain(name)
        if kind not in DOMAIN_KINDS:
            raise HostingError("Choose addon domain, subdomain or alias.")
        existing = self._all(data)
        if name in existing:
            raise HostingError("That domain is already on this account.")
        if kind == "subdomain":
            parent = next((p for p in sorted(existing, key=len, reverse=True) if name.endswith("." + p)), None)
            if not parent:
                raise HostingError("A subdomain must be under one of your own domains, such as blog." + self.primary + ".")
            if sum(1 for d in data["domains"] if d["kind"] == "subdomain") >= MAX_SUBDOMAINS:
                raise HostingError(f"Your plan allows {MAX_SUBDOMAINS} subdomains. Delete one or upgrade your plan.")
        else:
            if any(name.endswith("." + p) or p.endswith("." + name) for p in existing):
                raise HostingError("That domain overlaps one you already have. Add it as a subdomain instead.")
            if sum(1 for d in data["domains"] if d["kind"] in ("addon", "alias")) >= MAX_ADDON_DOMAINS:
                raise HostingError(f"Your plan allows {MAX_ADDON_DOMAINS} extra domains. Delete one or upgrade your plan.")
        if kind == "alias":
            root = "public_html"  # an alias shows the main website
        else:
            root = clean_relative(document_root or f"public_html/{name}")
            if not root or root.split("/")[0] == ".trash":
                raise HostingError("Choose a folder for this domain's website.")
            self.make_folder(root)
        entry = {"name": name, "kind": kind, "document_root": root, "redirect_to": "", "permanent": True}
        data["domains"].append(entry)
        if kind == "subdomain":
            zone = data["zones"][parent] if parent in data["zones"] else None
            if zone is not None:
                relative = name[: -(len(parent) + 1)]
                zone.append({"id": uuid.uuid4().hex[:10], "type": "A", "name": relative, "value": SERVER_IP, "ttl": 14400,
                             "priority": None})
        else:
            data["zones"][name] = self._default_zone(name)
        self._save(data)

    def remove_domain(self, name):
        data = self._load()
        entry, name = self._find(data, name)
        if entry is None:
            raise HostingError("The main domain of an account cannot be removed.")
        data["domains"].remove(entry)
        data["zones"].pop(name, None)
        if entry["kind"] == "subdomain":
            for zone_name, records in data["zones"].items():
                if name.endswith("." + zone_name):
                    relative = name[: -(len(zone_name) + 1)]
                    records[:] = [r for r in records if not (r["name"] == relative and r["type"] == "A" and r["value"] == SERVER_IP)]
        self._save(data)

    def set_redirect(self, name, redirect_to, permanent):
        data = self._load()
        entry, name = self._find(data, name)
        target = (redirect_to or "").strip()
        if target:
            parts = urlsplit(target)
            if parts.scheme not in ("http", "https") or not parts.netloc or len(target) > 500 or any(c.isspace() or ord(c) < 32 for c in target):
                raise HostingError("The redirect must be a full web address that starts with http or https, followed by a colon and two slashes.")
            if (parts.hostname or "").lower() == name:
                raise HostingError("A domain cannot redirect to itself.")
        if entry is None:  # the primary domain keeps its redirect in the same file
            data.setdefault("primary_redirect", {})
            data["primary_redirect"] = {"redirect_to": target, "permanent": bool(permanent)} if target else {}
        else:
            entry["redirect_to"], entry["permanent"] = target, bool(permanent)
        self._save(data)

    def primary_redirect(self):
        return self._load().get("primary_redirect") or {}

    # --- DNS -----------------------------------------------------------------------------------------------------------

    def _zone(self, data, domain):
        domain = (domain or "").strip().lower().rstrip(".")
        if domain not in data["zones"]:
            if domain in self._all(data):
                raise HostingError("A subdomain's DNS records are kept in its parent domain.")
            raise HostingError("That domain is not on this account.")
        return domain, data["zones"][domain]

    def records(self, domain):
        data = self._load()
        domain, zone = self._zone(data, domain)
        order = {t: i for i, t in enumerate(DNS_RECORD_TYPES)}
        return [DnsRecord(record_id=r["id"], type=r["type"], name=r["name"], value=r["value"], ttl=r["ttl"], priority=r["priority"])
                for r in sorted(zone, key=lambda r: (r["name"] != "@", r["name"], order.get(r["type"], 99), r["value"]))]

    def _normal(self, domain, record):
        """The record checked and tidied: (type, name, value, ttl, priority). Raises HostingError with a plain reason."""
        return normalize_record(domain, record)

    def _check_conflicts(self, zone, kind, name, value, priority, ignore_id=""):
        others = [r for r in zone if r["id"] != ignore_id]
        if kind == "CNAME" and any(r["name"] == name for r in others):
            raise HostingError("That name already has other records, and a CNAME cannot share a name with them.")
        if kind != "CNAME" and any(r["name"] == name and r["type"] == "CNAME" for r in others):
            raise HostingError("That name is already a CNAME, which cannot share a name with other records.")
        if any(r["name"] == name and r["type"] == kind and r["value"] == value and r["priority"] == priority for r in others):
            raise HostingError("That record already exists.")

    def add_record(self, domain, record):
        data = self._load()
        domain, zone = self._zone(data, domain)
        if len(zone) >= MAX_RECORDS:
            raise HostingError(f"A zone can hold {MAX_RECORDS} records.")
        kind, name, value, ttl, priority = self._normal(domain, record)
        self._check_conflicts(zone, kind, name, value, priority)
        record_id = uuid.uuid4().hex[:10]
        zone.append({"id": record_id, "type": kind, "name": name, "value": value, "ttl": ttl, "priority": priority})
        self._save(data)
        return record_id

    def update_record(self, domain, record):
        data = self._load()
        domain, zone = self._zone(data, domain)
        current = next((r for r in zone if r["id"] == record.record_id), None)
        if current is None:
            raise HostingError("That record no longer exists.")
        if (record.type or "").upper() != current["type"]:
            raise HostingError("The type of a record cannot be changed. Add a new record and delete this one.")
        kind, name, value, ttl, priority = self._normal(domain, record)
        self._check_conflicts(zone, kind, name, value, priority, ignore_id=current["id"])
        current.update(name=name, value=value, ttl=ttl, priority=priority)
        self._save(data)

    def delete_record(self, domain, record_id):
        data = self._load()
        domain, zone = self._zone(data, domain)
        before = len(zone)
        zone[:] = [r for r in zone if r["id"] != record_id]
        if len(zone) == before:
            raise HostingError("That record no longer exists.")
        self._save(data)
