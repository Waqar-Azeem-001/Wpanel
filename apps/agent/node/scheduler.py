"""
The node's own recurring jobs (N9, N7, N5, N8): the daily usage record, certificate renewal, the mail abuse scan, and the weekly backup of the node's configuration. Until now each existed and was tested but
nothing started it; this is what the agent calls on every loop. It remembers when each job last ran (in ``/var/lib/whp/scheduler.json``) so a restart does not repeat a job or skip a day, and one job failing never
stops the others.
"""
import json
import time

STATE = "/var/lib/whp/scheduler.json"
HOUR, DAY, WEEK = 3600, 86400, 604800


class Scheduler:
    def __init__(self, system, jobs, now=time.time):
        """``jobs`` is a list of (name, every_seconds, function). A function returns a short text for the log."""
        self.s, self.jobs, self.now = system, jobs, now

    def _load(self):
        raw = self.s.read(STATE)
        return json.loads(raw) if raw else {}

    def run_due(self):
        """Run every job that is due. Returns [(name, "ok: ..." or "failed: ...")]."""
        state, done = self._load(), []
        for name, every, function in self.jobs:
            if name in state and self.now() - state[name] < every:  # a job that has never run is due now
                continue
            try:
                done.append((name, "ok: " + str(function() or "")))
            except Exception as exc:  # noqa: BLE001 - the others still run, and this one is tried again next hour
                done.append((name, f"failed: {type(exc).__name__}: {str(exc)[:200]}"))
                state[name] = self.now() - every + HOUR  # retry in an hour, not on every loop
                continue
            state[name] = self.now()
        if done:
            self.s.write(STATE, json.dumps(state), 0o600)
        return done


def standard_jobs(machine, accounts, log_lines=None):
    """The jobs a node runs, wired to its handlers. ``accounts()`` lists the account names on the node; ``log_lines()`` gives the mail log lines of the last hour (mail nodes)."""
    jobs = [("usage", DAY, lambda: f"{len(machine.usage.record_day(accounts()))} accounts"),
            ("certificates", DAY, lambda: f"{len(machine.ssl.renew_due(accounts()))} checked or renewed")]
    if log_lines is not None:
        jobs.append(("mail-abuse", HOUR, lambda: f"blocked {machine.mail.scan_logs(log_lines())}"))
    if machine.backups is not None:
        jobs.append(("node-config-backup", WEEK, lambda: machine.backups.run("BACKUP_NODE_CONFIG", {})[0]))
    return jobs
