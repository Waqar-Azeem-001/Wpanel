"""
The customer's Usage screen: disk and bandwidth against the plan, how many databases, mailboxes, websites, FTP accounts and cron jobs
are used out of the plan's allowance, warnings when something is nearly full, a history chart, and live resource use. The figures and
limits come from the provider; this screen only shows them. The limits themselves are enforced by the provider, so a full plan refuses
the action no matter which screen asks.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse

from apps.core import portal
from apps.core.exceptions import ServiceError

from .dashboard import DANGER_AT, WARN_AT, format_mb, website_card
from .views import _own_account
from .views_domains import _read, _supports

RANGES = [7, 30, 90]
CHART_W, CHART_H = 300, 90


def _safe(request, account, capability, operation, *args):
    """What an optional capability reports, or None when the provider does not offer it or cannot answer."""
    if not _supports(account, capability):
        return None
    try:
        return _read(request, account, capability, operation, *args)
    except ServiceError:
        return None


def _row(label, icon, used, limit, url=""):
    percent = min(100, round(used * 100 / limit)) if limit else None
    state = "danger" if percent is not None and percent >= DANGER_AT else "warning" if percent is not None and percent >= WARN_AT else "ok"
    return {"label": label, "icon": icon, "used": used, "limit": limit, "percent": percent, "state": state, "url": url}


def _resources(request, account):
    rows = []
    url = lambda name: reverse(f"hosting_customer:{name}", args=[account.pk])  # noqa: E731
    limits = _safe(request, account, "databases", "database_limits")
    listed = _safe(request, account, "databases", "list_databases")
    if limits is not None and listed is not None:
        rows.append(_row("Databases", "bi-database", len(listed), limits.max_databases, url("databases")))
    limits = _safe(request, account, "email", "email_limits")
    listed = _safe(request, account, "email", "list_mailboxes")
    if limits is not None and listed is not None:
        rows.append(_row("Email accounts", "bi-envelope", len(listed), limits.max_mailboxes, url("email")))
    limits = _safe(request, account, "domains", "domain_limits")
    listed = _safe(request, account, "domains", "list_domains")
    if limits is not None and listed is not None:
        extra = sum(1 for d in listed if d.kind in ("addon", "alias"))
        subs = sum(1 for d in listed if d.kind == "subdomain")
        rows.append(_row("Websites (extra domains)", "bi-globe2", extra, limits.max_addon_domains, url("domains")))
        rows.append(_row("Subdomains", "bi-diagram-2", subs, limits.max_subdomains, url("domains")))
    options = _safe(request, account, "ftp", "ftp_options")
    listed = _safe(request, account, "ftp", "list_ftp_accounts")
    if options is not None and listed is not None:
        rows.append(_row("FTP accounts", "bi-hdd-network", len(listed), options.max_accounts, url("ftp")))
    options = _safe(request, account, "cron", "cron_options")
    listed = _safe(request, account, "cron", "list_cron_jobs")
    if options is not None and listed is not None:
        rows.append(_row("Cron jobs", "bi-clock-history", len(listed), options.max_jobs, url("cron")))
    options = _safe(request, account, "backups", "backup_options")
    listed = _safe(request, account, "backups", "list_backups")
    if options is not None and listed is not None:
        rows.append(_row("Backups kept", "bi-cloud-arrow-up", len(listed), options.max_backups, url("backups")))
    return rows


def _chart(points, field):
    """An SVG polyline for one series, scaled to the chart box, plus its highest value."""
    values = [getattr(p, field) for p in points]
    top = max(values + [1])
    last = max(len(values) - 1, 1)
    coordinates = [f"{round(i * CHART_W / last, 1)},{round(CHART_H - (v * (CHART_H - 6) / top) - 3, 1)}" for i, v in enumerate(values)]
    return {"line": " ".join(coordinates), "area": f"0,{CHART_H} " + " ".join(coordinates) + f" {CHART_W},{CHART_H}", "top": format_mb(top),
            "now": format_mb(values[-1]), "from": points[0].day, "to": points[-1].day}


@login_required
def my_hosting_usage(request, pk):
    account = _own_account(request, pk)
    sidebar = portal.service_sidebar(request, account)
    card = website_card(request.user, account)
    try:
        days = int(request.GET.get("days", ""))
    except ValueError:
        days = 0
    days = days if days in RANGES else 30
    points = _safe(request, account, "insights", "usage_history", days) or []
    stats = _safe(request, account, "insights", "performance_stats") or []
    resources = _resources(request, account)
    performance = []
    for s in stats:
        percent = min(100, round(s.used * 100 / s.limit)) if s.limit else 0
        performance.append({"label": s.label, "percent": percent, "unit": s.unit, "used": round(s.used), "limit": round(s.limit),
                            "state": "danger" if percent >= DANGER_AT else "warning" if percent >= WARN_AT else "ok"})
    warnings = []
    for label, m in (("Storage", card["disk"]), ("Bandwidth", card["bandwidth"])):
        if m["percent"] is not None and m["state"] != "ok":
            warnings.append({"level": m["state"], "text": f"{label} is {m['percent']}% used ({m['used']} of {m['limit']})."})
    for r in resources:
        if r["percent"] is not None and r["state"] != "ok":
            warnings.append({"level": r["state"], "text": f"{r['label']}: {r['used']} of {r['limit']} used."})
    for p in performance:
        if p["state"] != "ok":
            warnings.append({"level": p["state"], "text": f"{p['label']} is at {p['percent']}% of what your plan allows right now."})
    if not card["show_usage"]:
        messages.info(request, "Usage appears here once your hosting is active.")
    return render(request, "hosting/customer/usage.html", {
        "account": account, "sidebar": sidebar, "card": card, "resources": resources, "performance": performance,
        "warnings": warnings, "days": days, "ranges": [(d, reverse("hosting_customer:usage", args=[account.pk]) + f"?days={d}") for d in RANGES],
        "disk_chart": _chart(points, "disk_mb") if points else None, "bandwidth_chart": _chart(points, "bandwidth_mb") if points else None,
        "points": list(reversed(points))[:14], "chart_w": CHART_W, "chart_h": CHART_H,
        "logs_url": reverse("hosting_customer:logs", args=[account.pk]) if _supports(account, "logs") else "",
        "upgrade_url": reverse("hosting_customer:addons", args=[account.pk])})
