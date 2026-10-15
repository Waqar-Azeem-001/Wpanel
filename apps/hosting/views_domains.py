"""
The customer's Domains and DNS screens: extra domains, subdomains and aliases (each with its own website folder), redirects, and
the DNS records of every domain. Like the other management screens, every operation goes through the hosting-control gateway
(ownership, the allowlist, the live-account check and the audit trail are shared) and the provider enforces the plan limits and
validates every name and record. Changing DNS can take a site or its mail offline, so every removal asks first.
"""
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core import portal
from apps.core.exceptions import ServiceError

from . import control, dashboard
from .adapters.capabilities import DNS_MAX_TTL, DNS_MIN_TTL, DNS_RECORD_TYPES, DnsRecord
from .views import _own_account

TTL_CHOICES = [(300, "5 minutes"), (3600, "1 hour"), (14400, "4 hours"), (43200, "12 hours"), (86400, "1 day")]
SPF_VALUE = "v=spf1 +a +mx ~all"


def _supports(account, capability):
    try:
        return capability in control.capabilities_for(account)
    except ServiceError:
        return False


def _run(request, account, capability, operation, *args, metadata=None):
    return control.perform(request.user, account, capability, operation, *args, request=request, audit_metadata=metadata)


def _read(request, account, capability, operation, *args):
    return control.perform(request.user, account, capability, operation, *args)


def _domains_url(account):
    return reverse("hosting_customer:domains", args=[account.pk])


def _dns_url(account, domain=""):
    url = reverse("hosting_customer:dns", args=[account.pk])
    return url + ("?" + urlencode({"domain": domain}) if domain else "")


def _parent_zone(name, zone_domains):
    """The domain whose DNS zone holds this name's records (a domain with its own zone is its own parent)."""
    if name in zone_domains:
        return name
    return next((z for z in sorted(zone_domains, key=len, reverse=True) if name.endswith("." + z)), "")


# --- Domains --------------------------------------------------------------------------------------------------------

DOMAIN_PAGES = {
    "subdomains": ("Subdomains", "bi-diagram-2"),
    "parked": ("Parked domains", "bi-signpost-2"),
    "redirects": ("Redirects", "bi-signpost-split"),
}


def _return_url(account, name):
    """Where a form goes back to: one of the three focused pages, or the all-in-one Domains page."""
    if name in DOMAIN_PAGES:
        return reverse("hosting_customer:domain_page", args=[account.pk, name])
    return _domains_url(account)


@login_required
def my_hosting_domain_page(request, pk, page):
    """Subdomains, Parked domains and Redirects: one focused page each, over the same domains engine as the all-in-one page."""
    from django.http import Http404

    if page not in DOMAIN_PAGES:
        raise Http404
    account = _own_account(request, pk)
    sidebar = portal.service_sidebar(request, account)
    if not _supports(account, "domains"):
        return render(request, "hosting/customer/section.html", {
            "account": account, "sidebar": sidebar, "section": dashboard.section_page(request.user, account, "domains")})
    try:
        limits = _read(request, account, "domains", "domain_limits")
        domains = _read(request, account, "domains", "list_domains")
    except ServiceError as exc:
        messages.error(request, exc.message)
        limits, domains = None, []
    zone_domains = [d.name for d in domains if d.kind != "subdomain"]
    files_url = reverse("hosting_customer:files", args=[account.pk])

    def row(d):
        return {"d": d, "dns": _dns_url(account, _parent_zone(d.name, zone_domains)),
                "files": files_url + "?" + urlencode({"path": d.document_root}) if d.document_root else ""}

    subs = [row(d) for d in domains if d.kind == "subdomain"]
    aliases = [row(d) for d in domains if d.kind == "alias"]
    addons = [row(d) for d in domains if d.kind == "addon"]
    title, icon = DOMAIN_PAGES[page]
    return render(request, "hosting/customer/domain_page.html", {
        "account": account, "sidebar": sidebar, "limits": limits, "page": page, "title": title, "icon": icon, "zone_domains": zone_domains,
        "subs": subs, "aliases": aliases, "addons": addons, "all_rows": [row(d) for d in domains],
        "sub_full": bool(limits and limits.max_subdomains is not None and len(subs) >= limits.max_subdomains),
        "extra_full": bool(limits and limits.max_addon_domains is not None and len(aliases) + len(addons) >= limits.max_addon_domains),
        "action_url": reverse("hosting_customer:domain_action", args=[account.pk]), "all_url": _domains_url(account),
        "plan_url": reverse("hosting_customer:plan", args=[account.pk]), "dns_url": _dns_url(account)})


@login_required
def my_hosting_domains(request, pk):
    account = _own_account(request, pk)
    sidebar = portal.service_sidebar(request, account)
    if not _supports(account, "domains"):
        return render(request, "hosting/customer/section.html", {
            "account": account, "sidebar": sidebar, "section": dashboard.section_page(request.user, account, "domains")})
    try:
        limits = _read(request, account, "domains", "domain_limits")
        domains = _read(request, account, "domains", "list_domains")
    except ServiceError as exc:
        messages.error(request, exc.message)
        limits, domains = None, []
    zone_domains = [d.name for d in domains if d.kind != "subdomain"]
    files_url = reverse("hosting_customer:files", args=[account.pk])
    rows = [{"d": d, "dns": _dns_url(account, _parent_zone(d.name, zone_domains)),
             "files": files_url + "?" + urlencode({"path": d.document_root}) if d.document_root else ""} for d in domains]
    extra = [d for d in domains if d.kind in ("addon", "alias")]
    subs = [d for d in domains if d.kind == "subdomain"]
    return render(request, "hosting/customer/domains.html", {
        "account": account, "sidebar": sidebar, "limits": limits, "rows": rows, "zone_domains": zone_domains,
        "extra_count": len(extra), "sub_count": len(subs),
        "extra_full": bool(limits and limits.max_addon_domains is not None and len(extra) >= limits.max_addon_domains),
        "sub_full": bool(limits and limits.max_subdomains is not None and len(subs) >= limits.max_subdomains)})


@require_POST
@login_required
def my_hosting_domain_action(request, pk):
    account = _own_account(request, pk)
    post = request.POST
    action = post.get("action", "")
    try:
        if action == "add":
            kind = post.get("kind", "")
            name = post.get("name", "").strip().lower()
            if kind == "subdomain":  # the person types the first part and picks which domain it sits under
                name = f"{name.rstrip('.')}.{post.get('parent', '').strip().lower()}"
            _run(request, account, "domains", "add_domain", name, kind, post.get("document_root", "").strip(),
                 metadata={"domain": name, "kind": kind})
            messages.success(request, f"{name} was added. Its website folder is ready in the file manager.")
        elif action == "remove":
            name = post.get("name", "")
            _run(request, account, "domains", "remove_domain", name, metadata={"domain": name})
            messages.success(request, f"{name} was removed. Its files were left in place.")
        elif action in ("redirect", "remove_redirect"):
            name = post.get("name", "")
            target = "" if action == "remove_redirect" else post.get("redirect_to", "").strip()
            if target and "://" not in target:
                target = "https" + "://" + target  # a bare "www.example.com" is what people type; the server wants the full address
            permanent = post.get("kind", "301") != "302"
            _run(request, account, "domains", "set_redirect", name, target, permanent,
                 metadata={"domain": name, "redirect": bool(target), "permanent": permanent})
            messages.success(request, "Redirect saved." if target else "Redirect removed.")
        else:
            raise ServiceError("That action is not available.", code="unknown_action")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect(_return_url(account, post.get("return_to", "")))


# --- DNS ------------------------------------------------------------------------------------------------------------

def _record_from(post, record_id=""):
    priority = post.get("priority", "").strip()
    return DnsRecord(record_id=record_id, type=post.get("type", ""), name=post.get("name", ""), value=post.get("value", ""),
                     ttl=post.get("ttl") or 14400, priority=priority if priority != "" else None)


@login_required
def my_hosting_dns(request, pk):
    account = _own_account(request, pk)
    sidebar = portal.service_sidebar(request, account)
    if not _supports(account, "dns"):
        return render(request, "hosting/customer/section.html", {
            "account": account, "sidebar": sidebar, "section": dashboard.section_page(request.user, account, "dns")})
    try:
        domains = _read(request, account, "domains", "list_domains") if _supports(account, "domains") else []
        limits = _read(request, account, "domains", "domain_limits") if _supports(account, "domains") else None
        zones = [d.name for d in domains if d.kind != "subdomain"]
        chosen = request.GET.get("domain") or (zones[0] if zones else "")
        records = _read(request, account, "dns", "list_dns_records", chosen) if chosen else []
    except ServiceError as exc:
        messages.error(request, exc.message)
        return redirect(_domains_url(account) if _supports(account, "domains") else reverse("hosting_customer:detail", args=[account.pk]))
    has = {(r.type, r.name) for r in records}
    return render(request, "hosting/customer/dns.html", {
        "account": account, "sidebar": sidebar, "zones": zones, "domain": chosen, "records": records,
        "record_types": DNS_RECORD_TYPES, "ttl_choices": TTL_CHOICES, "min_ttl": DNS_MIN_TTL, "max_ttl": DNS_MAX_TTL,
        "nameservers": limits.nameservers if limits else [], "action_url": reverse("hosting_customer:dns_action", args=[account.pk]),
        "domains_url": _domains_url(account) if _supports(account, "domains") else "",
        "has_spf": any(r.type == "TXT" and r.name == "@" and r.value.startswith("v=spf1") for r in records),
        "has_dmarc": ("TXT", "_dmarc") in has, "spf_value": SPF_VALUE,
        "dns_urls": [(z, _dns_url(account, z)) for z in zones]})


@require_POST
@login_required
def my_hosting_dns_action(request, pk):
    account = _own_account(request, pk)
    post = request.POST
    action, domain = post.get("action", ""), post.get("domain", "")
    try:
        if action == "add":
            record = _record_from(post)
            _run(request, account, "dns", "add_dns_record", domain, record,
                 metadata={"domain": domain, "type": record.type.upper(), "name": record.name or "@"})
            messages.success(request, "Record added. DNS changes can take a while to reach everyone.")
        elif action == "update":
            record = _record_from(post, post.get("record_id", ""))
            _run(request, account, "dns", "update_dns_record", domain, record,
                 metadata={"domain": domain, "type": record.type.upper(), "name": record.name or "@"})
            messages.success(request, "Record saved. DNS changes can take a while to reach everyone.")
        elif action == "delete":
            _run(request, account, "dns", "delete_dns_record", domain, post.get("record_id", ""),
                 metadata={"domain": domain, "record": post.get("record_id", "")})
            messages.success(request, "Record deleted.")
        elif action == "mail_spf":
            _run(request, account, "dns", "add_dns_record", domain, DnsRecord("", "TXT", "@", SPF_VALUE, 14400),
                 metadata={"domain": domain, "type": "TXT", "name": "@", "purpose": "spf"})
            messages.success(request, "SPF record added.")
        elif action == "mail_dmarc":
            value = f"v=DMARC1; p=none; rua=mailto:postmaster@{domain}"
            _run(request, account, "dns", "add_dns_record", domain, DnsRecord("", "TXT", "_dmarc", value, 14400),
                 metadata={"domain": domain, "type": "TXT", "name": "_dmarc", "purpose": "dmarc"})
            messages.success(request, "DMARC record added. It only reports for now; tighten the policy once you have checked the reports.")
        else:
            raise ServiceError("That action is not available.", code="unknown_action")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect(_dns_url(account, domain))
