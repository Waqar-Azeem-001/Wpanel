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


@shared_task
def retry_stuck_emails_task():
    """Every 10 minutes: try again the emails that are still queued or failed (see ``services.sweep_stuck``)."""
    return services.sweep_stuck()


@shared_task
def purge_sensitive_emails_task():
    """Daily: remove the secret content of emails that could not be delivered within a day."""
    return services.purge_sensitive()
