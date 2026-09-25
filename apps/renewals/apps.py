from django.apps import AppConfig


class RenewalsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.renewals"
    label = "renewals"

    def ready(self):
        from apps.billing import integrity

        from . import integrity as renewals_integrity
        from . import signals  # noqa: F401  (connects the billing receivers)

        integrity.register_check(renewals_integrity.verify_all)
