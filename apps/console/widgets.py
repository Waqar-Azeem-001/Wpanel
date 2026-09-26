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
from django.urls import reverse
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
    size: str = "half"  # "strip" (the KPI strip), "full" (a whole row) or "half" (shares a row; `span` of 12 columns)
    span: int = 6

    @property
    def template(self):
        return f"console/widgets/{self.key}.html"


def _can(user, *codes):
    return any(user.has_perm(perm(code)) for code in codes)


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


def summary_widget(user):
    """The KPI strip: the few figures that matter, each only for people who may open the list behind it, each equal to it."""
    from apps.clients.models import Client, ClientStatus

    today = timezone.localdate()
    tiles = []
    if _can(user, "view_clients"):
        clients = Client.objects.filter(status=ClientStatus.ACTIVE)
        new = clients.filter(created_at__date__gte=today.replace(day=1)).count()
        tiles.append({"label": "Active clients", "value": clients.count(), "icon": "bi-people", "tone": "",
                      "sub": f"{new} new this month", "url": reverse("clients_staff:list") + "?status=active"})
    if _can(user, "view_hosting"):
        active = HostingAccount.objects.filter(status=HostingStatus.ACTIVE)
        suspended = HostingAccount.objects.filter(status=HostingStatus.SUSPENDED).count()
        tiles.append({"label": "Active services", "value": active.count(), "icon": "bi-hdd-rack", "tone": "ok",
                      "sub": f"{suspended} suspended", "url": reverse("hosting_staff:list") + "?status=active"})
    if _can(user, "view_orders"):
        pending = Order.objects.filter(status__in=order_lifecycle.GROUPS["pending"]).count()
        tiles.append({"label": "Pending orders", "value": pending, "icon": "bi-bag", "tone": "info",
                      "sub": f"{Order.objects.filter(created_at__date=today).count()} placed today",
                      "url": reverse("orders_staff:list") + "?group=pending"})
    if _can(user, "view_billing"):
        billing = billing_widget(user)
        tiles.append({"label": "Unpaid invoices", "value": billing["owed"], "money": True, "currency": billing["currency"],
                      "icon": "bi-receipt", "tone": "warn", "bad": bool(billing["overdue"]),
                      "sub": f"{billing['open']} open · {billing['overdue']} overdue",
                      "url": reverse("billing_staff:invoice_list") + "?status=unpaid"})
        tiles.append({"label": "Income this month", "value": billing["month"], "money": True, "currency": billing["currency"],
                      "icon": "bi-cash-coin", "tone": "ok", "sub": f"{billing['today']} today",
                      "url": reverse("billing_staff:transaction_list")})
    if _can(user, "view_support"):
        counts = support.overview(user)["counts"]
        tiles.append({"label": "Open tickets", "value": counts["active"], "icon": "bi-life-preserver", "tone": "info",
                      "sub": f"{counts['unassigned']} unassigned", "bad": bool(counts["urgent"]),
                      "url": reverse("support_staff:tickets") + "?status=active"})
    if _can(user, "view_orders"):
        failed = Order.objects.filter(status=OrderStatus.FAILED).count()
        hosting_failed = HostingAccount.objects.filter(status=HostingStatus.FAILED).count() if _can(user, "view_hosting") else 0
        tiles.append({"label": "Failed orders", "value": failed, "icon": "bi-exclamation-octagon",
                      "tone": "danger" if failed else "ok", "bad": bool(failed),
                      "sub": f"{hosting_failed} hosting accounts failed" if hosting_failed else "Nothing failed to set up",
                      "url": reverse("orders_staff:list") + "?status=failed"})
    return {"tiles": tiles}


def attention_widget(user):
    """What needs a person now, worst first. Every row appears only when its count is above zero and links to the list."""
    rows = []

    def add(severity, icon, title, text, url, go="View"):
        rows.append({"severity": severity, "icon": icon, "title": title, "text": text, "url": url, "go": go})

    if _can(user, "view_orders") or _can(user, "view_hosting"):
        orders = Order.objects.filter(status=OrderStatus.FAILED).count() if _can(user, "view_orders") else 0
        hosting = HostingAccount.objects.filter(status=HostingStatus.FAILED).count() if _can(user, "view_hosting") else 0
        if orders or hosting:
            add("critical", "bi-exclamation-octagon", "Provisioning failed",
                f"{orders} order(s) and {hosting} hosting account(s) could not be set up and need a retry or a decision.",
                reverse("orders_staff:list") + "?status=failed" if orders else reverse("hosting_staff:list") + "?status=failed")
    if _can(user, "view_billing"):
        overdue = filter_invoices_by_status(Invoice.objects.all(), "overdue").count()
        if overdue:
            add("critical", "bi-receipt", "Overdue invoices", f"{overdue} invoice(s) are past their due date.",
                reverse("billing_staff:invoice_list") + "?status=overdue")
        waiting = Transaction.objects.filter(status=TransactionStatus.PENDING, provider__isnull=True).count()
        if waiting:
            add("warning", "bi-cash-coin", "Payments to confirm",
                f"{waiting} reported payment(s) are waiting for someone to confirm the money arrived.",
                reverse("billing_staff:transaction_list"))
    if _can(user, "view_support"):
        counts = support.overview(user)["counts"]
        if counts["urgent"]:
            add("critical", "bi-life-preserver", "Urgent tickets", f"{counts['urgent']} open ticket(s) are marked urgent.",
                reverse("support_staff:tickets") + "?status=active&priority=urgent")
        if counts["unassigned"]:
            add("warning", "bi-inbox", "Unassigned tickets", f"{counts['unassigned']} open ticket(s) have nobody looking at them.",
                reverse("support_staff:tickets") + "?status=active&assigned=none")
    if _can(user, "view_hosting", "view_domains"):
        overview = lifecycle.overview()
        if overview["pending_cancellations"]:
            add("warning", "bi-x-circle", "Cancellation requests",
                f"{overview['pending_cancellations']} request(s) are waiting for a decision.",
                reverse("lifecycle_staff:cancellations"))
    if _can(user, "view_settings"):
        for check in health_widget(user)["checks"]:
            if not check["ok"]:
                target = {"Email provider": "console:email_providers", "Domain registrar": "console:registrars",
                          "Servers": "catalog_staff:server_list"}.get(check["name"])
                add("critical" if check["name"] in ("Database", "Email provider", "Domain registrar") else "warning",
                    "bi-heart-pulse", f"{check['name']} needs attention", check["detail"].capitalize() + ".",
                    reverse(target) if target else reverse("console:dashboard"), "Fix")
    order = {"critical": 0, "warning": 1}
    rows.sort(key=lambda r: order[r["severity"]])
    return {"rows": rows, "critical": sum(1 for r in rows if r["severity"] == "critical")}


WIDGETS = [
    Widget("summary", "Summary", "bi-speedometer2",
           tuple(perm(c) for c in ("view_clients", "view_hosting", "view_orders", "view_billing", "view_support")),
           summary_widget, size="strip", span=12),
    Widget("attention", "Needs attention", "bi-bell", tuple(perm(c) for c in (
        "view_orders", "view_hosting", "view_billing", "view_support", "view_domains", "view_settings")),
           attention_widget, size="full", span=12),
    Widget("orders", "Orders", "bi-bag", (perm("view_orders"),), orders_widget, span=7),
    Widget("billing", "Money", "bi-cash-coin", (perm("view_billing"),), billing_widget, span=5),
    Widget("support", "Support queue", "bi-life-preserver", (perm("view_support"),), support_widget, span=7),
    Widget("failures", "Provisioning failures", "bi-exclamation-octagon", (perm("view_orders"), perm("view_hosting")),
           failures_widget, span=5),
    Widget("domains", "Domains expiring in 30 days", "bi-globe", (perm("view_domains"),), domains_widget, span=6),
    Widget("renewals", "Services and renewals", "bi-arrow-repeat", (perm("view_hosting"), perm("view_domains")),
           renewals_widget, span=6),
    Widget("health", "System health", "bi-heart-pulse", (perm("view_settings"),), health_widget, span=5),
    Widget("activity", "Recent activity", "bi-clock-history", (perm("view_audit_log"),), activity_widget, span=7),
]
BY_KEY = {w.key: w for w in WIDGETS}


def visible_widgets(user):
    """The widgets this person may see: at least one of a widget's permissions is enough."""
    return [w for w in WIDGETS if any(user.has_perm(p) for p in w.permissions)]


# The link in a panel's header: where the whole list lives.
HEADS = {"orders": ("View all", "orders_staff:list"), "billing": ("Billing", "billing_staff:index"),
         "support": ("Open the queue", "support_staff:tickets"), "domains": ("All domains", "domains_staff:list"),
         "renewals": ("Lifecycle", "lifecycle_staff:overview"), "activity": ("Activity log", "console:audit_log")}


def context_for(widget, user):
    context = {"widget": widget, **widget.build(user)}
    if widget.key in HEADS:
        label, name = HEADS[widget.key]
        context.update(head_label=label, head_link=reverse(name))
    if widget.key == "failures":
        total = context["order_count"] + context["hosting_count"]
        context.update(head_chip=total or None, head_chip_danger=True)
    if widget.key == "attention":
        context.update(head_chip=len(context["rows"]) or None, head_chip_danger=bool(context["critical"]))
    return context
