from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _sync_roles(sender, **kwargs):
    from .roles import sync_roles

    sync_roles()


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"

    def ready(self):
        from . import signals  # noqa: F401

        post_migrate.connect(_sync_roles, sender=self, dispatch_uid="accounts.sync_roles")
