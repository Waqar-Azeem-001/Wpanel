"""
What a node reports about itself (N10): CPU, memory, disk, load, network, the state of its services, the mail queue and the soonest certificate expiry. The agent sends this in every heartbeat; WHPanel
decides what it means (``apps/agent/monitoring.py``), so the thresholds live in one place.

It reads ``/proc`` and asks ``systemctl is-active``. It has been run only against made-up ``/proc`` files, never on a real machine. A figure that cannot be read is simply left out of the report (WHPanel
treats "not reported" as unknown, never as fine or as broken); a service that cannot be asked about is reported as ``down``.
"""
import shutil

# Which services each kind of node is expected to run. A service the node does not have is not asked about.
SERVICES = {"web": ("httpd", "sshd"), "database": ("mariadb",), "mail": ("postfix", "dovecot", "rspamd")}


def _num(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def service_state(system, unit):
    status, out = system.probe(["systemctl", "is-active", unit])
    if status == 0:
        return "up"
    return "degraded" if out in ("activating", "reloading", "deactivating") else "down"


class Metrics:
    def __init__(self, system, capabilities, disk_path="/home", disk_usage=shutil.disk_usage, cert_days=None):
        self.s, self.capabilities, self.disk_path, self.disk_usage, self.cert_days = system, list(capabilities), disk_path, disk_usage, cert_days
        self._cpu = None  # (busy, total) at the last reading
        self._net = None  # (bytes, errors) at the last reading

    def _cpu_percent(self):
        line = (self.s.read("/proc/stat") or "").splitlines()[:1]
        if not line or not line[0].startswith("cpu "):
            return None
        fields = [int(x) for x in line[0].split()[1:] if x.isdigit()]
        if len(fields) < 5:
            return None
        total, idle = sum(fields), fields[3] + fields[4]
        busy = total - idle
        before, self._cpu = self._cpu, (busy, total)
        if before is None or total <= before[1]:
            return None  # needs two readings
        return round((busy - before[0]) * 100 / (total - before[1]), 1)

    def _memory(self):
        values = {}
        for line in (self.s.read("/proc/meminfo") or "").splitlines():
            key, _, rest = line.partition(":")
            values[key] = _num(rest.split()[0]) if rest.split() else None
        total, available = values.get("MemTotal"), values.get("MemAvailable")
        if not total or available is None:
            return None, None
        return round((total - available) / 1024), round(total / 1024)

    def _network(self):
        errors = 0
        total = 0
        for line in (self.s.read("/proc/net/dev") or "").splitlines()[2:]:
            name, _, rest = line.partition(":")
            cols = rest.split()
            if name.strip() == "lo" or len(cols) < 12:
                continue
            total += int(cols[0]) + int(cols[8])
            errors += int(cols[2]) + int(cols[3]) + int(cols[10]) + int(cols[11])
        before, self._net = self._net, (total, errors)
        return None if before is None else max(0, errors - before[1])

    def collect(self):
        """The heartbeat's figures. Anything that could not be read is absent."""
        out = {}
        cpu = self._cpu_percent()
        if cpu is not None:
            out["cpu_percent"] = cpu
        load = _num(((self.s.read("/proc/loadavg") or "").split() or [None])[0])
        if load is not None:
            out["load_1m"] = load
        used, total = self._memory()
        if used is not None:
            out["memory_used_mb"], out["memory_total_mb"] = used, total
        try:
            disk_total, disk_used, _ = self.disk_usage(self.disk_path)
            out["disk_total_gb"], out["disk_used_gb"] = round(disk_total / 1073741824), round(disk_used / 1073741824)
        except OSError:
            pass
        errors = self._network()
        if errors is not None:
            out["net_errors"] = errors
        services = {}
        for capability in self.capabilities:
            for unit in SERVICES.get(capability, ()):
                services[unit] = service_state(self.s, unit)
        out["services"] = services
        if "mail" in self.capabilities:
            status, listing = self.s.probe(["postqueue", "-j"])
            if status == 0:
                out["mail_queue"] = len([line for line in listing.splitlines() if line.strip()])
        if self.cert_days is not None:
            days = self.cert_days()
            if days is not None:
                out["certs_min_days"] = days
        return out
