from django.apps import AppConfig


class SupportConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.support"
    label = "support"

    def ready(self):
        from django.db.models.signals import post_delete, post_save

        from . import kb
        from .models import KBArticle, KBCategory

        for model in (KBCategory, KBArticle):  # the public pages read a one-minute copy of the help categories
            post_save.connect(kb.forget_category_list, sender=model, dispatch_uid=f"kb-categories-save-{model.__name__}")
            post_delete.connect(kb.forget_category_list, sender=model, dispatch_uid=f"kb-categories-delete-{model.__name__}")
