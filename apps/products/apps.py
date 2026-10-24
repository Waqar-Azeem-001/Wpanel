from django.apps import AppConfig


class ProductsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.products"
    label = "products"

    def ready(self):
        from django.db.models.signals import post_delete, post_save

        from . import categories
        from .models import Addon, AddonPrice, Product, ProductPrice, StoreCategory

        for model in (Product, ProductPrice, Addon, AddonPrice, StoreCategory):  # the public store reads a one-minute snapshot of these
            post_save.connect(categories.forget_snapshot, sender=model, dispatch_uid=f"catalog-snapshot-save-{model.__name__}")
            post_delete.connect(categories.forget_snapshot, sender=model, dispatch_uid=f"catalog-snapshot-delete-{model.__name__}")
