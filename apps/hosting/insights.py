"""
Resource use over time: a reading of what each account is using every few minutes (the server only tells us the moment), kept for a week, and drawn for the last 24 hours.
Nothing is invented: an account whose server reports nothing simply has no readings and the chart is not shown.
"""
from datetime import timedelta

from django.utils import timezone

from .adapters.capabilities import Capability, supported_capabilities
from .models import HostingAccount, HostingStatus, ResourceReading
from .services import get_adapter

KEEP = timedelta(days=7)
CHART_W, CHART_H = 300, 80
LABELS = {"cpu": "CPU", "memory": "Memory", "processes": "Processes", "entry": "Entry processes", "io": "Disk speed", "iops": "Disk operations"}


def record(account):
    """Take one reading for ``account``; returns the row, or None when its server reports nothing (or does not answer: a slow server is skipped, never raised into the schedule)."""
    try:
        adapter = get_adapter(account.server)
        if Capability.INSIGHTS not in supported_capabilities(adapter):
            return None
        stats = adapter.performance_stats(account.username)
    except Exception:  # noqa: BLE001
        return None
    if not stats:
        return None
    values = {s.key: {"used": s.used, "limit": s.limit, "unit": s.unit, "label": s.label} for s in stats}
    return ResourceReading.objects.create(account=account, taken_at=timezone.now(), values=values)


def record_all():
    """Every active account with a server, one reading each, and the old ones removed. Returns how many readings were taken."""
    taken = 0
    for account in HostingAccount.objects.filter(status=HostingStatus.ACTIVE, server__isnull=False).select_related("server"):
        if record(account) is not None:
            taken += 1
    ResourceReading.objects.filter(taken_at__lt=timezone.now() - KEEP).delete()
    return taken


def series(account, hours=24):
    """{key: {label, unit, limit, now, top, line, area, points}} for each resource that has readings in the last ``hours`` hours (oldest reading on the left)."""
    since = timezone.now() - timedelta(hours=hours)
    rows = list(ResourceReading.objects.filter(account=account, taken_at__gte=since).order_by("taken_at"))
    if len(rows) < 2:
        return {}
    out = {}
    for key in LABELS:
        points = [(r.taken_at, r.values[key]) for r in rows if key in r.values]
        if len(points) < 2:
            continue
        limit = points[-1][1].get("limit") or 0
        peak = max([p[1].get("used", 0) for p in points] + [limit * 0.05, 1e-9])
        top = max(peak, limit) if limit and limit < peak * 20 else peak
        span = (points[-1][0] - points[0][0]).total_seconds() or 1
        coords = []
        for when, value in points:
            x = round((when - points[0][0]).total_seconds() * CHART_W / span, 1)
            y = round(CHART_H - (value.get("used", 0) * (CHART_H - 6) / top) - 3, 1)
            coords.append(f"{x},{y}")
        unit = points[-1][1].get("unit", "")
        out[key] = {"label": LABELS[key], "unit": unit, "limit": round(limit, 1) if limit else None, "now": round(points[-1][1].get("used", 0), 1),
                    "peak": round(peak, 1), "line": " ".join(coords), "area": f"0,{CHART_H} " + " ".join(coords) + f" {CHART_W},{CHART_H}",
                    "since": points[0][0], "count": len(points)}
    return out
