"""
The demo provider's machine figures: sample values worked out from the server's hostname and the time, so a few demo servers look
different from each other and steady between page loads. They are not measurements. A real provider (a native node's agent) reports
real ones; a cPanel server reports only what WHM can (see ``WhmApiAdapter.node_status``).
"""
from datetime import datetime, timezone

from .capabilities import NodeService, NodeStatus


def _wave(seed, step, spread):
    return ((seed * 2654435761 + step * 40503) % 10007) * spread // 10007


def demo_node_status(host):
    seed = sum(ord(c) for c in host or "demo")
    now = datetime.now(timezone.utc)
    bucket = now.hour * 12 + now.minute // 5
    strained = seed % 3 == 0  # one demo machine in three runs hotter and has a service in trouble
    cpu = (60 if strained else 18) + _wave(seed, bucket, 25)
    memory_total = 32768
    memory = int(memory_total * ((72 if strained else 38) + _wave(seed, bucket + 1, 18)) / 100)
    disk_total = 800.0
    disk = round(disk_total * ((86 if strained else 41) + _wave(seed, 7, 9)) / 100, 1)
    load = (round(cpu / 25, 2), round(cpu / 28, 2), round(cpu / 31, 2))
    services = [NodeService("web", "Web server", "running"),
                NodeService("database", "Database", "degraded" if strained else "running", "Slow queries." if strained else ""),
                NodeService("mail", "Mail", "running"), NodeService("dns", "DNS", "running"),
                NodeService("agent", "Node agent", "running", "Reporting every minute."),
                NodeService("backups", "Backups", "running", "Last backup finished 6 hours ago.")]
    return NodeStatus(version="WHPanel node (sample data)", cpu_percent=float(cpu), memory_used_mb=memory, memory_total_mb=memory_total,
                      disk_used_gb=disk, disk_total_gb=disk_total, load=load, network_in_kbps=800 + _wave(seed, bucket + 2, 6000),
                      network_out_kbps=1500 + _wave(seed, bucket + 3, 12000), uptime_days=20 + seed % 200, services=services)
