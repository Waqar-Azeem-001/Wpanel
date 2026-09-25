from django.apps import AppConfig


class LifecycleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.lifecycle"
    label = "lifecycle"

    def ready(self):
        from . import signals  # noqa: F401  (connects the renewal receiver)
