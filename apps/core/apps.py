from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"

    def ready(self):
        from . import checks  # noqa: F401  (registers the menu registry check)
        from . import celery_signals
        from . import job_marks  # noqa: F401  (remembers when each background job last finished)

        celery_signals.register()
