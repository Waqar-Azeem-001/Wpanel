"""
Live resource use and visitor statistics on a cPanel server.

* What the account is using right now against its plan's allowance (CPU, memory, processes, input/output) comes from cPanel's own ``ResourceUsage::get_usages``. The ``lve*``
  entries exist on CloudLinux servers; on a server without them the list is simply empty and the screen shows nothing invented.
* A day-by-day history is not something cPanel keeps for us, so ``usage_history`` is empty here: WHPanel keeps its own readings (see ``apps.hosting.insights``).
* Visitor statistics are cPanel's own AWStats. The customer's browser is sent to it through a short-lived, single-use sign-in (``create_user_session``) that goes straight to the
  report for the main domain; no password is involved and the address is never logged or shown.
"""
from urllib.parse import quote, urlsplit

from .base import HostingError
from .capabilities import InsightsCapability, PerformanceStat
from .cpanel_api import CpanelApi

# (usage id, key, label, unit, divisor from the number cPanel gives to the unit shown)
LVE = (
    ("lvecpu", "cpu", "CPU", "%", 1),
    ("lvememphy", "memory", "Memory", "MB", 1048576),
    ("lvenproc", "processes", "Processes", "", 1),
    ("lveep", "entry", "Entry processes", "", 1),
    ("lveio", "io", "Disk speed", "KB/s", 1024),
    ("lveiops", "iops", "Disk operations", "", 1),
)


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class CpanelInsights(CpanelApi, InsightsCapability):
    def usage_history(self, username, days):
        return []

    def performance_stats(self, username):
        rows = {str(i.get("id")): i for i in self._uapi(username, "ResourceUsage", "get_usages") or [] if isinstance(i, dict)}
        found = []
        for usage_id, key, label, unit, divisor in LVE:
            item = rows.get(usage_id)
            if not item:
                continue
            used, limit = _number(item.get("usage")), _number(item.get("maximum"))
            if used is None or not limit:
                continue
            found.append(PerformanceStat(key=key, label=label, used=round(used / divisor, 2), limit=round(limit / divisor, 2), unit=unit))
        return found

    def statistics_access(self, username, domain):
        main = self._account_domains(username)[0]
        name = (domain or main).strip().lower()
        if not name or any(not (c.isalnum() or c in ".-") for c in name):
            raise HostingError("That is not a domain name.")
        data = (self._call("create_user_session", {"user": username, "service": "cpaneld"}).get("data") or {})
        url = str(data.get("url") or "")
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise HostingError("The server did not give a usable sign-in link.")
        return url + ("&" if parts.query else "?") + "goto_uri=" + quote(f"/awstats.pl?config={name}", safe="")
