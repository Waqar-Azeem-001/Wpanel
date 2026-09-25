from celery import shared_task

from . import services


@shared_task
def generate_renewal_invoices_task():
    """Daily: invoice every service that is about to expire (see ``services.generate_renewal_invoices``)."""
    result = services.generate_renewal_invoices()
    return {"hosting": result["hosting"], "domains": result["domains"], "errors": len(result["errors"])}
