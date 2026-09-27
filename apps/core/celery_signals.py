"""
Records the last run of every Celery task into ``TaskRun`` (Phase 17: automation status), from one place - no task
anywhere has to remember to report itself. Connected once, in ``CoreConfig.ready()``.

Beat's own scheduled jobs and anything queued on demand (order fulfilment, email delivery...) are recorded the same
way, so "has this job run, and did it succeed" is answered for all of them without touching each task's own code.
"""
import logging

from celery.signals import task_failure, task_postrun, task_prerun
from django.utils import timezone

logger = logging.getLogger(__name__)

DETAIL_LIMIT = 2000


def _name_of(sender, task):
    if task is not None and getattr(task, "name", None):
        return task.name
    if sender is not None and getattr(sender, "name", None):
        return sender.name
    return "unknown"


def _upsert(name, **fields):
    from .models import TaskRun

    try:
        TaskRun.objects.update_or_create(name=name, defaults=fields)
    except Exception:  # noqa: BLE001 - recording a task's status must never be why the task itself fails
        logger.exception("Could not record the run of task %s", name)


def register():
    @task_prerun.connect(weak=False)
    def _on_start(sender=None, task_id=None, task=None, **kwargs):
        from .models import TaskRunStatus

        _upsert(_name_of(sender, task), status=TaskRunStatus.RUNNING, started_at=timezone.now(), finished_at=None,
               detail="")

    @task_postrun.connect(weak=False)
    def _on_finish(sender=None, task_id=None, task=None, state=None, retval=None, **kwargs):
        from .models import TaskRunStatus

        if state != "SUCCESS":
            return  # a failure is recorded by task_failure below, with the actual exception
        _upsert(_name_of(sender, task), status=TaskRunStatus.SUCCESS, finished_at=timezone.now(),
               detail=str(retval)[:DETAIL_LIMIT])

    @task_failure.connect(weak=False)
    def _on_failure(sender=None, task_id=None, exception=None, **kwargs):
        from .models import TaskRunStatus

        _upsert(_name_of(sender, None), status=TaskRunStatus.FAILURE, finished_at=timezone.now(),
               detail=str(exception)[:DETAIL_LIMIT])
