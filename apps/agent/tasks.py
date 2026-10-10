from celery import shared_task

from . import monitoring


@shared_task
def monitor_sweep_task():
    """Every minute: settle each native node's state, alert on changes and, if switched on, restart a failed service within strict limits."""
    return monitoring.sweep()
