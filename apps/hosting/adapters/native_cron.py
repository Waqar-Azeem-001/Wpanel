"""
Scheduled jobs on a native node: the same ``CronCapability`` the customer's Cron screen already uses, served by the node's agent. The rules (php and a script inside the website folder,
five-field schedule, every five minutes at most, ten jobs) are checked here for a quick answer and again on the node. See apps/agent/node/cron.py.
"""
from .base import HostingError
from .capabilities import CronCapability, CronJobInfo, CronOptions
from .cron_rules import check_command, check_schedule


def to_info(j):
    return CronJobInfo(job_id=j["job_id"], schedule=j["schedule"], command=j["command"], enabled=j["enabled"], last_run_at=j.get("last_run_at"),
                       last_status=j.get("last_status") or "", last_output=j.get("last_output") or "")


class NativeCron(CronCapability):
    def cron_options(self, username):
        data = self._call("CRON_LIST", {"account": username})
        return CronOptions(max_jobs=data["max_jobs"], min_interval_minutes=data["min_interval_minutes"], presets=[tuple(x) for x in data["presets"]])

    def list_cron_jobs(self, username):
        return [to_info(j) for j in self._call("CRON_LIST", {"account": username})["jobs"]]

    def create_cron_job(self, username, schedule, command):
        schedule, command = check_schedule(schedule), check_command(command)
        try:
            return self._call("CRON_CREATE", {"account": username, "schedule": schedule, "command": command})["job_id"]
        except KeyError:
            raise HostingError("The server did not return the new job.") from None

    def update_cron_job(self, username, job_id, *, schedule=None, command=None, enabled=None):
        params = {"account": username, "job_id": job_id}
        if schedule is not None:
            params["schedule"] = check_schedule(schedule)
        if command is not None:
            params["command"] = check_command(command)
        if enabled is not None:
            params["enabled"] = "yes" if enabled else "no"
        self._call("CRON_UPDATE", params)

    def delete_cron_job(self, username, job_id):
        self._call("CRON_DELETE", {"account": username, "job_id": job_id})

    def run_cron_job(self, username, job_id):
        return to_info(self._call("CRON_RUN", {"account": username, "job_id": job_id}, wait=120)["job"])
