"""
Limits on the agent endpoints (security non-negotiable: rate limiting).

* **Failed attempts per source address.** After ``AGENT_FAIL_LIMIT`` requests that were turned away (unknown agent, bad or used enrolment token) within ``AGENT_FAIL_WINDOW`` seconds, that address
  is answered with 429 for the rest of the window, and nothing it sends is even looked at. This stops guessing of tokens. A revoked agent that keeps polling reaches the limit and is slowed
  to one polite refusal, instead of hammering the database.
* **Requests per enrolled node per minute** (``AGENT_NODE_RATE``). A healthy agent makes a few a minute; a runaway one is told to back off with 429 and ``Retry-After``.

Counters live in the Django cache (Redis in production), so they are shared by every web worker. A lockout is per source address as the proxy reports it (X-Forwarded-For).
"""
import time
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse

from apps.core.utils import client_ip

FAIL_STATUSES = (401, 403)  # what counts as a turned-away attempt on every endpoint
ENROLL_FAIL_STATUSES = (400, 401, 403, 404, 409, 410)  # the enrolment call has no identity yet: any refusal of the token counts


class RateLimited(Exception):
    def __init__(self, retry_after):
        super().__init__("Too many requests.")
        self.retry_after = retry_after


def _key(prefix, ident):
    return f"agent-throttle:{prefix}:{ident}"


def _limits():
    return (getattr(settings, "AGENT_FAIL_LIMIT", 20), getattr(settings, "AGENT_FAIL_WINDOW", 600), getattr(settings, "AGENT_NODE_RATE", 600))


def locked(ip):
    limit, window, _ = _limits()
    return bool(ip) and (cache.get(_key("fail", ip)) or 0) >= limit


def record_failure(ip):
    if not ip:
        return
    _, window, _ = _limits()
    key = _key("fail", ip)
    cache.add(key, 0, window)  # the window starts at the first failure
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)


def node_hit(node_id):
    """Count one request from an enrolled node; raise RateLimited when it is over its minute."""
    _, _, rate = _limits()
    minute = int(time.time() // 60)
    key = _key("rate", f"{node_id}:{minute}")
    cache.add(key, 0, 70)
    try:
        used = cache.incr(key)
    except ValueError:
        cache.set(key, 1, 70)
        used = 1
    if used > rate:
        raise RateLimited(60 - int(time.time() % 60))


def too_many(retry_after):
    reply = JsonResponse({"error": "Too many requests. Slow down."}, status=429)
    reply["Retry-After"] = str(max(int(retry_after), 1))
    return reply


def guarded(view, enrolment=False):
    """Wrap an agent endpoint: refuse a locked-out address at once, count turned-away attempts, turn a node over its rate into 429."""
    @wraps(view)
    def inner(request, *args, **kwargs):
        ip = client_ip(request)
        if locked(ip):
            return too_many(_limits()[1])
        try:
            response = view(request, *args, **kwargs)
        except RateLimited as exc:
            return too_many(exc.retry_after)
        if response.status_code in (ENROLL_FAIL_STATUSES if enrolment else FAIL_STATUSES):
            record_failure(ip)
        return response
    return inner


def guarded_enrolment(view):
    return guarded(view, enrolment=True)
