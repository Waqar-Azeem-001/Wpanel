"""
Payment reminders (roadmap events "invoice" and "overdue").

For every open invoice with a balance, one reminder goes out as its due date approaches or passes, on the days
configured in ``BillingSettings.reminder_schedule_days`` (default: 3 days before the due date, then 1, 7 and 14
days after it - the roadmap's original fixed schedule, now the starting point rather than the only option).

Only the *most advanced* reminder that is due is ever sent, and each configured day is sent to an invoice at most
once (``InvoiceReminder``'s unique constraint, keyed by the day itself), so the daily job is safe to run twice, and
if it was not running for a while it sends one message, not a burst. What happens to an invoice that stays unpaid
(suspension) belongs to the service lifecycle (Phase 12); this module only asks politely.
"""
import logging

from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.notifications import services as notifications

from .models import OPEN_STATUSES, BillingSettings, Invoice, InvoiceReminder

logger = logging.getLogger(__name__)


def due_reminder(invoice, *, today, schedule):
    """The configured day ``invoice`` is due a reminder for today (ignoring whether it was sent), and days overdue."""
    days = (today - invoice.due_date).days
    eligible = [offset for offset in schedule if days >= offset]
    return (max(eligible), max(days, 0)) if eligible else (None, 0)


def send_invoice_reminders(*, today=None):
    """Send today's reminders. Returns {"sent": n, "skipped": n}; one failing invoice never stops the rest."""
    settings_row = BillingSettings.load()
    if not settings_row.send_payment_reminders:
        return {"sent": 0, "skipped": 0, "disabled": True}
    schedule = settings_row.reminder_schedule()
    today = today or timezone.localdate()
    result = {"sent": 0, "skipped": 0}
    invoices = Invoice.objects.filter(status__in=OPEN_STATUSES, due_date__isnull=False).select_related("client")
    for invoice in invoices:
        if invoice.balance_due <= 0:
            continue
        offset, days_overdue = due_reminder(invoice, today=today, schedule=schedule)
        if offset is None:
            continue
        kind = str(offset)
        try:
            with transaction.atomic():
                if InvoiceReminder.objects.filter(invoice=invoice, kind=kind).exists():
                    result["skipped"] += 1
                    continue
                InvoiceReminder.objects.create(invoice=invoice, kind=kind)
                _send(invoice, offset, days_overdue)
            result["sent"] += 1
        except IntegrityError:  # another run got there first
            result["skipped"] += 1
        except Exception:  # noqa: BLE001 - one bad invoice must not stop the others
            logger.exception("Could not send the %s reminder for invoice %s", kind, invoice.pk)
    return result


def _send(invoice, offset, days_overdue):
    link = reverse("billing_customer:invoice_detail", args=[invoice.pk])
    soon = offset < 0
    notifications.dispatch_client(
        "invoice.due_soon" if soon else "invoice.overdue", invoice.client,
        title=(f"Invoice {invoice.number} is due on {invoice.due_date:%d %b %Y}" if soon
               else f"Invoice {invoice.number} is {days_overdue} day{'s' if days_overdue != 1 else ''} overdue"),
        body=f"{invoice.currency} {invoice.balance_due} outstanding.", link=link,
        context={"invoice": invoice, "days_overdue": days_overdue, "link": link})
