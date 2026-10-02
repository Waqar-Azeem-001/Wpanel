from celery import shared_task

from . import webhooks
from .models import StorefrontWebhookDelivery


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    dont_autoretry_for=(webhooks.WebhookConfigError,),
    retry_backoff=30,
    retry_backoff_max=60 * 30,
    retry_jitter=True,
    max_retries=5,
)
def deliver_webhook_task(self, delivery_id):
    delivery = StorefrontWebhookDelivery.objects.get(pk=delivery_id)
    webhooks.send(delivery)
