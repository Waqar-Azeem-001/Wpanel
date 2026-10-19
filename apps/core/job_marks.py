"""Remember when each background job last finished well, so the health panel can say whether the schedule is really running instead of only that it exists."""
from datetime import datetime, timezone

from celery.signals import task_postrun
from django.core.cache import cache

PREFIX = "job:last:"


def _name(task):
    return getattr(task, "name", "") or ""


@task_postrun.connect
def mark_finished(sender=None, state=None, **kwargs):
    if state == "SUCCESS" and _name(sender).startswith("apps."):
        try:
            cache.set(PREFIX + _name(sender), datetime.now(timezone.utc).isoformat(), None)
        except Exception:  # noqa: BLE001 - a cache hiccup must never fail the job itself
            pass


def last_finished(task_name):
    """When ``task_name`` last succeeded (aware datetime), or None if it has not since the cache was last empty."""
    raw = cache.get(PREFIX + task_name)
    try:
        return datetime.fromisoformat(raw) if raw else None
    except ValueError:
        return None
