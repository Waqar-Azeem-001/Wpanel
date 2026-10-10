"""
Measuring one account's use of the node (N9): disk, files, database size, bandwidth, and what its cgroup is using right now (CPU, memory, processes, disk I/O).

Disk and file counts come from walking the account's folder (links counted but never followed). It is exact but costs time on huge accounts; a real node should read the XFS project
quota report instead, which is cheap (not built). Bandwidth comes from the web server's access log for the account's site (the bytes sent, per day). Live figures come from the
cgroup files systemd keeps for the account's slice. ``record_day`` is the daily job (the agent runs it; WHPanel cannot trigger it) that keeps one row per day for the usage charts.
"""
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .. import catalog
from . import render
from .website import HandlerError

KEEP_DAYS = 400
LOG = re.compile(r'\[(\d{2})/([A-Za-z]{3})/(\d{4}):[^\]]*\] "[^"]*" \d{3} (\d+|-)')
MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
HISTORY = "/var/lib/whp/usage/{account}.json"
CPU_MARK = "/var/lib/whp/usage/{account}.cpu.json"


def measure_tree(root):
    """(bytes, number of files and folders) under ``root``. Links are counted but never followed."""
    total = inodes = 0
    stack = [str(root)]
    while stack:
        with os.scandir(stack.pop()) as entries:
            for entry in entries:
                inodes += 1
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
    return total, inodes


def parse_access_log(lines):
    """{ "YYYY-MM-DD": bytes sent } from lines in the web server's combined log format. Lines that do not fit are ignored."""
    days = {}
    for line in lines:
        m = LOG.search(line)
        if m and m.group(2) in MONTHS:
            day = f"{m.group(3)}-{MONTHS[m.group(2)]:02d}-{m.group(1)}"
            days[day] = days.get(day, 0) + (0 if m.group(4) == "-" else int(m.group(4)))
    return days


def _read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def _int(text):
    return int(text) if text and text.isdigit() else None


class Usage:
    def __init__(self, system, root_for, engine, websites, log_for=None, cgroup_root="/sys/fs/cgroup", now=time.time):
        self.s, self.root_for, self.db, self.web = system, root_for, engine, websites
        self.log_for, self.cgroup, self.now = log_for or (lambda account: None), Path(cgroup_root), now

    def run(self, command, params):
        try:
            p = catalog.validate(command, params)
            return self.report(p)
        except catalog.InvalidCommand as exc:
            raise HandlerError("invalid", str(exc)) from None

    # --- measuring ------------------------------------------------------------------------------------------------------------------
    def _today(self):
        return datetime.fromtimestamp(self.now(), tz=timezone.utc).strftime("%Y-%m-%d")

    def _db_mb(self, account):
        return round(sum(self.db.size_mb(d) for d in self.db.databases(account + "_")), 2)

    def _bandwidth(self, account):
        log = self.log_for(account)
        if log is None or not Path(log).is_file():
            return {}
        with open(log, encoding="utf-8", errors="replace") as handle:
            return parse_access_log(handle)

    def measure(self, account):
        root = self.root_for(account)
        if root is None or not Path(root).is_dir() or self.web._state(account) is None:
            raise HandlerError("not_found", "No such account on this node.")
        size, inodes = measure_tree(root)
        return {"disk_mb": round(size / 1048576, 2), "inodes": inodes, "db_mb": self._db_mb(account), "bandwidth_by_day": self._bandwidth(account)}

    def record_day(self, accounts):
        """The daily job: write today's figures for each account into its history. Returns the accounts measured."""
        done = []
        for account in accounts:
            try:
                m = self.measure(account)
            except HandlerError:
                continue
            raw = self.s.read(HISTORY.format(account=account))
            days = json.loads(raw) if raw else {}
            for day, sent in m["bandwidth_by_day"].items():
                days.setdefault(day, {})["bandwidth_mb"] = round(sent / 1048576, 2)
            today = days.setdefault(self._today(), {})
            today.update(disk_mb=m["disk_mb"], inodes=m["inodes"], db_mb=m["db_mb"])
            today.setdefault("bandwidth_mb", 0.0)
            self.s.write(HISTORY.format(account=account), json.dumps(dict(sorted(days.items())[-KEEP_DAYS:])), 0o600)
            done.append(account)
        return done

    # --- live figures from the account's cgroup ----------------------------------------------------------------------------------------
    def live(self, account):
        base = self.cgroup / f"whp-{account}.slice"
        if not base.is_dir():
            return None
        memory = _int(_read(base / "memory.current"))
        memory_max = _read(base / "memory.max")
        pids = _int(_read(base / "pids.current"))
        pids_max = _read(base / "pids.max")
        usec = next((int(line.split()[1]) for line in (_read(base / "cpu.stat") or "").splitlines() if line.startswith("usage_usec")), None)
        io = sum(int(v) for line in (_read(base / "io.stat") or "").splitlines() for k, v in (kv.split("=") for kv in line.split()[1:] if "=" in kv) if k in ("rbytes", "wbytes"))
        mark_raw = self.s.read(CPU_MARK.format(account=account))
        mark = json.loads(mark_raw) if mark_raw else None
        now = self.now()
        cpu = io_rate = None
        if mark and usec is not None and now > mark["t"]:
            cpu = round(max(0, usec - mark["usec"]) / ((now - mark["t"]) * 1e6) * 100, 1)
            io_rate = round(max(0, io - mark["io"]) / 1024 / (now - mark["t"]), 1)
        if usec is not None:
            self.s.write(CPU_MARK.format(account=account), json.dumps({"usec": usec, "t": now, "io": io}), 0o600)
        return {"cpu_percent": cpu, "memory_mb": None if memory is None else round(memory / 1048576, 1), "memory_limit_mb": None if not memory_max or memory_max == "max" else round(int(memory_max) / 1048576),
                "processes": pids, "processes_limit": None if not pids_max or pids_max == "max" else int(pids_max), "io_kb_s": io_rate}

    # --- the command ------------------------------------------------------------------------------------------------------------------------
    def report(self, p):
        account = p["account"]
        state = self.web._need(account)
        m = self.measure(account)
        raw = self.s.read(HISTORY.format(account=account))
        history = json.loads(raw) if raw else {}
        today = self._today()
        for day, sent in m["bandwidth_by_day"].items():
            history.setdefault(day, {})["bandwidth_mb"] = round(sent / 1048576, 2)
        history.setdefault(today, {}).update(disk_mb=m["disk_mb"], inodes=m["inodes"], db_mb=m["db_mb"])
        month = today[:7]
        used_month = round(sum(v.get("bandwidth_mb", 0) for d, v in history.items() if d.startswith(month)), 2)
        rows = [{"day": d, "disk_mb": round(v.get("disk_mb", 0) + v.get("db_mb", 0)), "bandwidth_mb": round(v.get("bandwidth_mb", 0))} for d, v in sorted(history.items())[-p["days"]:]]
        limits = {**render.DEFAULTS, **state["limits"]}
        return "reported", {"limits": limits, "now": {"disk_mb": m["disk_mb"], "db_mb": m["db_mb"], "inodes": m["inodes"], "bandwidth_month_mb": used_month,
                                                      "bandwidth_today_mb": round(history[today].get("bandwidth_mb", 0), 2)},
                            "history": rows, "live": self.live(account)}
