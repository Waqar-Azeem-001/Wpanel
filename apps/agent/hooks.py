"""Tells the part of WHPanel that asked for a job how it ended. A failing listener must never break the agent's own reply."""
import logging

logger = logging.getLogger(__name__)


def job_finished(job):
    if job.subject.startswith("heal:"):
        try:
            from . import monitoring

            monitoring.job_finished(job)
        except Exception:  # noqa: BLE001
            logger.exception("Could not apply the outcome of agent job %s", job.pk)
    elif job.subject.startswith("backupcfg:"):
        try:
            from . import backup_repo

            backup_repo.job_finished(job)
        except Exception:  # noqa: BLE001
            logger.exception("Could not apply the outcome of agent job %s", job.pk)
    elif job.subject.startswith("backup:"):
        try:
            from apps.hosting import native_backups

            native_backups.job_finished(job)
        except Exception:  # noqa: BLE001
            logger.exception("Could not apply the outcome of agent job %s", job.pk)
    elif job.subject.startswith("hosting:"):
        try:
            from apps.hosting import native

            native.job_finished(job)
        except Exception:  # noqa: BLE001 - the job is already recorded; log and carry on
            logger.exception("Could not apply the outcome of agent job %s", job.pk)
