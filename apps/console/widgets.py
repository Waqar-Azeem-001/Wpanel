"""
The staff dashboard's widgets (roadmap Section 11.2): each one is a small query, a permission and a template.

A widget is shown only to staff who may open what it links to (Rule 5.6), and is loaded on its own so one slow widget
never blocks the page. Everything here reads existing records through their own services or models; nothing is written.
"""
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Callable

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.utils import timezone

from apps.accounts.roles import perm
from apps.audit.models import AuditEvent
from apps.billing.models import OPEN_STATUSES, Invoice, Transaction, TransactionStatus, TransactionType
from apps.billing.invoicing import filter_invoices_by_status
from apps.domains.models import Domain, DomainStatus, RegistrarProvider
from apps.hosting.models import HostingAccount, HostingStatus
from apps.lifecycle import services as lifecycle
from apps.lifecycle.models import Stage
from apps.notifications.models import EmailProvider
from apps.orders import lifecycle as order_lifecycle
from apps.orders.models import Order, OrderStatus
from apps.products.models import Server
from apps.support import services as support
from apps.support.models import Ticket, TicketStatus


@dataclass(frozen=True)
class Widget:
    key: str
    title: str
    icon: str
    permissions: tuple  # any one of these opens the widget
    build: Callable
    size: str = "half"  # "half" (one of two columns) or "full"

    @property
    def template(self):
        return f"console/widgets/{self.key}.html"


def _money(value):
    return (value or Decimal("0.00")).quantize(Decimal("0.01"))


def _income(since):
    """Money received from ``since`` (a date) to now: payments that succeeded, less refunds that succeeded."""
    rows = Transaction.objects.filter(status=TransactionStatus.SUCCEEDED, occurred_at__date__gte=since)
    paid = rows.filter(type=TransactionType.PAYMENT).aggregate(total=Sum("amount"))["total"]
    refunded = rows.filter(type=TransactionType.REFUND).aggregate(total=Sum("amount"))["total"]
    return _money((paid or 0) - (refunded or 0))


def billing_widget(user):
    today = timezone.localdate()
    open_invoices = Invoice.objects.filter(status__in=OPEN_STATUSES)
    owed = open_invoices.aggregate(total=Sum(ExpressionWrapper(F("total") - F("amount_paid"),
                                                              output_field=DecimalField(max_digits=14, decimal_places=2))))
    return {
        "today": _income(today), "month": _income(today.replace(day=1)), "year": _income(today.replace(month=1, day=1)),
        "owed": _money(owed["total"]), "open": open_invoices.count(),
        "overdue": filter_invoices_by_status(Invoice.objects.all(), "overdue").count(),
        "waiting": Transaction.objects.filter(status=TransactionStatus.PENDING, provider__isnull=True).count(),
        "currency": settings.STORE_CURRENCY,
    }


def orders_widget(user):
    groups = order_lifecycle.GROUPS
    return {"counts": {key: Order.objects.filter(status__in=statuses).count() for key, statuses in groups.items()},
            "recent": list(Order.objects.select_related("client").order_by("-created_at", "-id")[:5])}


def support_widget(user):
    overview = support.overview(user)
    counts = dict(overview["counts"])
    counts["customer_reply"] = Ticket.objects.filter(status=TicketStatus.CUSTOMER_REPLY).count()  # what its link lists
    return {"counts": counts, "waiting": overview["needs_attention"][:5]}


def failures_widget(user):
    return {"orders": list(Order.objects.filter(status=OrderStatus.FAILED).select_related("client")[:5]),
            "hosting": list(HostingAccount.objects.filter(status=HostingStatus.FAILED).select_related("client")[:5]),
            "order_count": Order.objects.filter(status=OrderStatus.FAILED).count(),
            "hosting_count": HostingAccount.objects.filter(status=HostingStatus.FAILED).count()}


def domains_widget(user):
    soon = timezone.now() + timedelta(days=30)
    rows = Domain.objects.filter(status=DomainStatus.ACTIVE, expires_at__isnull=False, expires_at__lte=soon
                                 ).select_related("client").order_by("expires_at")
    return {"domains": list(rows[:8]), "count": rows.count(), "now": timezone.now()}


def renewals_widget(user):
    overview = lifecycle.overview()
    counts = overview["counts"]
    return {"stages": [(label, counts[stage]) for stage, label in (
        (Stage.RENEWAL_DUE, "Renewal due"), (Stage.OVERDUE, "Overdue"), (Stage.GRACE, "In grace period"),
        (Stage.SUSPENDED, "Suspended"))], "pending_cancellations": overview["pending_cancellations"],
        "needing_attention": overview["needing_attention"]}


def _check(name, fn):
    try:
        ok, detail = fn()
    except Exception as exc:  # noqa: BLE001 - a broken dependency is the thing this widget exists to show
        return {"name": name, "ok": False, "detail": f"{type(exc).__name__}"}
    return {"name": name, "ok": ok, "detail": detail}


def _database():
    connection.ensure_connection()
    return True, connection.vendor


def _cache():
    cache.set("console.health", "ok", 10)
    return cache.get("console.health") == "ok", "read and write"


def health_widget(user):
    return {"checks": [
        _check("Database", _database),
        _check("Cache", _cache),
        _check("Email provider", lambda: ((p := EmailProvider.objects.filter(is_active=True).first()) is not None,
                                          p.name if p else "none is active: emails will not be delivered")),
        _check("Domain registrar", lambda: ((p := RegistrarProvider.objects.filter(is_active=True).first()) is not None,
                                            p.name if p else "none is active")),
        _check("Servers", lambda: (Server.objects.filter(status="active").exists(),
                                   f"{Server.objects.filter(status='active').count()} active")),
    ]}


def activity_widget(user):
    return {"events": list(AuditEvent.objects.select_related("actor")[:10])}


WIDGETS = [
    Widget("billing", "Billing", "bi-cash-coin", (perm("view_billing"),), billing_widget),
    Widget("orders", "Orders", "bi-bag", (perm("view_orders"),), orders_widget),
    Widget("support", "Support", "bi-life-preserver", (perm("view_support"),), support_widget),
    Widget("failures", "Provisioning failures", "bi-exclamation-octagon", (perm("view_orders"), perm("view_hosting")),
           failures_widget),
    Widget("domains", "Domains expiring in 30 days", "bi-globe", (perm("view_domains"),), domains_widget),
    Widget("renewals", "Services and renewals", "bi-arrow-repeat", (perm("view_hosting"), perm("view_domains")),
           renewals_widget),
    Widget("health", "System health", "bi-heart-pulse", (perm("view_settings"),), health_widget),
    Widget("activity", "Recent activity", "bi-clock-history", (perm("view_audit_log"),), activity_widget, size="full"),
]
BY_KEY = {w.key: w for w in WIDGETS}


def visible_widgets(user):
    """The widgets this person may see: at least one of a widget's permissions is enough."""
    return [w for w in WIDGETS if any(user.has_perm(p) for p in w.permissions)]


def context_for(widget, user):
    return {"widget": widget, **widget.build(user)}
