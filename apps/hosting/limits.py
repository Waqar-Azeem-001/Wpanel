"""
What happens when a native account gets close to, or past, what its plan allows (N9). The limits themselves are enforced on the node (a full disk refuses writes, a busy loop is held to its CPU
share, a fork bomb stops at the process limit); this is the part that tells people and, for bandwidth, decides the consequence, because bandwidth is measured, not capped.

* From 90 percent of disk or bandwidth: one warning to the customer. At 100 percent: one notice that it is over.
* Bandwidth over the monthly allowance: ``NATIVE_BANDWIDTH_OVER_ACTION`` is ``notify`` (the default: tell the customer) or ``suspend`` (take the site offline until next month or until staff reactivate
  it). A suspension made here is lifted automatically when usage is back under the allowance, and only if it was made here.
* Each state is announced once; moving back to normal resets it, and the bandwidth state resets with the month.
"""
import logging

from django.conf import settings

from apps.core.system import SYSTEM

logger = logging.getLogger(__name__)
WARN_AT = 90


def _state(used, limit):
    if not limit or used is None:
        return "ok"
    percent = used * 100 / limit
    return "over" if percent >= 100 else "warn" if percent >= WARN_AT else "ok"


def check(account, now=None):
    """Look at an account's stored usage and act. Returns the list of things done, for tests and logs."""
    from django.utils import timezone

    from . import services
    from .models import HostingStatus

    now = now or timezone.now()
    done = []
    alerts = dict(account.limit_alerts or {})
    month = now.strftime("%Y-%m")
    if alerts.get("month") != month:  # a new month: bandwidth starts again
        alerts.pop("bandwidth", None)
        alerts["month"] = month
    for resource, used, limit, label in (("disk", account.disk_used_mb, account.disk_limit_mb, "disk space"), ("bandwidth", account.bandwidth_used_mb, account.bandwidth_limit_mb, "bandwidth")):
        state = _state(used, limit)
        before = alerts.get(resource, "ok")
        if state == before:
            continue
        alerts[resource] = state
        if state == "ok":
            continue
        if before == "over" and state == "warn":
            continue  # it dropped from over to warn: nothing new to say
        percent = round(used * 100 / limit)
        action = ""
        if resource == "bandwidth" and state == "over" and getattr(settings, "NATIVE_BANDWIDTH_OVER_ACTION", "notify") == "suspend" and account.status == HostingStatus.ACTIVE:
            try:
                services.suspend_account(SYSTEM, account, reason="Your monthly bandwidth allowance has been used up.")
                alerts["suspended_for"] = "bandwidth"
                action = "Your website has been taken offline until next month or until you upgrade your plan."
                done.append("suspended")
            except Exception:  # noqa: BLE001 - a failed suspension is logged; the warning still goes out
                logger.exception("Could not suspend account %s for bandwidth", account.pk)
        services._announce(account, "hosting.limit_warning", f"{account.domain} is {'over' if state == 'over' else 'close to'} its {label} limit",
                           resource=label, percent=percent, used=used, limit=limit, state=state, action=action)
        done.append(f"{resource}:{state}")
    if alerts.get("suspended_for") == "bandwidth" and account.status == HostingStatus.SUSPENDED and _state(account.bandwidth_used_mb, account.bandwidth_limit_mb) != "over":
        try:
            services.unsuspend_account(SYSTEM, account)
            alerts.pop("suspended_for", None)
            done.append("unsuspended")
        except Exception:  # noqa: BLE001
            logger.exception("Could not reactivate account %s", account.pk)
    if alerts != (account.limit_alerts or {}):
        account.limit_alerts = alerts
        account.save(update_fields=["limit_alerts", "updated_at"])
    return done
