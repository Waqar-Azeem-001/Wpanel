"""
The staff dashboard's widgets (roadmap Section 11.2): each one is a small query, a permission and a template.

A widget is shown only to staff who may open what it links to (Rule 5.6), and is loaded on its own so one slow widget
never blocks the page. Everything here reads existing records through their own services or models; nothing is written.
"""
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Callable
from urllib.parse import urlsplit

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
from apps.branding.services import store_currency


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
        "currency": store_currency(),
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


def _celery():
    from apps.core.tasks import ping

    pong = ping.apply_async().get(timeout=3)
    return pong == "pong", "a worker replied"


def _public_address():
    ok = urlsplit(settings.SITE_URL).scheme == "https"
    return ok, settings.SITE_URL if ok else f"{settings.SITE_URL}: emailed links need the public secure address"


def _error_tracking():
    try:
        import sentry_sdk

        active = sentry_sdk.get_client().is_active()
    except ImportError:
        active = False
    return active, "reporting errors" if active else "off: set SENTRY_DSN so crashes are reported"


def _eager():
    """Development runs every job inline with no worker or scheduler, so "is it running" has no meaning there."""
    return bool(getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False))


def _minutes_since(moment):
    return None if moment is None else int((timezone.now() - moment).total_seconds() // 60)


def _scheduled_jobs():
    """Green only when the scheduler is really firing: the job that runs every 5 minutes finished within the last 12."""
    from apps.core import job_marks

    if _eager():
        return True, "run inline in development"
    ago = _minutes_since(job_marks.last_finished("apps.orders.tasks.sweep_stuck_orders_task"))
    if ago is None or ago > 12:
        return False, "no scheduled job has finished in the last 12 minutes: the scheduler (beat) or the worker is not running"
    return True, f"{len(settings.CELERY_BEAT_SCHEDULE)} scheduled, the last ran {ago} min ago"


def _backups():
    """Green only when a real backup file exists and is less than 36 hours old."""
    if _eager():
        return True, "not run in development"
    from . import storage

    files = storage.panel_backups()
    if not files:
        return False, "no backup exists yet"
    ago = _minutes_since(max(f["modified"] for f in files))
    if ago > 36 * 60:
        return False, f"the newest backup is {ago // 60} hours old: the nightly backup is not running"
    return True, f"newest {ago // 60} h ago, {len(files)} kept" + ("" if _offsite() else "; none copied off this server")


def _offsite():
    from apps.core.management.commands import backup_panel

    return backup_panel.offsite_configured()


def _registrar():
    """A manual registrar only records domains here; it never registers anything, so it must not look healthy. A real one is asked, not assumed."""
    provider = RegistrarProvider.objects.filter(is_active=True).first()
    if provider is None:
        return False, "none is active"
    if provider.kind == "manual":
        return False, f"{provider.name} is manual: domains are recorded here but NOT registered at a registrar"
    cached = cache.get("console.registrar.probe")
    if cached is None:
        from apps.domains.adapters.base import RegistrarError
        from apps.domains.adapters.registry import build_adapter

        try:
            build_adapter(provider).check_availability("whpanel-health-probe-zz91.com")
            cached = (True, f"{provider.name} answered")
        except RegistrarError as exc:
            cached = (False, f"{provider.name} did not answer: {exc}"[:200])
        cache.set("console.registrar.probe", cached, 600)
    return cached


def _email():
    """An active provider is only green while mail is really leaving: not when everything sent in the last day failed."""
    provider = EmailProvider.objects.filter(is_active=True).first()
    if provider is None:
        return False, "none is active: emails will not be delivered"
    from apps.notifications.models import EmailMessage

    since = timezone.now() - timedelta(days=1)
    recent = EmailMessage.objects.filter(created_at__gte=since)
    sent, failed = recent.filter(status="sent").count(), recent.filter(status="failed").count()
    if failed and not sent:
        return False, f"{provider.name}: {failed} email(s) failed in the last day and none went out"
    return True, provider.name + (f" ({failed} failed in the last day)" if failed else "")


def _usage_sync():
    from apps.core import job_marks

    scheduled = any("usage" in str(entry.get("task", "")) or "resource_readings" in str(entry.get("task", ""))
                    for entry in settings.CELERY_BEAT_SCHEDULE.values())
    if not scheduled:
        return False, "not scheduled: usage is refreshed by hand (launch item)"
    if _eager():
        return True, "scheduled"
    ago = _minutes_since(job_marks.last_finished("apps.hosting.tasks.record_resource_readings_task"))
    if ago is None or ago > 30:
        return False, "scheduled but has not finished in the last 30 minutes"
    return True, f"last ran {ago} min ago"


def health_widget(user):
    return {"checks": [
        _check("Database", _database),
        _check("Cache", _cache),
        _check("Background jobs", _celery),
        _check("Email provider", _email),
        _check("Domain registrar", _registrar),
        _check("Servers", lambda: (Server.objects.filter(status="active").exists(),
                                   f"{Server.objects.filter(status='active').count()} active")),
        _check("Public address", _public_address),
        _check("Error tracking", _error_tracking),
        _check("Scheduled jobs", _scheduled_jobs),
        _check("Backups", _backups),
        _check("Hosting usage sync", _usage_sync),
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
    tiles += _more_tiles(user, today)
    return {"tiles": tiles}


def _more_tiles(user, today):
    """The second rank of numbers: domains, the year's income, refunds, the average order, and what was resolved."""
    from django.db.models import Avg

    tiles = []
    month_start = today.replace(day=1)
    if _can(user, "view_domains"):
        active = Domain.objects.filter(status=DomainStatus.ACTIVE)
        soon = active.filter(expires_at__isnull=False, expires_at__lte=timezone.now() + timedelta(days=30)).count()
        tiles.append({"label": "Active domains", "value": active.count(), "icon": "bi-globe2", "tone": "info",
                      "sub": f"{soon} expiring in 30 days", "bad": bool(soon), "url": reverse("domains_staff:list") + "?status=active"})
    if _can(user, "view_billing"):
        billing = billing_widget(user)
        refunds = Transaction.objects.filter(status=TransactionStatus.SUCCEEDED, type=TransactionType.REFUND,
                                             occurred_at__date__gte=month_start, currency=store_currency())
        refunded = _money(refunds.aggregate(t=Sum("amount"))["t"])
        tiles.append({"kpi": True, "label": "Income this year", "value": billing["year"], "money": True, "currency": billing["currency"],
                      "icon": "bi-graph-up-arrow", "tone": "ok", "sub": f"{billing['month']} this month",
                      "url": reverse("billing_staff:transaction_list")})
        tiles.append({"kpi": True, "label": "Refunds this month", "value": refunded, "money": True, "currency": billing["currency"],
                      "icon": "bi-arrow-counterclockwise", "tone": "warn", "sub": f"{refunds.count()} refund(s)",
                      "url": reverse("billing_staff:transaction_list") + "?type=refund"})
        tiles.append({"kpi": True, "label": "Payments to confirm", "value": billing["waiting"], "icon": "bi-hourglass-split", "tone": "warn",
                      "bad": bool(billing["waiting"]), "sub": "reported by customers",
                      "url": reverse("billing_staff:transaction_list") + "?status=pending"})
    if _can(user, "view_orders"):
        month_orders = Order.objects.filter(created_at__date__gte=month_start).exclude(
            status__in=(OrderStatus.DRAFT, OrderStatus.CANCELLED))
        average = month_orders.filter(currency=store_currency()).aggregate(a=Avg("total"))["a"]
        tiles.append({"kpi": True, "label": "Average order", "value": _money(average), "money": True, "currency": store_currency(),
                      "icon": "bi-basket", "tone": "info", "sub": f"{month_orders.count()} order(s) this month",
                      "url": reverse("orders_staff:list")})
    if _can(user, "view_clients"):
        from apps.clients.models import Client

        today_new = Client.objects.filter(created_at__date=today).count()
        tiles.append({"kpi": True, "label": "New customers today", "value": today_new, "icon": "bi-person-plus", "tone": "ok",
                      "sub": f"{Client.objects.filter(created_at__date__gte=month_start).count()} this month",
                      "url": reverse("clients_staff:list")})
    if _can(user, "view_support"):
        resolved = Ticket.objects.filter(resolved_at__date__gte=today - timedelta(days=7)).count()
        tiles.append({"kpi": True, "label": "Tickets resolved", "value": resolved, "icon": "bi-check2-circle", "tone": "ok",
                      "sub": "in the last 7 days", "url": reverse("support_staff:tickets") + "?status=resolved"})
    return tiles


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
    if _can(user, "view_affiliates"):
        from apps.affiliates.models import Affiliate, AffiliateStatus

        waiting = Affiliate.objects.filter(status=AffiliateStatus.PENDING).count()
        if waiting:
            add("warning", "bi-share", "Affiliate applications", f"{waiting} application(s) are waiting for approval.",
                reverse("affiliates_staff:affiliates") + "?status=pending", go="Review")
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
                          "Servers": "catalog_staff:server_list", "Backups": "console:storage"}.get(check["name"])
                add("critical" if check["name"] in ("Database", "Email provider", "Domain registrar", "Background jobs", "Scheduled jobs", "Backups") else "warning",
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
    """
    The widgets this person may see (at least one of a widget's permissions is enough), in their own saved order.
    The KPI strip always leads, unmoved - only the panels below it are reorderable. A saved key this person can no
    longer see (or that no longer exists) is simply skipped, and any panel not in their saved order yet (new, or
    never touched) keeps the registry's own order at the end - so nothing here needs a migration or a data fix-up
    when a widget is added, removed or renamed.
    """
    visible = [w for w in WIDGETS if any(user.has_perm(p) for p in w.permissions)]
    strip = [w for w in visible if w.size == "strip"]
    panels = [w for w in visible if w.size != "strip"]
    by_key = {w.key: w for w in panels}
    order = getattr(user, "dashboard_widget_order", None) or []
    ordered = [by_key[k] for k in order if k in by_key]
    ordered += [w for w in panels if w.key not in order]
    return strip + ordered


def move_widget(user, key, direction):
    """
    Move one panel up or down in this person's own saved dashboard order (never the KPI strip, which is not in this
    list at all). Does nothing for an unknown key, an unknown direction, or a widget already at that edge - calling
    it again from a stale page is harmless, not an error.
    """
    panels = [w.key for w in visible_widgets(user) if w.size != "strip"]
    if key not in panels or direction not in ("up", "down"):
        return
    order = panels
    i = order.index(key)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(order):
        order[i], order[j] = order[j], order[i]
        user.dashboard_widget_order = order
        user.save(update_fields=["dashboard_widget_order"])


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
    if widget.size != "strip":
        panel_keys = [w.key for w in visible_widgets(user) if w.size != "strip"]
        context.update(can_move=len(panel_keys) > 1,
                       is_first_panel=panel_keys[:1] == [widget.key],
                       is_last_panel=panel_keys[-1:] == [widget.key])
    return context
