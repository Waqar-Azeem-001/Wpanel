from celery import shared_task

from . import services


@shared_task
def auto_close_resolved_tickets_task():
    """Daily: close tickets that have been Resolved for a week with no reply."""
    return services.auto_close_resolved()
