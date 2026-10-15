"""
The websites of one hosting plan: how many the plan allows, how many exist, and what each one is. A plan's own domain is the first website; every further website lives on a
domain added to the plan (an addon domain with its own folder). What the plan allows comes from the server (its addon-domain limit), never from a guess.
"""
from apps.core.exceptions import ServiceError

from . import control, site_apps
from .adapters.capabilities import Capability
from .models import HostingStatus, WebsiteApp


def summary(user, account):
    """{"allowed": int or None (no limit), "attached": int, "created": int, "sites": [...], "can_add": bool, "reason": str}"""
    try:
        have = set(control.capabilities_for(account))
    except ServiceError:
        have = set()
    primary = account.domain.lower()
    names = [primary]
    allowed = 1
    if Capability.DOMAINS.value in have:
        try:
            limits = control.perform(user, account, Capability.DOMAINS, "domain_limits")
            allowed = None if limits.max_addon_domains is None else 1 + int(limits.max_addon_domains)
            for info in control.perform(user, account, Capability.DOMAINS, "list_domains"):
                name = info.name.lower()
                if info.kind == "addon" and name not in names:
                    names.append(name)
        except ServiceError:
            allowed = 1
    apps = {a.domain or primary: a for a in WebsiteApp.objects.filter(account=account)}
    for name in apps:  # a website WHPanel made whose domain the server no longer lists is still shown
        if name not in names:
            names.append(name)
    sites = []
    for name in names:
        app = apps.get(name)
        is_primary = name == primary
        sites.append({"domain": name, "primary": is_primary, "app": app,
                      "state": app.state if app else "empty", "kind": app.kind if app else "",
                      "url": site_apps.site_url(account, name)})
    created = sum(1 for s in sites if s["app"] is not None)
    attached = len(names)
    live = account.status == HostingStatus.ACTIVE
    if not live:
        can_add, reason = False, "This hosting plan is not active."
    elif allowed is not None and attached >= allowed:
        can_add, reason = False, f"This plan allows {allowed} website{'s' if allowed != 1 else ''} and all of them are used."
    elif Capability.DOMAINS.value not in have:
        can_add, reason = False, "This server cannot add domains to the plan from here yet."
    else:
        can_add, reason = True, ""
    return {"allowed": allowed, "attached": attached, "created": created, "sites": sites, "can_add": can_add, "reason": reason,
            "free": None if allowed is None else max(allowed - attached, 0)}


def add_domain(user, account, domain, request=None):
    """Attach a new domain to the plan with its own folder, ready for a website. Returns the cleaned domain."""
    import re

    domain = (domain or "").strip().lower()
    if not re.fullmatch(r"(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}", domain):
        raise ServiceError("Enter a domain name such as shop.example.com.", code="bad_domain")
    state = summary(user, account)
    if not state["can_add"]:
        raise ServiceError(state["reason"] or "No more websites can be added to this plan.", code="limit_reached")
    if domain in {s["domain"] for s in state["sites"]}:
        raise ServiceError("That domain is already on this plan.", code="exists")
    control.perform(user, account, Capability.DOMAINS, "add_domain", domain, "addon", site_apps.docroot_path(account, domain),
                    audit_metadata={"domain": domain, "purpose": "website"}, request=request)
    return domain
