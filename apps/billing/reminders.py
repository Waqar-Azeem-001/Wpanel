"""
Payment reminders (roadmap events "invoice" and "overdue").

For every open invoice with a balance, one reminder goes out as its due date approaches or passes:

    3 days before the due date, then 1, 7 and 14 days after it.

Only the *most advanced* reminder that is due is ever sent, and each kind is sent to an invoice at most once
(``InvoiceReminder``'s unique constraint), so the daily job is safe to run twice, and if it was not running for a while
it sends one message, not a burst. What happens to an invoice that stays unpaid (suspension) belongs to the service
lifecycle (Phase 12); this module only asks politely.
"""
import logging

from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.notifications import services as notifications

from .models import OPEN_STATUSES, BillingSettings, Invoice, InvoiceReminder

logger = logging.getLogger(__name__)

# (kind, days relative to the due date): negative = before it is due.
SCHEDULE = (("due_soon", -3), ("overdue_1", 1), ("overdue_7", 7), ("overdue_14", 14))


def due_reminder(invoice, *, today):
    """The kind of reminder ``invoice`` is due for today (ignoring whether it was sent), and the days overdue."""
    days = (today - invoice.due_date).days
    eligible = [(kind, offset) for kind, offset in SCHEDULE if days >= offset]
    return (eligible[-1][0], max(days, 0)) if eligible else (None, 0)


def send_invoice_reminders(*, today=None):
    """Send today's reminders. Returns {"sent": n, "skipped": n}; one failing invoice never stops the rest."""
    if not BillingSettings.load().send_payment_reminders:
        return {"sent": 0, "skipped": 0, "disabled": True}
    today = today or timezone.localdate()
    result = {"sent": 0, "skipped": 0}
    invoices = Invoice.objects.filter(status__in=OPEN_STATUSES, due_date__isnull=False).select_related("client")
    for invoice in invoices:
        if invoice.balance_due <= 0:
            continue
        kind, days_overdue = due_reminder(invoice, today=today)
        if kind is None:
            continue
        try:
            with transaction.atomic():
                if InvoiceReminder.objects.filter(invoice=invoice, kind=kind).exists():
                    result["skipped"] += 1
                    continue
                InvoiceReminder.objects.create(invoice=invoice, kind=kind)
                _send(invoice, kind, days_overdue)
            result["sent"] += 1
        except IntegrityError:  # another run got there first
            result["skipped"] += 1
        except Exception:  # noqa: BLE001 - one bad invoice must not stop the others
            logger.exception("Could not send the %s reminder for invoice %s", kind, invoice.pk)
    return result


def _send(invoice, kind, days_overdue):
    link = reverse("billing_customer:invoice_detail", args=[invoice.pk])
    soon = kind == "due_soon"
    notifications.dispatch_client(
        "invoice.due_soon" if soon else "invoice.overdue", invoice.client,
        title=(f"Invoice {invoice.number} is due on {invoice.due_date:%d %b %Y}" if soon
               else f"Invoice {invoice.number} is {days_overdue} day{'s' if days_overdue != 1 else ''} overdue"),
        body=f"{invoice.currency} {invoice.balance_due} outstanding.", link=link,
        context={"invoice": invoice, "days_overdue": days_overdue, "link": link})
