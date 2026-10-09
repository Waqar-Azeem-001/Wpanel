"""
Scheduled jobs on a cPanel server, through API 2 ``Cron`` (list, add, edit, remove). The same rules as everywhere apply first: only a PHP script
inside the account, and a schedule that is valid and not more frequent than the plan allows - so cron never becomes a shell.
"""
from .base import HostingError
from .capabilities import CronCapability, CronJobInfo, CronOptions
from .cpanel_api import CpanelApi
from .cron_rules import MAX_JOBS, MIN_INTERVAL, PRESETS, check_command, check_schedule

FIELDS = ["minute", "hour", "day", "month", "weekday"]
DISABLED = "#WHP-DISABLED# "  # a switched-off job stays in the crontab as a comment, so it can be switched back on


class CpanelCron(CpanelApi, CronCapability):
    def cron_options(self, username):
        return CronOptions(max_jobs=MAX_JOBS, min_interval_minutes=MIN_INTERVAL, presets=list(PRESETS))

    def _lines(self, user):
        rows = []
        for item in self._api2(user, "Cron", "listcron") or []:
            if not isinstance(item, dict) or not item.get("command"):
                continue
            command = str(item["command"])
            enabled = not command.startswith(DISABLED)
            command = command[len(DISABLED):] if not enabled else command
            schedule = " ".join(str(item.get(f, "*")) for f in FIELDS)
            rows.append({"id": str(item.get("linekey") or item.get("count")), "line": str(item.get("count")), "schedule": schedule,
                         "command": command, "enabled": enabled})
        return rows

    def list_cron_jobs(self, username):
        return [CronJobInfo(job_id=r["id"], schedule=r["schedule"], command=r["command"], enabled=r["enabled"]) for r in self._lines(username)]

    def _find(self, user, job_id):
        for row in self._lines(user):
            if row["id"] == job_id:
                return row
        raise HostingError("That scheduled job no longer exists.")

    def _add(self, user, schedule, command, enabled=True):
        minute, hour, day, month, weekday = schedule.split()
        self._api2(user, "Cron", "add_line", {"minute": minute, "hour": hour, "day": day, "month": month, "weekday": weekday,
                                              "command": command if enabled else DISABLED + command})

    def create_cron_job(self, username, schedule, command):
        schedule, command = check_schedule(schedule), check_command(command)
        rows = self._lines(username)
        if len(rows) >= MAX_JOBS:
            raise HostingError(f"Your plan allows {MAX_JOBS} scheduled jobs. Delete one to add another.")
        if any(r["schedule"] == schedule and r["command"] == command for r in rows):
            raise HostingError("That job already exists.")
        self._add(username, schedule, command)
        added = [r for r in self._lines(username) if r["schedule"] == schedule and r["command"] == command]
        return added[-1]["id"] if added else ""

    def update_cron_job(self, username, job_id, *, schedule=None, command=None, enabled=None):
        row = self._find(username, job_id)
        new_schedule = check_schedule(schedule) if schedule is not None else row["schedule"]
        new_command = check_command(command) if command is not None else row["command"]
        self._api2(username, "Cron", "remove_line", {"line": row["line"]})
        self._add(username, new_schedule, new_command, row["enabled"] if enabled is None else bool(enabled))

    def delete_cron_job(self, username, job_id):
        self._api2(username, "Cron", "remove_line", {"line": self._find(username, job_id)["line"]})

    def run_cron_job(self, username, job_id):
        self._unsupported("Running a job on demand")
