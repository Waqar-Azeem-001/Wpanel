from celery import shared_task

from . import reminders


@shared_task
def send_invoice_reminders_task():
    """Daily: remind customers about invoices that are due soon or overdue (see ``reminders``)."""
    return reminders.send_invoice_reminders()
