from django.apps import AppConfig


class AffiliatesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.affiliates"
    label = "affiliates"

    def ready(self):
        from . import signals  # noqa: F401  (connects the billing receivers)
