"""Tells the part of WHPanel that asked for a job how it ended. A failing listener must never break the agent's own reply."""
import logging

logger = logging.getLogger(__name__)


def job_finished(job):
    if job.subject.startswith("hosting:"):
        try:
            from apps.hosting import native

            native.job_finished(job)
        except Exception:  # noqa: BLE001 - the job is already recorded; log and carry on
            logger.exception("Could not apply the outcome of agent job %s", job.pk)
