"""
The isolation baseline (N9, ADR 03/11): what the node itself must look like for tenants to be kept apart, and a check that every account's installed limits and service
hardening are still what the plan says (drift, for example a unit edited by hand, is reported).

The baseline checks read ``/proc`` and ``/sys/fs/cgroup`` and have been **run only against made-up files**, never on a real machine. They are necessary, not sufficient: the real proof
is the cross-tenant probe and the noisy-neighbour load test (``probe.py``, ``loadtest.py``), which must be run on a real node before it takes customers.
"""
import json

from .. import catalog
from . import render
from .website import HandlerError

NEEDED_CONTROLLERS = {"cpu", "memory", "io", "pids"}


def _check(name, ok, detail, why):
    return {"name": name, "ok": bool(ok), "detail": detail, "why": why}


def _sysctl(system, path):
    text = system.read(path)
    return None if text is None else text.strip()


class Isolation:
    def __init__(self, system, websites):
        self.s, self.web = system, websites

    def baseline(self):
        s = self.s
        out = []
        controllers = (s.read("/sys/fs/cgroup/cgroup.controllers") or "").split()
        out.append(_check("cgroup v2 with cpu, memory, io and pids", NEEDED_CONTROLLERS <= set(controllers), " ".join(controllers) or "could not be read",
                          "Without these the plan's CPU, memory, process and I/O limits cannot be enforced."))
        mounts = (s.read("/proc/mounts") or "").splitlines()
        proc = next((m for m in mounts if m.split()[:3][1:2] == ["/proc"]), "")
        out.append(_check("/proc hides other users' processes (hidepid=2)", "hidepid=2" in proc or "hidepid=invisible" in proc, proc or "could not be read",
                          "Otherwise a tenant can list every other tenant's processes and their command lines."))
        home = next((m for m in mounts if m.split()[:3][1:2] == ["/home"]), "")
        out.append(_check("/home is XFS with project quotas", " xfs " in home and ("prjquota" in home or "pquota" in home), home or "no separate /home mount found",
                          "Disk and file limits per account are project quotas."))
        for name, path, wanted, why in (("symlink protection", "/proc/sys/fs/protected_symlinks", "1", "Stops tricks with links in shared folders."),
                                        ("hardlink protection", "/proc/sys/fs/protected_hardlinks", "1", "Stops hard links to files a tenant does not own."),
                                        ("ptrace restricted", "/proc/sys/kernel/yama/ptrace_scope", None, "A tenant must not attach to another process.")):
            value = _sysctl(s, path)
            ok = value == wanted if wanted else value in ("1", "2", "3")
            out.append(_check(name, ok, f"{path} = {value}", why))
        return out

    def accounts(self):
        issues = {}
        for path in (p for p in self.s.list("/var/lib/whp/accounts/") if p.endswith(".json") and ".cpu" not in p):
            state = json.loads(self.s.read(path))
            account = path.rsplit("/", 1)[1][:-5]
            limits = {**render.DEFAULTS, **state["limits"]}
            paths = render.paths(account, state["domain"])
            found = []
            unit = self.s.read(paths["unit"])
            if unit is None:
                found.append("its PHP service file is missing")
            else:
                found += [f"the service lacks {line}" for line in render.UNIT_HARDENING if line not in unit.splitlines()]
                if f"User={account}" not in unit.splitlines():
                    found.append("the PHP service does not run as the account's own user")
            slice_text = self.s.read(paths["slice"])
            if slice_text != render.slice_unit(account, limits):
                found.append("the resource limits on the node differ from the account's plan" if slice_text else "its resource limits file is missing")
            pool = self.s.read(paths["fpm_conf"])
            if pool != render.fpm_conf(account, limits):
                found.append("its PHP settings differ from what WHPanel would write" if pool else "its PHP settings file is missing")
            if self.s.read(paths["vhost"]) is None:
                found.append("its web server settings are missing")
            if found:
                issues[account] = found
        return issues

    def run(self, command, params):
        try:
            catalog.validate(command, params)
        except catalog.InvalidCommand as exc:
            raise HandlerError("invalid", str(exc)) from None
        base, accounts = self.baseline(), self.accounts()
        return "checked", {"baseline": base, "accounts": accounts, "ok": all(c["ok"] for c in base) and not accounts}
