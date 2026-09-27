from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TaskRunStatus(models.TextChoices):
    RUNNING = "running", "Running"
    SUCCESS = "success", "Success"
    FAILURE = "failure", "Failure"


class TaskRun(models.Model):
    """
    The last time each Celery task ran (Phase 17: "automation status" / "last run of each beat job, failures",
    roadmap Section 11.2) - one row per task name, kept up to date by ``apps.core.celery_signals``, not a growing
    log (``apps.audit`` already records business events; this is operational telemetry about the jobs themselves).
    A task that has never run simply has no row - the automation page treats that as "never run", not an error.
    """

    name = models.CharField(max_length=200, unique=True, help_text="The Celery task's dotted name.")
    status = models.CharField(max_length=16, choices=TaskRunStatus.choices, default=TaskRunStatus.RUNNING)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    detail = models.TextField(blank=True, help_text="The task's return value, or the exception, truncated.")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
