"""
The demo provider's scheduled jobs: a JSON file per account. A job is never a free shell command: only ``php`` followed by a
script inside the account (and plain arguments) is accepted, the schedule must be a valid five-field expression that does not run
more often than every five minutes, an account may have ten jobs, and a job can be switched off without being deleted. "Run now"
records a sample result - nothing is executed here; a real provider (the cPanel adapter, a native node) implements the same
``CronCapability`` contract.
"""
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .base import HostingError
from .capabilities import CronJobInfo, CronOptions

MAX_JOBS = 10
MIN_INTERVAL = 5
PRESETS = [("Every 5 minutes", "*/5 * * * *"), ("Every 15 minutes", "*/15 * * * *"), ("Every hour", "0 * * * *"),
           ("Every day at 02:00", "0 2 * * *"), ("Every Sunday at 03:00", "0 3 * * 0"), ("First day of the month", "0 4 1 * *")]
FIELDS = [("minute", 0, 59), ("hour", 0, 23), ("day of the month", 1, 31), ("month", 1, 12), ("day of the week", 0, 7)]
SCRIPT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*\.php$")
ARGUMENT = re.compile(r"^[A-Za-z0-9_.=:/-]{1,100}$")
MAX_COMMAND = 300


def _values(text, low, high, label):
    """The numbers one cron field stands for (supports *, lists, ranges and steps)."""
    found = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, _, step_text = part.partition("/")
            if not step_text.isdigit() or int(step_text) < 1:
                raise HostingError(f"The {label} part of the schedule is not valid.")
            step = int(step_text)
        if part == "*":
            start, end = low, high
        elif re.fullmatch(r"\d+-\d+", part):
            start, end = (int(x) for x in part.split("-"))
        elif part.isdigit():
            start = int(part)
            end = high if step > 1 else start
        else:
            raise HostingError(f"The {label} part of the schedule is not valid.")
        if start < low or end > high or start > end:
            raise HostingError(f"The {label} part of the schedule must be between {low} and {high}.")
        found.update(range(start, end + 1, step))
    return found


def check_schedule(schedule):
    parts = (schedule or "").split()
    if len(parts) != 5:
        raise HostingError("A schedule has five parts: minute, hour, day of the month, month and day of the week. "
                           "Pick a common schedule or write them like 0 2 * * *.")
    sets = [_values(p, low, high, label) for p, (label, low, high) in zip(parts, FIELDS)]
    minutes = sorted(sets[0])
    gaps = [b - a for a, b in zip(minutes, minutes[1:])] + [60 - minutes[-1] + minutes[0]]
    if len(minutes) > 1 and min(gaps) < MIN_INTERVAL:
        raise HostingError(f"A job may not run more often than every {MIN_INTERVAL} minutes.")
    return " ".join(parts)


def check_command(command):
    command = " ".join((command or "").split())
    if not command or len(command) > MAX_COMMAND:
        raise HostingError("Enter the PHP script to run, for example: php public_html/cron.php")
    words = command.split(" ")
    if words[0] != "php" or len(words) < 2:
        raise HostingError("Only PHP scripts can be scheduled here. Start the command with php, for example: php public_html/cron.php")
    script = words[1]
    if script.startswith("/") or ".." in script.split("/") or not SCRIPT.match(script):
        raise HostingError("The script must be a .php file inside your hosting folder, written without a leading slash or '..'.")
    if any(not ARGUMENT.match(a) for a in words[2:]):
        raise HostingError("Arguments may only contain letters, numbers and . _ = : / - (no shell symbols).")
    return command


class DemoCron:
    def __init__(self, directory):
        self.file = Path(directory) / "cron.json"

    def _load(self):
        try:
            return json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _save(self, jobs):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(jobs), encoding="utf-8")

    @staticmethod
    def _info(job):
        return CronJobInfo(job_id=job["id"], schedule=job["schedule"], command=job["command"], enabled=job["enabled"],
                           last_run_at=job.get("last_run_at"), last_status=job.get("last_status", ""),
                           last_output=job.get("last_output", ""))

    def _find(self, jobs, job_id):
        for job in jobs:
            if job["id"] == job_id:
                return job
        raise HostingError("That scheduled job no longer exists.")

    def options(self):
        return CronOptions(max_jobs=MAX_JOBS, min_interval_minutes=MIN_INTERVAL, presets=list(PRESETS))

    def jobs(self):
        return [self._info(j) for j in self._load()]

    def create(self, schedule, command):
        schedule, command = check_schedule(schedule), check_command(command)
        jobs = self._load()
        if len(jobs) >= MAX_JOBS:
            raise HostingError(f"Your plan allows {MAX_JOBS} scheduled jobs. Delete one to add another.")
        if any(j["schedule"] == schedule and j["command"] == command for j in jobs):
            raise HostingError("That job already exists.")
        job = {"id": uuid.uuid4().hex[:10], "schedule": schedule, "command": command, "enabled": True}
        jobs.append(job)
        self._save(jobs)
        return job["id"]

    def update(self, job_id, schedule=None, command=None, enabled=None):
        jobs = self._load()
        job = self._find(jobs, job_id)
        if schedule is not None:
            job["schedule"] = check_schedule(schedule)
        if command is not None:
            job["command"] = check_command(command)
        if enabled is not None:
            job["enabled"] = bool(enabled)
        self._save(jobs)

    def delete(self, job_id):
        jobs = self._load()
        self._find(jobs, job_id)
        self._save([j for j in jobs if j["id"] != job_id])

    def run(self, job_id):
        jobs = self._load()
        job = self._find(jobs, job_id)
        script = job["command"].split(" ")[1]
        job["last_run_at"] = datetime.now(timezone.utc).isoformat()
        job["last_status"] = "ok"
        job["last_output"] = f"Ran {script} (sample run: nothing was executed). Finished in 0.4 seconds."
        self._save(jobs)
        return self._info(job)
