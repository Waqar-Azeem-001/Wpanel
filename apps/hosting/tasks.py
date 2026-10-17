from celery import shared_task

from . import services


@shared_task
def run_site_care_task():
    """Daily: the malware scans and (where the server cannot schedule them itself) the backups that customers switched on and that are due."""
    from . import care

    return care.run_due()


@shared_task
def record_resource_readings_task():
    """Every ten minutes: one reading of what each active account is using (CPU, memory, processes), for the last-24-hours chart."""
    from . import insights

    return insights.record_all()


@shared_task
def sync_hosting_usage_task():
    """Daily (only when HOSTING_USAGE_SYNC_ENABLED is on): refresh disk and bandwidth figures from the servers."""
    return services.sync_all_usage()


@shared_task
def sweep_helper_files_task():
    """Daily: delete any one-file PHP helper that WHPanel left in a website's folder and never used (older than an hour)."""
    from . import php_helpers

    return php_helpers.sweep_all()
