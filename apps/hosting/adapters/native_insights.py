"""
Usage history and live resource use for native hosting (N9): the same ``InsightsCapability`` the customer's Usage page already shows, read from the node's own measurements (the account's folder,
databases, the web server's access log and the account's cgroup). The limits shown are the real ones installed on the node.

I/O is shared by weight rather than capped, so there is no figure to compare it with and it is not shown.
"""
from apps.agent import services as agent
from apps.core.exceptions import ServiceError

from .base import HostingError
from .capabilities import InsightsCapability, PerformanceStat, UsagePoint


class NativeInsights(InsightsCapability):
    def _report(self, username, days):
        try:
            return agent.call(self.server, "USAGE_REPORT", {"account": username, "days": max(1, min(int(days), 90))}, wait=60)
        except ServiceError as exc:
            raise HostingError(exc.message) from exc

    def usage_history(self, username, days):
        return [UsagePoint(day=r["day"], disk_mb=r["disk_mb"], bandwidth_mb=r["bandwidth_mb"]) for r in self._report(username, days)["history"]]

    def performance_stats(self, username):
        data = self._report(username, 1)
        live, limits = data["live"], data["limits"]
        if not live:
            return []  # the node has no live figures for this account yet
        stats = []
        if live.get("cpu_percent") is not None:
            stats.append(PerformanceStat("cpu", "CPU", live["cpu_percent"], limits["cpu_percent"], "%"))
        if live.get("memory_mb") is not None:
            stats.append(PerformanceStat("memory", "Memory", live["memory_mb"], limits["memory_mb"], "MB"))
        if live.get("processes") is not None:
            stats.append(PerformanceStat("processes", "Processes", live["processes"], limits["processes"], ""))
        return stats
