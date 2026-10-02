"""
Tells every storefront a plan is already published to "refetch, something changed" when that plan's own name,
description, status, or price changes - connected once, in ``StorefrontsConfig.ready()``, so ``apps.products``
never has to know this app exists (Rule 3: no changes needed in an app that already works and is already tested).
An ordinary product/price save elsewhere in the project costs one extra, cheap "does this have any storefront
links at all" check before doing anything.
"""
from django.db import transaction
from django.db.models.signals import post_delete, post_save


def _notify(product, event):
    from . import services

    for storefront in services.storefronts_for(product):
        services.notify_catalog_changed(storefront, event)


def register():
    from apps.products.models import Product, ProductPrice

    def _on_product_saved(sender, instance, created, **kwargs):
        if created or not instance.storefront_links.exists():
            return
        transaction.on_commit(lambda: _notify(instance, "product.updated"))

    def _on_price_changed(sender, instance, **kwargs):
        if not instance.product.storefront_links.exists():
            return
        transaction.on_commit(lambda: _notify(instance.product, "product.updated"))

    post_save.connect(_on_product_saved, sender=Product, weak=False, dispatch_uid="storefronts_product_saved")
    post_save.connect(_on_price_changed, sender=ProductPrice, weak=False, dispatch_uid="storefronts_price_saved")
    post_delete.connect(_on_price_changed, sender=ProductPrice, weak=False, dispatch_uid="storefronts_price_deleted")
