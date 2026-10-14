from celery import shared_task

from . import services


@shared_task
def run_site_care_task():
    """Daily: the malware scans and (where the server cannot schedule them itself) the backups that customers switched on and that are due."""
    from . import care

    return care.run_due()


@shared_task
def sync_hosting_usage_task():
    """Daily (only when HOSTING_USAGE_SYNC_ENABLED is on): refresh disk and bandwidth figures from the servers."""
    return services.sync_all_usage()
