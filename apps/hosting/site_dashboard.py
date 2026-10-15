"""
What the website dashboard shows beyond the tiles: the Essentials (cache, backups, files, databases), the Performance card (Google's desktop and
mobile scores from the last test), and the plan's live use (websites used of allowed, inodes, CPU and memory where the provider reports them).
Nothing here is invented: a figure the provider does not report is simply left out.
"""
from urllib.parse import urlencode

from django.urls import reverse

from . import care, plan_sites
from .models import HostingStatus

SCORE_GOOD, SCORE_OK = 90, 50


def _band(score):
    return "good" if score >= SCORE_GOOD else "ok" if score >= SCORE_OK else "poor"


def performance(account):
    """{"desktop": row or None, "mobile": row or None, "server": last server test or None}. Rows: {score, band, at, figures}."""
    out = {"desktop": None, "mobile": None, "server": None}
    tests = account.speed_tests.all()
    for strategy in ("desktop", "mobile"):
        for t in tests:
            if t.kind == "pagespeed" and (t.report or {}).get("strategy") == strategy:
                out[strategy] = {"score": t.score, "band": _band(t.score), "at": t.created_at, "figures": (t.report or {}).get("figures") or {}}
                break
    for t in tests:
        if t.kind == "server":
            out["server"] = {"score": t.score, "band": _band(t.score), "at": t.created_at, "first_byte_ms": t.first_byte_ms, "total_ms": t.total_ms, "ok": t.ok}
            break
    return out


def essentials(account, app, live):
    """The Essentials card: the few things most people come for. Each is {key, label, icon, url, note, kind} (kind "link" or "post")."""
    from .views_domains import _supports

    pk = account.pk
    url = lambda name, *extra: reverse(f"hosting_customer:{name}", args=[pk, *extra])  # noqa: E731
    rows = []
    if not live:
        return rows
    if _supports(account, "files"):
        rows.append({"key": "files", "label": "File manager", "icon": "bi-folder2-open", "url": url("files"), "kind": "link", "note": "Upload and edit your files"})
    if _supports(account, "databases"):
        rows.append({"key": "databases", "label": "Databases", "icon": "bi-database", "url": url("databases"), "kind": "link", "note": "MySQL databases and users"})
    if _supports(account, "backups"):
        rows.append({"key": "backups", "label": "Backups", "icon": "bi-cloud-arrow-up", "url": url("backups"), "kind": "link", "note": "Create and restore backups"})
    if app is not None and app.kind == "wordpress" and app.state == "ready" and care.can_manage_updates(account):
        rows.append({"key": "cache", "label": "Clear cache", "icon": "bi-lightning-charge", "url": url("cache_clear"), "kind": "post",
                     "note": "Empty WordPress and page-cache plugins"})
        rows.append({"key": "nocache", "label": "Preview without cache", "icon": "bi-eye", "kind": "external",
                     "url": f"{account.site_url.rstrip('/')}/?" + urlencode({"nocache": "1"}), "note": "Open the site, skipping caches"})
    rows.append({"key": "plan", "label": "Hosting plan", "icon": "bi-window-stack", "url": url("plan"), "kind": "link", "note": "Websites, limits and upgrades"})
    return rows


def plan_use(user, account, request):
    """Extra figures for the Plan resource usage card: [{label, used, limit (or None), percent (or None), unit}]."""
    from .views_usage import _safe

    rows = []
    try:
        state = plan_sites.summary(user, account)
        rows.append({"label": "Websites", "used": state["created"], "limit": state["allowed"], "unit": ""})
    except Exception:  # noqa: BLE001
        pass
    for stat in _safe(request, account, "insights", "performance_stats") or []:
        rows.append({"label": stat.label, "used": round(stat.used), "limit": round(stat.limit) if stat.limit else None, "unit": stat.unit})
    for row in rows:
        row["percent"] = min(100, round(row["used"] * 100 / row["limit"])) if row["limit"] else None
        row["state"] = "danger" if row["percent"] is not None and row["percent"] >= 90 else "warning" if row["percent"] is not None and row["percent"] >= 75 else "ok"
    return rows


def build(request, account, card):
    live = account.status == HostingStatus.ACTIVE
    return {
        "essentials": essentials(account, card["app"], live),
        "perf": performance(account) if live and card["app"] is not None else None,
        "plan_use": plan_use(request.user, account, request) if live else [],
        "perf_url": reverse("hosting_customer:care", args=[account.pk]) + "#care-speed",
    }
