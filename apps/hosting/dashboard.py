"""
The customer's Websites dashboard (next-generation roadmap, H1): one card per hosting account with its plan, primary
domain, status, disk and bandwidth meters, SSL and PHP where the provider can report them, and quick actions.

Everything here is read-only and provider-neutral: usage comes from the account's own stored figures, and SSL/PHP are
asked of the hosting-control gateway (``apps.hosting.control``) only when the account's provider declares those
capabilities. A provider that does not (every provider today) simply leaves them off the card - never a placeholder
that looks like data.
"""
from django.urls import reverse

from apps.core.exceptions import ServiceError

from . import control
from .adapters.capabilities import Capability
from .models import HostingStatus

WARN_AT = 80
DANGER_AT = 95


def format_mb(value):
    """``4915`` -> ``"4.8 GB"``; ``120`` -> ``"120 MB"``; ``None`` -> ``None`` (not reported, which is not zero)."""
    if value is None:
        return None
    if value >= 1024:
        text = f"{value / 1024:.1f}".rstrip("0").rstrip(".")
        return f"{text} GB"
    return f"{value} MB"


def meter(used_mb, limit_mb):
    """
    One usage bar. ``percent`` is None when there is nothing to draw (usage unknown, or no limit to measure against);
    ``state`` is "ok", "warning" or "danger" so the colour is never the only signal (the text always says the figures).
    """
    percent = None
    if used_mb is not None and limit_mb:
        percent = min(100, round(used_mb * 100 / limit_mb))
    state = "ok"
    if percent is not None and percent >= DANGER_AT:
        state = "danger"
    elif percent is not None and percent >= WARN_AT:
        state = "warning"
    return {"known": used_mb is not None, "used": format_mb(used_mb),
            "limit": format_mb(limit_mb) if limit_mb else None, "percent": percent, "state": state}


def _optional(user, account, capabilities, capability, operation, *args):
    """What the provider reports for an optional capability, or None when it does not offer it or cannot answer."""
    if capability.value not in capabilities:
        return None
    try:
        return control.perform(user, account, capability, operation, *args)
    except ServiceError:
        return None


def website_card(user, account):
    try:
        capabilities = control.capabilities_for(account)
    except ServiceError:
        capabilities = []
    live = account.status in (HostingStatus.ACTIVE, HostingStatus.SUSPENDED)
    ssl = _optional(user, account, capabilities, Capability.SSL, "get_ssl_status", account.domain) if live else None
    php = _optional(user, account, capabilities, Capability.PHP, "get_php_settings", account.domain) if live else None
    return {
        "account": account,
        "plan": account.product.name,
        "show_usage": live,
        "disk": meter(account.disk_used_mb, account.disk_limit_mb),
        "bandwidth": meter(account.bandwidth_used_mb, account.bandwidth_limit_mb),
        "ssl": ssl,
        "php_version": php.version if php is not None else None,
        "capabilities": capabilities,
        "tone": sum(ord(c) for c in account.domain) % 6,  # which of the six preview-tile colours this website gets
        "manage_url": reverse("hosting_customer:detail", args=[account.pk]),
        "upgrade_url": (reverse("renewals_customer:hosting_upgrade", args=[account.pk])
                        if account.status == HostingStatus.ACTIVE else ""),
    }


# Working accounts first, then the ones waiting or in trouble; each group alphabetical.
_ORDER = {HostingStatus.ACTIVE: 0, HostingStatus.SUSPENDED: 1, HostingStatus.PENDING: 2, HostingStatus.FAILED: 3}


def website_cards(user, accounts):
    ordered = sorted(accounts, key=lambda a: (_ORDER.get(a.status, 9), a.domain.lower(), a.pk))
    return [website_card(user, account) for account in ordered]


# --- The management workspace menu (the left side of every website page) -----------------------------------------

# group, key, label, icon, capability that must be supported, what the screen will do
SECTIONS = [
    ("Files", "files", "File manager", "bi-folder2-open", Capability.FILES,
     "Browse, upload, edit and organise your website files."),
    ("Files", "ftp", "FTP accounts", "bi-hdd-network", Capability.FTP,
     "Create logins for uploading files with an FTP program, limited to a folder."),
    ("Files", "backups", "Backups", "bi-cloud-arrow-up", Capability.BACKUPS,
     "Create, download and restore backups of your website."),
    ("Databases", "databases", "MySQL databases", "bi-database", Capability.DATABASES,
     "Create databases and users, and set their passwords and access."),
    ("Email", "email", "Email accounts", "bi-envelope", Capability.EMAIL,
     "Create mailboxes, forwarders and set quotas for your domain."),
    ("Domains", "domains", "Domains", "bi-globe2", Capability.DOMAINS,
     "Attach domains and subdomains, and set redirects."),
    ("Domains", "dns", "DNS records", "bi-diagram-3", Capability.DNS,
     "Add and edit the DNS records of your domain."),
    ("Domains", "ssl", "SSL certificate", "bi-shield-lock", Capability.SSL,
     "See your certificate, renew it, and force HTTPS."),
    ("Advanced", "php", "PHP settings", "bi-filetype-php", Capability.PHP,
     "Choose the PHP version and tune memory and upload limits."),
    ("Advanced", "cron", "Cron jobs", "bi-clock-history", Capability.CRON,
     "Schedule tasks to run automatically."),
    ("Advanced", "logs", "Logs", "bi-journal-text", Capability.LOGS,
     "Read your website's error and access logs."),
    ("Advanced", "access", "IP blocking", "bi-slash-circle", Capability.ACCESS,
     "Block visitors by IP address or range."),
    ("Advanced", "security", "Security", "bi-shield-check", Capability.SECURITY,
     "Scan for malware, protect folders, manage SSH keys and watch for changed files."),
    ("Advanced", "usage", "Usage", "bi-bar-chart-line", Capability.USAGE,
     "Disk and bandwidth used against your plan."),
]
SECTION_BY_KEY = {key: (group, label, icon, capability, about) for group, key, label, icon, capability, about in SECTIONS}


def workspace_links(request, account):
    """The grouped menu for one website: a section is a real link when the account's provider supports it, otherwise
    it is shown muted with a "Soon" tag (and no address, so there is never a dead link)."""
    from apps.core.portal import Link

    try:
        supported = set(control.capabilities_for(account))
    except ServiceError:
        supported = set()
    links = []
    last_group = None
    for group, key, label, icon, capability, _about in SECTIONS:
        url = reverse("hosting_customer:section", args=[account.pk, key])
        available = capability.value in supported
        links.append(Link(label, url if available else "", active=available and request.path == url, icon=icon,
                          group=group if group != last_group else "", disabled=not available))
        last_group = group
    return links


def section_page(user, account, key):
    """What a section page needs: its names, whether the provider supports it, and (for usage) the meters."""
    group, label, icon, capability, about = SECTION_BY_KEY[key]
    try:
        supported = capability.value in control.capabilities_for(account)
    except ServiceError:
        supported = False
    return {"key": key, "group": group, "label": label, "icon": icon, "about": about, "supported": supported,
            "usage": website_card(user, account) if key == "usage" else None}
