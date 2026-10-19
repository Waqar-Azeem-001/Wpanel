"""The public status page: whether the portal, hosting servers and email are working, in plain words and without any internal detail."""
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

CACHE_KEY = "public.status"
CACHE_SECONDS = 60
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


def components():
    """The rows and the headline. Remembered for a minute so a busy page does not ask the servers every time."""
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached
    rows = [("Customer portal", "ok")]
    for name, probe in (("Hosting servers", _servers), ("Email delivery", _email), ("Billing and renewals", _jobs)):
        try:
            state = probe()
        except Exception:  # noqa: BLE001 - a failing probe is shown as "checking", never as a broken status page
            state = "unknown"
        if state is not None:
            rows.append((name, state))
    states = {state for _, state in rows}
    overall = "down" if "down" in states else "degraded" if "degraded" in states else "unknown" if "unknown" in states else "ok"
    result = {"rows": [(name, state, LABELS[state]) for name, state in rows], "checked": timezone.now(), "overall": overall,
              "overall_label": HEADLINES[overall]}
    cache.set(CACHE_KEY, result, CACHE_SECONDS)
    return result
