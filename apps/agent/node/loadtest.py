"""
The noisy-neighbour test (N9, ADR 11): one tenant burns CPU, memory, processes, disk, files, I/O or database connections while another tenant's site is measured. The test passes only if the
quiet tenant is not materially slower, never sees errors, and the noisy tenant is visibly held to its own limit. A node does not take customers until this has been run on it.

``judge`` is the pass/fail logic and is tested here with made-up numbers. ``SCENARIOS`` and ``RealLoad`` describe and start the real load; ``RealLoad`` has **never been run** (it needs a real node,
systemd and two real tenants). The numbers it produces on a real node are what ADR 11's "verified by a load test" means: nothing in this repository has yet produced them.
"""
import subprocess

SCENARIOS = {
    "cpu_burn": "Run a busy loop on every core the tenant may use.",
    "memory_hog": "Allocate memory until the tenant is stopped.",
    "fork_bomb": "Start processes as fast as possible.",
    "disk_fill": "Write one big file until the disk limit stops it.",
    "inode_flood": "Create empty files until the file limit stops it.",
    "io_flood": "Read and write the disk flat out.",
    "connection_flood": "Open as many database connections as possible.",
}
SLOWDOWN_LIMIT = 1.5  # the quiet tenant's median response may be at most 1.5 times what it was before
ERROR_LIMIT = 0  # and it must see no errors at all


def judge(scenario, quiet_before_ms, quiet_during_ms, quiet_errors, noisy_was_limited):
    """One scenario. Returns {"scenario", "ok", "problems"}."""
    problems = []
    if quiet_before_ms <= 0:
        problems.append("the quiet tenant could not be measured before the load")
    elif quiet_during_ms > quiet_before_ms * SLOWDOWN_LIMIT:
        problems.append(f"the quiet tenant became {quiet_during_ms / quiet_before_ms:.1f} times slower (allowed {SLOWDOWN_LIMIT})")
    if quiet_errors > ERROR_LIMIT:
        problems.append(f"the quiet tenant saw {quiet_errors} errors")
    if not noisy_was_limited:
        problems.append("the noisy tenant was not held to its limit")
    return {"scenario": scenario, "ok": not problems, "problems": problems}


def run_all(measure_quiet, start_noisy, stop_noisy, noisy_limited, scenarios=None):
    """Run each scenario: measure the quiet tenant, start the load, measure again, stop the load. The four steps are passed in. ``measure_quiet`` returns (median ms, errors)."""
    out = []
    for name in scenarios or SCENARIOS:
        before_ms, _ = measure_quiet()
        start_noisy(name)
        try:
            during_ms, errors = measure_quiet()
            limited = noisy_limited(name)
        finally:
            stop_noisy(name)
        out.append(judge(name, before_ms, during_ms, errors, limited))
    return out


class RealLoad:
    """UNTESTED. Starts the noisy load inside the noisy tenant's own slice with ``systemd-run`` so exactly the plan's limits apply. Only three scenarios have a driver here."""

    COMMANDS = {"cpu_burn": ["python3", "-c", "while True: pass"], "memory_hog": ["python3", "-c", "x=[]\nwhile True: x.append(bytearray(10**7))"],
                "io_flood": ["dd", "if=/dev/zero", "of=/home/{account}/tmp/flood", "bs=1M", "oflag=direct"]}

    def start(self, account, scenario):
        argv = self.COMMANDS.get(scenario)
        if argv is None:
            raise NotImplementedError(f"{scenario} has no driver yet")
        return subprocess.Popen(["systemd-run", f"--slice=whp-{account}.slice", f"--uid={account}", "--collect", "--pipe", *[a.format(account=account) for a in argv]], shell=False)
