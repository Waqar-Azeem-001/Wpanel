"""
Running and exporting reports (roadmap Phase 14).

A report needs ``view_reports`` *and* the permission for the area it reads, so a report is never a way round what the
person could not open in the module itself. Exporting is the same permission but is recorded in the audit log: a
downloaded file leaves the portal's control.
"""
from datetime import datetime, time, timedelta

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone
from rest_framework import status

from apps.accounts.roles import perm
from apps.audit import services as audit
from apps.billing import calculations as calc
from apps.billing.models import OPEN_STATUSES, Invoice, Transaction, TransactionStatus, TransactionType
from apps.clients.models import Client
from apps.core.exceptions import ServiceError
from apps.domains.models import Domain, DomainStatus
from apps.hosting.models import HostingAccount, HostingStatus
from apps.orders.models import Order, OrderStatus
from apps.support.models import Ticket, TicketStatus

from . import exports
from .builders import GROUP_TITLES, REGISTRY, all_reports
from .definitions import SCREEN_ROWS, parse_params


def _denied():
    return ServiceError("You do not have permission to view this report.", code="permission_denied",
                        status_code=status.HTTP_403_FORBIDDEN)


def can_run(user, definition):
    return bool(user and user.is_authenticated and user.has_perm(perm("view_reports"))
                and all(user.has_perm(perm(p)) for p in definition.permissions))


def available(user):
    """The reports this person may run, in the order they are listed."""
    return [d for d in all_reports() if can_run(user, d)]


def grouped(user):
    """[(group title, [definitions])] for the index page."""
    result = []
    for key, title in GROUP_TITLES.items():
        items = [d for d in available(user) if d.group == key]
        if items:
            result.append((title, items))
    return result


def get_definition(slug):
    definition = REGISTRY.get(slug)
    if definition is None:
        raise ServiceError("There is no such report.", code="report_not_found", status_code=status.HTTP_404_NOT_FOUND)
    return definition


def run(user, slug, data=None):
    """(definition, params, result). Raises ValidationError for a bad date or number, ServiceError for access."""
    definition = get_definition(slug)
    if not can_run(user, definition):
        raise _denied()
    params = parse_params(definition, data)
    return definition, params, definition.build(definition, params, user)


def export(user, slug, data, fmt, *, request=None):
    """(bytes, content type, filename) for a download, after recording who took which report."""
    if fmt not in exports.EXPORT_TYPES:
        raise ServiceError("Choose CSV, XLSX or PDF.", code="format_invalid")
    definition, params, result = run(user, slug, data)
    content, content_type, extension = exports.render(result, fmt)
    audit.record("report.exported", actor=user, metadata={
        "report": slug, "format": fmt, "rows": len(result.rows), "parameters": result.params}, request=request)
    return content, content_type, f"{slug}-{timezone.localdate():%Y%m%d}.{extension}"


def screen_rows(result):
    """What the page shows of a long report (the export has every row)."""
    return result.rows[:SCREEN_ROWS], len(result.rows) > SCREEN_ROWS


# --- The headline figures -------------------------------------------------------------------------------------------

def _net_income(start, end):
    ok = Transaction.objects.filter(status=TransactionStatus.SUCCEEDED, occurred_at__gte=start, occurred_at__lt=end,
                                    currency=settings.STORE_CURRENCY)
    totals = {r["type"]: r["t"] for r in ok.values("type").annotate(t=Sum("amount"))}
    return calc.money((totals.get(TransactionType.PAYMENT) or 0) - (totals.get(TransactionType.REFUND) or 0))


def headline(user):
    """[(label, kind, value, link slug)] of the figures on the reports index, only for what the person may see."""
    now = timezone.now()
    today = timezone.localdate()
    day_start = timezone.make_aware(datetime.combine(today, time.min))
    month_start = day_start.replace(day=1)
    tomorrow = day_start + timedelta(days=1)
    figures = []
    if user.has_perm(perm("view_billing")):
        overdue = Invoice.objects.filter(status__in=OPEN_STATUSES, due_date__lt=today)
        figures += [
            (f"Income today ({settings.STORE_CURRENCY})", "money", _net_income(day_start, tomorrow), "income"),
            ("Income this month", "money", _net_income(month_start, tomorrow), "income"),
            ("Invoices overdue", "int", overdue.count(), "unpaid-invoices")]
    if user.has_perm(perm("view_orders")):
        figures.append(("Orders today", "int", Order.objects.exclude(status=OrderStatus.DRAFT).filter(
            created_at__gte=day_start, created_at__lt=tomorrow).count(), "orders"))
    if user.has_perm(perm("view_clients")):
        figures.append(("New customers today", "int", Client.objects.filter(
            created_at__gte=day_start, created_at__lt=tomorrow).count(), "new-customers"))
    if user.has_perm(perm("view_hosting")) and user.has_perm(perm("view_domains")):
        horizon = now + timedelta(days=30)
        expiring = (HostingAccount.objects.filter(status__in=(HostingStatus.ACTIVE, HostingStatus.SUSPENDED),
                                                  expires_at__isnull=False, expires_at__lte=horizon).count()
                    + Domain.objects.filter(status=DomainStatus.ACTIVE, expires_at__isnull=False,
                                            expires_at__lte=horizon).count())
        figures.append(("Services expiring in 30 days", "int", expiring, "expiring-services"))
    if user.has_perm(perm("view_support")):
        figures.append(("Open tickets", "int", Ticket.objects.exclude(
            status__in=(TicketStatus.RESOLVED, TicketStatus.CLOSED)).count(), "open-tickets"))
    return [f for f in figures if can_run(user, REGISTRY[f[3]])]

