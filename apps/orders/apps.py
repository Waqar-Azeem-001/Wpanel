from django.apps import AppConfig


class OrdersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.orders"
    label = "orders"

    def ready(self):
        from apps.billing import integrity

        from . import integrity as orders_integrity
        from . import signals  # noqa: F401  (connects the billing receivers)

        integrity.register_check(orders_integrity.verify_all)
