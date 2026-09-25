from celery import shared_task

from . import services


@shared_task
def run_lifecycle_task():
    """Daily: carry out due cancellations, send final notices, suspend accounts that stayed unpaid, and terminate
    those that stayed suspended if that is switched on."""
    result = services.run_lifecycle()
    return {key: (len(value) if isinstance(value, list) else value) for key, value in result.items()}
