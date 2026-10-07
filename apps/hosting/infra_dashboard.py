"""
The infrastructure dashboard's data: one card per server (node) with its health, where it is, who rents it, how many hosting accounts
it holds and what its provider can report about it. Read-only. A server that cannot report a figure (a cPanel server cannot report
CPU, memory, disk or network) shows it as not reported - never as a made-up number.
"""
from django.db.models import Count

from apps.products.models import Server, ServerStatus

from .adapters.base import HostingError
from .adapters.capabilities import supports_node
from .adapters.registry import build_adapter
from .models import LIVE_STATUSES

HOT = 90  # a resource at or above this percent is called out
SERVICE_KEYS = ["web", "database", "mail", "dns", "agent", "backups"]
HEALTH_ORDER = {"down": 0, "unreachable": 1, "offline": 2, "degraded": 3, "maintenance": 4, "unknown": 5, "healthy": 6}


def _percent(used, total):
    return round(used * 100 / total) if used is not None and total else None


def _meter(label, used, total, unit, scale=1):
    percent = _percent(used, total)
    return {"label": label, "known": used is not None, "percent": percent,
            "text": f"{round(used / scale, 1):g} / {round(total / scale, 1):g} {unit}" if used is not None and total else "",
            "state": "danger" if percent is not None and percent >= 95 else "warning" if percent is not None and percent >= HOT else "ok"}


def _health(server, status, error):
    if server.status == ServerStatus.OFFLINE:
        return "offline", "Marked offline by staff."
    if server.status == ServerStatus.MAINTENANCE:
        return "maintenance", "Marked as in maintenance by staff."
    if error:
        return "unreachable", error
    if status is None:
        return "unknown", "This server type reports no live health."
    states = [s.state for s in status.services]
    if "down" in states:
        return "down", "A service is down."
    hot = [m for m in (_percent(status.cpu_percent, 100), _percent(status.memory_used_mb, status.memory_total_mb),
                       _percent(status.disk_used_gb, status.disk_total_gb)) if m is not None and m >= HOT]
    if "degraded" in states or hot:
        return "degraded", "A service is struggling." if "degraded" in states else "A resource is nearly full."
    if states and all(s == "unavailable" for s in states):
        return "unknown", "No service state was reported."
    return "healthy", "Everything reported is fine."


def node_card(server, accounts, live):
    status, error = None, ""
    adapter = None
    try:
        adapter = build_adapter(server)
    except Exception:  # an unknown kind or broken saved details must not take the dashboard down
        error = "The saved connection details could not be used."
    if adapter is not None and supports_node(adapter) and server.status == ServerStatus.ACTIVE:
        try:
            status = adapter.node_status()
        except HostingError as exc:
            error = str(exc)[:200]
    health, why = _health(server, status, error)
    services = {s.key: s for s in (status.services if status else [])}
    meters = []
    if status is not None:
        meters = [_meter("CPU", status.cpu_percent, 100, "%"), _meter("Memory", status.memory_used_mb, status.memory_total_mb, "GB", 1024),
                  _meter("Disk", status.disk_used_gb, status.disk_total_gb, "GB")]
    return {"server": server, "health": health, "why": why, "status": status, "meters": meters,
            "load": status.load if status else None, "accounts": accounts, "live_accounts": live,
            "capacity": _meter("Accounts", accounts, server.max_accounts, "") if server.max_accounts else None,
            "services": [services.get(k) for k in SERVICE_KEYS], "service_keys": SERVICE_KEYS,
            "network": (status.network_in_kbps, status.network_out_kbps) if status and status.network_in_kbps is not None else None,
            "provider": server.get_infra_provider_display() if server.infra_provider else "", "kind": server.get_kind_display()}


def dashboard():
    servers = list(Server.objects.annotate(accounts=Count("hosting_accounts")).order_by("name"))
    live = dict(Server.objects.filter(hosting_accounts__status__in=LIVE_STATUSES).annotate(n=Count("hosting_accounts")).values_list("pk", "n"))
    cards = [node_card(s, s.accounts, live.get(s.pk, 0)) for s in servers]
    cards.sort(key=lambda c: (HEALTH_ORDER[c["health"]], c["server"].name))
    counts = {k: sum(1 for c in cards if c["health"] == k) for k in HEALTH_ORDER}
    return {"cards": cards, "counts": counts, "nodes": len(cards), "accounts": sum(c["accounts"] for c in cards),
            "attention": sum(counts[k] for k in ("down", "unreachable", "offline", "degraded"))}
