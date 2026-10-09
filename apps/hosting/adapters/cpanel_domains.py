"""Domains on a cPanel server: ``DomainInfo`` to list them, API 2 ``AddonDomain``, ``SubDomain`` and ``Park`` to add and remove, UAPI ``Mime`` for redirects."""
from urllib.parse import urlsplit

from .base import HostingError
from .capabilities import DOMAIN_KINDS, DomainInfo, DomainLimits, DomainsCapability
from .cpanel_api import CpanelApi, plan_limit
from .dns_rules import clean_domain
from .sandbox import clean_relative



class CpanelDomains(CpanelApi, DomainsCapability):
    def domain_limits(self, username):
        summary = self._summary(username)
        return DomainLimits(max_addon_domains=plan_limit(summary.get("maxaddons")), max_subdomains=plan_limit(summary.get("maxsub")), nameservers=[])

    def _redirects(self, user):
        """{domain: (target, permanent)} from the account's redirects, read defensively."""
        found = {}
        try:
            items = self._uapi(user, "Mime", "list_redirects") or []
        except HostingError:
            return found
        for item in items:
            if not isinstance(item, dict):
                continue
            domain = str(item.get("domain") or item.get("urldomain") or "").lower()
            target = str(item.get("destination") or item.get("targeturl") or "")
            if domain and target:
                found[domain] = (target, str(item.get("type", "permanent")).lower() != "temp" and str(item.get("type", "")).lower() != "temporary")
        return found

    def list_domains(self, username):
        data = self._uapi(username, "DomainInfo", "domains_data", {"format": "hash"}) or {}
        redirects = self._redirects(username)

        def info(item, kind):
            name = str((item.get("domain") if isinstance(item, dict) else item) or "").lower()
            root = self._relative(username, item.get("documentroot")) if isinstance(item, dict) else ""
            target, permanent = redirects.get(name, ("", True))
            return DomainInfo(name=name, kind=kind, document_root=root, redirect_to=target, redirect_permanent=permanent)

        main = data.get("main_domain")
        rows = [info(main if isinstance(main, dict) else {"domain": main}, "primary")] if main else []
        rows += [info(i, "addon") for i in data.get("addon_domains") or []]
        rows += [info(i, "subdomain") for i in data.get("sub_domains") or []]
        rows += [info(i, "alias") for i in data.get("parked_domains") or []]
        return [r for r in rows if r.name]

    def add_domain(self, username, name, kind, document_root):
        if kind not in DOMAIN_KINDS or kind == "primary":
            raise HostingError("Choose what you are adding: another domain, a subdomain or an alias.")
        name = clean_domain(name)
        main, addon, subs, parked = self._account_domains(username)
        if name in {main, *addon, *subs, *parked}:
            raise HostingError("That domain is already on this account.")
        root = clean_relative(document_root) or f"public_html/{name}"
        self._forget(("domains", username))  # the list changes with this call
        if kind == "addon":
            self._api2(username, "AddonDomain", "addaddondomain", {"dir": root, "newdomain": name, "subdomain": name.split(".")[0]})
        elif kind == "subdomain":
            parent = next((d for d in sorted([main, *addon, *parked], key=len, reverse=True) if name.endswith("." + d)), "")
            if not parent:
                raise HostingError("A subdomain must sit under a domain you have.")
            self._api2(username, "SubDomain", "addsubdomain", {"domain": name[: -len(parent) - 1], "rootdomain": parent, "dir": root, "disallowdot": 1})
        else:
            self._api2(username, "Park", "park", {"domain": name, "topdomain": main})

    def remove_domain(self, username, name):
        name = clean_domain(name)
        main, addon, subs, parked = self._account_domains(username)
        if name == main:
            raise HostingError("The main domain cannot be removed.")
        if name in addon:
            self._api2(username, "AddonDomain", "deladdondomain", {"domain": name, "subdomain": f"{name.replace('.', '_')}.{main}"})
        elif name in subs:
            self._api2(username, "SubDomain", "delsubdomain", {"domain": name})
        elif name in parked:
            self._api2(username, "Park", "unpark", {"domain": name})
        else:
            raise HostingError("That domain is not on this account.")
        self._forget(("domains", username))

    def set_redirect(self, username, name, redirect_to, permanent):
        name = self._owned_domain(username, name)
        current = self._redirects(username).get(name)
        if not redirect_to:
            if current:
                self._uapi(username, "Mime", "delete_redirect", {"domain": name, "docroot": "", "src": ""})
            return
        target = str(redirect_to).strip()
        parts = urlsplit(target)
        if parts.scheme not in ("http", "https") or not parts.netloc or len(target) > 500 or any(ord(c) < 32 for c in target):
            raise HostingError("A redirect goes to a full web address (using http or https).")
        self._uapi(username, "Mime", "add_redirect", {"domain": name, "redirect": target, "type": "permanent" if permanent else "temp",
                                                      "redirect_wildcard": 0, "redirect_www": 0, "src": ""})
