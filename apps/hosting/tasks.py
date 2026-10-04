from celery import shared_task

from . import services


@shared_task
def sync_hosting_usage_task():
    """Daily (only when HOSTING_USAGE_SYNC_ENABLED is on): refresh disk and bandwidth figures from the servers."""
    return services.sync_all_usage()
