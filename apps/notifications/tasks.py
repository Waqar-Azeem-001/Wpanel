from celery import shared_task
from django.core.exceptions import ImproperlyConfigured

from . import services


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    dont_autoretry_for=(ImproperlyConfigured,),
    retry_backoff=30,
    retry_backoff_max=60 * 30,
    retry_jitter=True,
    max_retries=5,
)
def deliver_email(self, message_id):
    return services.deliver(message_id)
