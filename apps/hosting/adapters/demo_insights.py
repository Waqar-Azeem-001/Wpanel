"""
The demo provider's usage history and live performance. Both are sample figures worked out from the account's name and the date, so
they are steady between page loads and tomorrow's picture continues today's: disk grows slowly towards today's real figure, bandwidth
varies from day to day, and CPU, memory, processes and disk activity move with the time of day. The last point of the history is
always today's figure. A real provider (the cPanel adapter, a native node) implements the same ``InsightsCapability`` contract.
"""
from datetime import date, datetime, timedelta, timezone

from .base import HostingError
from .capabilities import PerformanceStat, UsagePoint

MAX_DAYS = 90
CPU_LIMIT, MEMORY_LIMIT_MB, PROCESS_LIMIT, IO_LIMIT_KBPS = 100, 1024, 20, 4096


def _wave(seed, step, spread):
    """A steady pseudo-random number between 0 and ``spread`` for this seed and step."""
    return ((seed * 2654435761 + step * 40503) % 10007) * spread // 10007


class DemoInsights:
    def __init__(self, seed, current_usage):
        self.seed = seed
        self.current_usage = current_usage  # callback returning the provider's usage dict

    def history(self, days):
        if days not in (7, 30, MAX_DAYS):
            raise HostingError("Choose 7, 30 or 90 days.")
        usage = self.current_usage()
        today = date.today()
        disk_now, bandwidth_now = usage["disk_used_mb"], usage["bandwidth_used_mb"]
        daily = max(1, bandwidth_now // max(1, today.day))
        points = []
        for back in range(days - 1, -1, -1):
            day = today - timedelta(days=back)
            grow = 1 - (back * 0.004)  # disk was a little smaller earlier
            disk = int(disk_now * max(0.4, grow))
            bandwidth = daily // 2 + _wave(self.seed, day.toordinal(), daily)
            points.append(UsagePoint(day=day.isoformat(), disk_mb=disk if back else disk_now, bandwidth_mb=bandwidth))
        return points

    def performance(self):
        now = datetime.now(timezone.utc)
        bucket = now.hour * 12 + now.minute // 5  # the picture changes every five minutes
        busy = 20 + (now.hour % 12) * 3  # busier towards midday and evening
        cpu = min(CPU_LIMIT, busy // 2 + _wave(self.seed, bucket, 25))
        memory = 180 + _wave(self.seed, bucket + 1, 400)
        processes = 2 + _wave(self.seed, bucket + 2, 10)
        io = 200 + _wave(self.seed, bucket + 3, 2200)
        return [PerformanceStat("cpu", "CPU", float(cpu), float(CPU_LIMIT), "%"),
                PerformanceStat("memory", "Memory", float(memory), float(MEMORY_LIMIT_MB), "MB"),
                PerformanceStat("processes", "Running processes", float(processes), float(PROCESS_LIMIT), ""),
                PerformanceStat("io", "Disk activity", float(io), float(IO_LIMIT_KBPS), "KB/s")]
