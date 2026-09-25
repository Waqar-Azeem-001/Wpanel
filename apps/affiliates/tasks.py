from celery import shared_task

from . import services


@shared_task
def approve_commissions_task():
    """Daily: approve the pending commissions whose hold period has ended."""
    result = services.run_approvals()
    return {"approved": result["approved"], "errors": len(result["errors"])}
