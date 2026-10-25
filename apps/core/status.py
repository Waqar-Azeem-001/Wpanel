"""
The public status page: whether the portal, hosting servers and email are working, in plain words and without any internal detail.

The page never looks at the servers itself. A background job (``refresh``, every 50 seconds, started by Celery beat) does the looking, with a short deadline for each
check, and leaves the answer in the cache. The page only reads that answer, so a hosting server that does not answer can never make a visitor wait.
"""
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as DeadlineMissed
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

CACHE_KEY = "public.status.v2"
KEEP_SECONDS = 3600          # how long the last answer is kept if the job stops
STALE_AFTER = 150            # seconds: three missed refreshes, then the page says the figures are old
PROBE_SECONDS = 8            # the longest one check (a hosting server, the mail log) may take before it counts as "slow"
LABELS = {"ok": "Operational", "degraded": "Degraded", "down": "Outage", "unknown": "Checking"}
HEADLINES = {"ok": "All systems operational", "degraded": "Some systems are slower than usual", "down": "We are fixing an outage",
             "unknown": "Checking our systems"}
BAD = ("down", "unreachable", "offline")


def _servers():
    from apps.hosting import infra_dashboard
    from apps.products.models import Server

    if not Server.objects.filter(status="active").exists():
        return None
    health = [card["health"] for card in infra_dashboard.dashboard()["cards"] if card["server"].status == "active"]
    if health and all(h in BAD for h in health):
        return "down"
    if any(h in BAD or h in ("degraded", "maintenance") for h in health):
        return "degraded"
    return "ok" if health and all(h == "healthy" for h in health) else "unknown"


def _email():
    from apps.notifications.models import EmailMessage

    recent = EmailMessage.objects.filter(created_at__gte=timezone.now() - timedelta(hours=6))
    sent, failed = recent.filter(status="sent").count(), recent.filter(status="failed").count()
    if failed and not sent:
        return "down"
    return "degraded" if failed > sent else "ok"


def _jobs():
    from apps.core import job_marks

    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        return "ok"
    last = job_marks.last_finished("apps.orders.tasks.sweep_stuck_orders_task")
    return "ok" if last and timezone.now() - last < timedelta(minutes=12) else "degraded"


def _bounded(probe, seconds):
    """Run one check, but give up on it after ``seconds``: a server that does not answer is shown as slow, and the refresh still finishes."""
    def run():
        from django.db import connections

        try:
            return probe()
        finally:
            connections.close_all()  # this thread had its own database connection

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(run).result(timeout=seconds)
    except DeadlineMissed:
        return "degraded"
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def refresh():
    """Look at everything (each check with a deadline) and keep the answer. Run by the background job every 50 seconds. Returns what it kept."""
    rows = [("Customer portal", "ok")]
    for name, probe in (("Hosting servers", _servers), ("Email delivery", _email), ("Billing and renewals", _jobs)):
        try:
            state = _bounded(probe, PROBE_SECONDS)
        except Exception:  # noqa: BLE001 - a failing check is shown as "checking", never as a broken status page
            state = "unknown"
        if state is not None:
            rows.append((name, state))
    states = {state for _, state in rows}
    overall = "down" if "down" in states else "degraded" if "degraded" in states else "unknown" if "unknown" in states else "ok"
    result = {"rows": [(name, state, LABELS[state]) for name, state in rows], "checked": timezone.now(), "overall": overall}
    cache.set(CACHE_KEY, result, KEEP_SECONDS)
    return result


def components():
    """What the page shows: the last answer the background job left, with how old it is. Never looks at a server."""
    kept = cache.get(CACHE_KEY)
    if kept is None:  # the job has not run yet (just after a restart): say so rather than guess
        return {"rows": [("Customer portal", "ok", LABELS["ok"]), ("Hosting servers", "unknown", LABELS["unknown"]), ("Email delivery", "unknown", LABELS["unknown"]),
                         ("Billing and renewals", "unknown", LABELS["unknown"])], "checked": None, "age": None, "stale": False, "overall": "unknown",
                "overall_label": HEADLINES["unknown"]}
    age = max(0, int((timezone.now() - kept["checked"]).total_seconds()))
    stale = age > STALE_AFTER
    overall = kept["overall"]
    return {**kept, "age": age, "stale": stale, "overall_label": HEADLINES[overall]}
