"""
Scheduled jobs on a native node (the customer's Cron screen). A job is never a free shell: only ``php`` and a script inside the account's own website folder, plain arguments, a
five-field schedule that is not more often than every five minutes, ten jobs per account. The same rules as the demo and cPanel providers (``apps.hosting.adapters.cron_rules``).

How it runs: the node's ``/etc/cron.d/whp-<account>`` holds one line per enabled job, and every line is the same fixed program with two checked words,
``wh-agent cron-run <account> <job id>``. The agent then reads the job from its state file, refuses it if the website is suspended, and starts PHP through ``systemd-run`` in the
account's own slice (its CPU, memory and process limits), as the account's user, with the same sandbox settings as its PHP, for at most an hour. Nothing the customer typed is ever
placed in the cron file or in a shell.

Never run on a real node: cron.d, systemd-run and the account's slice behave as documented but are untested here.
"""
import hashlib
import json
import time
from datetime import datetime, timezone

from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.cron_rules import MAX_JOBS, MIN_INTERVAL, PRESETS, check_command, check_schedule

from .. import catalog
from . import render
from .system import SystemError_
from .website import HandlerError

STATE = "/var/lib/whp/cron/{account}.json"
CRON_D = "/etc/cron.d/whp-{account}"
PHP_BIN = "/opt/remi/php{nodot}/root/usr/bin/php"
MAX_OUTPUT = 500
RUN_LIMIT = 3600


def job_id(schedule, command):
    """The same schedule and command always give the same id, so adding it twice is "already there", never two jobs."""
    return hashlib.sha1(f"{schedule}\n{command}".encode()).hexdigest()[:10]


class Cron:
    def __init__(self, system, websites, now=time.time):
        self.s, self.web, self.now = system, websites, now

    # --- state ---------------------------------------------------------------------------------------------------------------------------
    def _jobs(self, account):
        raw = self.s.read(STATE.format(account=account))
        return json.loads(raw)["jobs"] if raw else []

    def _save(self, account, jobs):
        self.s.write(STATE.format(account=account), json.dumps({"jobs": jobs}, sort_keys=True), 0o600)
        self._render(account, jobs)

    def _render(self, account, jobs):
        lines = [f"{j['schedule']} root /usr/local/bin/wh-agent cron-run {account} {j['id']} >/dev/null 2>&1" for j in jobs if j["enabled"]]
        path = CRON_D.format(account=account)
        if not lines:
            self.s.remove(path)
            return
        head = "# managed by WH Agent - do not edit by hand\nSHELL=/bin/sh\nPATH=/usr/sbin:/usr/bin:/sbin:/bin\nMAILTO=\"\"\n"
        self.s.write(path, head + "\n".join(lines) + "\n", 0o644)

    def _need(self, account):
        state = self.web._state(account)
        if state is None:
            raise HandlerError("not_found", "No such account on this node.")
        return state

    @staticmethod
    def _info(job):
        return {"job_id": job["id"], "schedule": job["schedule"], "command": job["command"], "enabled": job["enabled"], "last_run_at": job.get("last_run_at"),
                "last_status": job.get("last_status", ""), "last_output": job.get("last_output", "")}

    @staticmethod
    def _checked(schedule, command):
        try:
            return check_schedule(schedule), check_command(command)
        except HostingError as exc:
            raise HandlerError("invalid", str(exc)) from None

    # --- entry ---------------------------------------------------------------------------------------------------------------------------
    def run(self, command, params):
        try:
            p = catalog.validate(command, params)
            handler = {"CRON_LIST": self.listing, "CRON_CREATE": self.create, "CRON_UPDATE": self.update, "CRON_DELETE": self.delete, "CRON_RUN": self.run_now}[command]
            return handler(p)
        except catalog.InvalidCommand as exc:
            raise HandlerError("invalid", str(exc)) from None
        except SystemError_ as exc:
            raise HandlerError("system_error", str(exc)[:300]) from None

    def listing(self, p):
        self._need(p["account"])
        return "listed", {"max_jobs": MAX_JOBS, "min_interval_minutes": MIN_INTERVAL, "presets": [list(x) for x in PRESETS], "jobs": [self._info(j) for j in self._jobs(p["account"])]}

    def create(self, p):
        account = p["account"]
        self._need(account)
        schedule, command = self._checked(p["schedule"], p["command"])
        jobs = self._jobs(account)
        wanted = job_id(schedule, command)
        if any(j["id"] == wanted for j in jobs):
            return "exists", {"job_id": wanted}
        if len(jobs) >= MAX_JOBS:
            raise HandlerError("limit_reached", f"Your plan allows {MAX_JOBS} scheduled jobs. Delete one to add another.")
        self._save(account, jobs + [{"id": wanted, "schedule": schedule, "command": command, "enabled": True}])
        return "created", {"job_id": wanted}

    def _find(self, jobs, wanted):
        for job in jobs:
            if job["id"] == wanted:
                return job
        raise HandlerError("not_found", "That scheduled job no longer exists.")

    def update(self, p):
        account = p["account"]
        self._need(account)
        jobs = self._jobs(account)
        job = self._find(jobs, p["job_id"])
        schedule, command = self._checked(p.get("schedule", job["schedule"]), p.get("command", job["command"]))
        enabled = p["enabled"] == "yes" if "enabled" in p else job["enabled"]
        new_id = job_id(schedule, command)
        if new_id != job["id"] and any(j["id"] == new_id for j in jobs):
            raise HandlerError("exists", "That job already exists.")
        job.update({"id": new_id, "schedule": schedule, "command": command, "enabled": enabled})
        self._save(account, jobs)
        return "updated", {"job_id": new_id}

    def delete(self, p):
        account = p["account"]
        self._need(account)
        jobs = self._jobs(account)
        self._find(jobs, p["job_id"])
        self._save(account, [j for j in jobs if j["id"] != p["job_id"]])
        return "deleted", {}

    # --- running a job -----------------------------------------------------------------------------------------------------------------
    def _execute(self, account, state, job):
        words = job["command"].split(" ")
        script, args = words[1], words[2:]
        domain = state["domain"]
        php = PHP_BIN.format(nodot=render.nodot(state["php_version"]))
        argv = ["systemd-run", "--quiet", "--collect", "--wait", "--pipe", "--uid", account, "--gid", account, "--slice", f"whp-{account}.slice",
                "--property=NoNewPrivileges=yes", "--property=PrivateTmp=yes", "--property=ProtectSystem=strict", "--property=ProtectHome=tmpfs",
                f"--property=BindPaths=/home/{account}", f"--property=RuntimeMaxSec={RUN_LIMIT}", f"--working-directory=/home/{account}/sites/{domain}", php, script, *args]
        try:
            output, status = self.s.run(argv) or "", "ok"
        except SystemError_ as exc:
            output, status = str(exc), "failed"
        return status, output[-MAX_OUTPUT:]

    def run_now(self, p):
        account = p["account"]
        state = self._need(account)
        jobs = self._jobs(account)
        job = self._find(jobs, p["job_id"])
        return "ran", {"job": self._record(account, state, jobs, job)}

    def _record(self, account, state, jobs, job):
        if state["suspended"]:
            raise HandlerError("suspended", "This website is suspended, so its scheduled jobs do not run.")
        status, output = self._execute(account, state, job)
        job.update({"last_run_at": datetime.fromtimestamp(self.now(), timezone.utc).isoformat(), "last_status": status, "last_output": output})
        self.s.write(STATE.format(account=account), json.dumps({"jobs": jobs}, sort_keys=True), 0o600)
        return self._info(job)

    def cron_run(self, account, wanted):
        """What ``wh-agent cron-run`` does when cron calls it: run one job if the account, the job and the website all allow it. Quiet when they do not."""
        state = self.web._state(account)
        if state is None or state["suspended"]:
            return None
        jobs = self._jobs(account)
        job = next((j for j in jobs if j["id"] == wanted and j["enabled"]), None)
        if job is None:
            return None
        return self._record(account, state, jobs, job)
