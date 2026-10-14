"""
The demo provider's scheduled jobs: a JSON file per account. A job is never a free shell command: only ``php`` followed by a
script inside the account (and plain arguments) is accepted, the schedule must be a valid five-field expression that does not run
more often than every five minutes, an account may have ten jobs, and a job can be switched off without being deleted. "Run now"
records a sample result - nothing is executed here; a real provider (the cPanel adapter) implements the same
``CronCapability`` contract.
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .base import HostingError
from .capabilities import CronJobInfo, CronOptions
from .cron_rules import MAX_JOBS, MIN_INTERVAL, PRESETS, check_command, check_schedule


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
