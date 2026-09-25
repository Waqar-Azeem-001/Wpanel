"""
The reports themselves. Each takes the parsed ``Params`` and returns a ``Result``; the registry at the bottom of each
function is what the pages, the API and the exports list.

Money comes from the permanent records (transactions), never from a stored total that could drift. Times are grouped
and bounded in the site's time zone. A report never holds more than ``MAX_ROWS`` rows and says so when it was cut.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Count, DateField, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth, TruncYear
from django.utils import timezone

from apps.billing import calculations as calc
from apps.billing.models import OPEN_STATUSES, Invoice, Transaction, TransactionStatus, TransactionType
from apps.clients.models import Client
from apps.domains.models import Domain, DomainStatus
from apps.hosting.models import HostingAccount, HostingStatus
from apps.lifecycle.models import CancellationRequest, CancellationStatus
from apps.orders.models import Order, OrderStatus
from apps.renewals.models import ChangeStatus, ServiceChange
from apps.support.models import Department, Ticket, TicketStatus

from .definitions import MAX_ROWS, Column, ReportDef, Result, describe

REGISTRY = {}
GROUP_TITLES = {"sales": "Sales", "financial": "Financial", "services": "Services", "support": "Support"}
ZERO = Decimal("0.00")


def report(slug, title, group, description, permissions, uses=()):
    def register(build):
        REGISTRY[slug] = ReportDef(slug=slug, title=title, group=group, description=description,
                                   permissions=tuple(permissions), uses=tuple(uses), build=build)
        return build

    return register


def _result(definition, params, columns, rows, *, summary=(), notes=()):
    truncated = len(rows) > MAX_ROWS
    notes = list(notes)
    if truncated:
        rows = rows[:MAX_ROWS]
        notes.append(f"Only the first {MAX_ROWS:,} rows are included. Narrow the dates to see the rest.")
    return Result(title=definition.title, columns=list(columns), rows=list(rows), summary=list(summary),
                  notes=notes, params=describe(definition, params), truncated=truncated)


def _rows(queryset, make):
    """At most MAX_ROWS + 1 rows (one over, so the caller can tell the report was cut)."""
    return [make(obj) for obj in queryset[:MAX_ROWS + 1]]


# --- Periods -------------------------------------------------------------------------------------------------------

def _trunc(group, field):
    if group == "day":
        return TruncDate(field)
    return (TruncMonth if group == "month" else TruncYear)(field, output_field=DateField())


def _period(group, value):
    """The start date of the period a date falls in."""
    if group == "month":
        return value.replace(day=1)
    if group == "year":
        return value.replace(month=1, day=1)
    return value


def _periods(group, start, end):
    """Every period from ``start`` to ``end`` (so a quiet day shows as zero, not as a gap)."""
    current, seen = _period(group, start), []
    while current <= end:
        seen.append(current)
        if group == "day":
            current += timedelta(days=1)
        elif group == "month":
            current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
        else:
            current = current.replace(year=current.year + 1)
    return seen


def _label(group, value):
    if group == "month":
        return value.strftime("%Y-%m")
    if group == "year":
        return str(value.year)
    return value.isoformat()


def _day(value):
    """A datetime's date in the site's time zone."""
    return timezone.localtime(value).date() if value else None


# --- Sales ---------------------------------------------------------------------------------------------------------

@report("sales-performance", "Sales performance", "sales",
        "Orders placed, their value and new customers, by day, month or year.", ("view_orders", "view_clients"),
        uses=("range", "group"))
def sales_performance(definition, params, user):
    orders = Order.objects.exclude(status=OrderStatus.DRAFT).filter(created_at__gte=params.start_at,
                                                                     created_at__lt=params.end_at)
    lost = (OrderStatus.CANCELLED, OrderStatus.FAILED, OrderStatus.FRAUD)
    placed = {r["p"]: r for r in orders.filter(currency=settings.STORE_CURRENCY).annotate(
        p=_trunc(params.group, "created_at")).values("p").annotate(
        n=Count("id"), value=Sum("total"), lost=Count("id", filter=Q(status__in=lost)))}
    other = orders.exclude(currency=settings.STORE_CURRENCY).count()
    customers = {r["p"]: r["n"] for r in Client.objects.filter(
        created_at__gte=params.start_at, created_at__lt=params.end_at).annotate(
        p=_trunc(params.group, "created_at")).values("p").annotate(n=Count("id"))}
    rows, totals = [], defaultdict(lambda: Decimal(0))
    for period in _periods(params.group, params.start, params.end):
        row = placed.get(period, {})
        n, value, lost_n, new = row.get("n", 0), calc.money(row.get("value") or 0), row.get("lost", 0), customers.get(period, 0)
        rows.append({"period": _label(params.group, period), "orders": n, "value": value, "lost": lost_n,
                     "customers": new})
        totals["orders"] += n
        totals["value"] += value
        totals["lost"] += lost_n
        totals["customers"] += new
    columns = [Column("period", "Period"), Column("orders", "Orders placed", "int"),
               Column("value", f"Order value ({settings.STORE_CURRENCY})", "money"),
               Column("lost", "Cancelled / failed", "int"), Column("customers", "New customers", "int")]
    summary = [("Orders placed", "int", int(totals["orders"])), ("Order value", "money", totals["value"]),
               ("New customers", "int", int(totals["customers"]))]
    notes = ["Order value is the total of orders placed (including tax), excluding drafts."]
    if other:
        notes.append(f"{other} order(s) in another currency are counted but not valued.")
    return _result(definition, params, columns, rows, summary=summary, notes=notes)


@report("orders", "Orders", "sales", "Every order placed in the period.", ("view_orders",), uses=("range",))
def orders_report(definition, params, user):
    queryset = Order.objects.exclude(status=OrderStatus.DRAFT).filter(
        created_at__gte=params.start_at, created_at__lt=params.end_at).select_related("client").order_by(
        "-created_at", "-id")
    rows = _rows(queryset, lambda o: {
        "reference": o.reference, "date": timezone.localtime(o.created_at), "client": o.client.display_name,
        "status": o.get_status_display(), "method": o.payment_method_name or "", "currency": o.currency,
        "total": o.total})
    totals = queryset.aggregate(n=Count("id"), value=Sum("total", filter=Q(currency=settings.STORE_CURRENCY)))
    by_status = queryset.values("status").annotate(n=Count("id")).order_by("-n")
    summary = [("Orders", "int", totals["n"]), ("Value", "money", calc.money(totals["value"] or 0))]
    summary += [(OrderStatus(r["status"]).label, "int", r["n"]) for r in by_status]
    columns = [Column("reference", "Order"), Column("date", "Placed", "datetime"), Column("client", "Client"),
               Column("status", "Status"), Column("method", "Payment method"), Column("currency", "Currency"),
               Column("total", "Total", "money")]
    return _result(definition, params, columns, rows, summary=summary)


@report("new-customers", "New customers", "sales", "Customers who signed up in the period, and where they came from.",
        ("view_clients",), uses=("range",))
def new_customers(definition, params, user):
    queryset = Client.objects.filter(created_at__gte=params.start_at, created_at__lt=params.end_at).select_related(
        "referral__affiliate").annotate(orders_n=Count("orders", filter=~Q(orders__status=OrderStatus.DRAFT))
                                        ).order_by("-created_at", "-id")

    def make(client):
        referral = getattr(client, "referral", None)
        return {"name": client.display_name, "email": client.email, "country": client.country,
                "signed_up": timezone.localtime(client.created_at), "orders": client.orders_n,
                "source": f"Affiliate {referral.affiliate.code}" if referral else "Direct"}

    rows = _rows(queryset, make)
    summary = [("New customers", "int", queryset.count()),
               ("Through an affiliate", "int", queryset.filter(referral__isnull=False).count()),
               ("Who have ordered", "int", queryset.filter(orders_n__gt=0).count())]
    columns = [Column("name", "Customer"), Column("email", "Email"), Column("country", "Country"),
               Column("signed_up", "Signed up", "datetime"), Column("orders", "Orders", "int"),
               Column("source", "Source")]
    return _result(definition, params, columns, rows, summary=summary)


# --- Financial -----------------------------------------------------------------------------------------------------

@report("income", "Income", "financial",
        "Money received and refunded, by day, month or year (daily, monthly and annual income).", ("view_billing",),
        uses=("range", "group"))
def income(definition, params, user):
    succeeded = Transaction.objects.filter(status=TransactionStatus.SUCCEEDED, occurred_at__gte=params.start_at,
                                           occurred_at__lt=params.end_at)
    grouped = {(r["p"], r["currency"]): r for r in succeeded.annotate(p=_trunc(params.group, "occurred_at")).values(
        "p", "currency").annotate(
        paid=Sum("amount", filter=Q(type=TransactionType.PAYMENT)),
        refunded=Sum("amount", filter=Q(type=TransactionType.REFUND)),
        n=Count("id", filter=Q(type=TransactionType.PAYMENT)))}
    currencies = sorted({c for _, c in grouped} | {settings.STORE_CURRENCY})
    rows, totals = [], defaultdict(lambda: defaultdict(lambda: Decimal(0)))
    for period in _periods(params.group, params.start, params.end):
        for currency in currencies:
            data = grouped.get((period, currency))
            if data is None and currency != settings.STORE_CURRENCY:
                continue
            data = data or {}
            paid, refunded = calc.money(data.get("paid") or 0), calc.money(data.get("refunded") or 0)
            rows.append({"period": _label(params.group, period), "currency": currency, "payments": data.get("n", 0),
                         "paid": paid, "refunded": refunded, "net": paid - refunded})
            totals[currency]["paid"] += paid
            totals[currency]["refunded"] += refunded
    summary = []
    for currency in currencies:
        tag = f" ({currency})" if len(currencies) > 1 else ""
        summary += [(f"Received{tag}", "money", totals[currency]["paid"]),
                    (f"Refunded{tag}", "money", totals[currency]["refunded"]),
                    (f"Net income{tag}", "money", totals[currency]["paid"] - totals[currency]["refunded"])]
    columns = [Column("period", "Period"), Column("currency", "Currency"), Column("payments", "Payments", "int"),
               Column("paid", "Received", "money"), Column("refunded", "Refunded", "money"),
               Column("net", "Net", "money")]
    return _result(definition, params, columns, rows, summary=summary,
                   notes=["Counted from successful payments and refunds on the day they happened, including tax."])


@report("unpaid-invoices", "Unpaid invoices", "financial", "Every issued invoice with a balance, and how overdue.",
        ("view_billing",))
def unpaid_invoices(definition, params, user):
    today = timezone.localdate()
    invoices = Invoice.objects.filter(status__in=OPEN_STATUSES).select_related("client").order_by("due_date", "id")
    buckets = {"Not yet due": ZERO, "1-30 days overdue": ZERO, "31-60 days overdue": ZERO, "61-90 days overdue": ZERO,
               "Over 90 days overdue": ZERO}
    outstanding = overdue = ZERO

    def days_late(invoice):
        return max(0, (today - invoice.due_date).days) if invoice.due_date else 0

    rows = []
    for invoice in invoices[:MAX_ROWS + 1]:
        balance = calc.money(invoice.balance_due)
        late = days_late(invoice)
        rows.append({"number": invoice.number or f"#{invoice.pk}", "client": invoice.client.display_name,
                     "issued": _day(invoice.created_at), "due": invoice.due_date, "currency": invoice.currency,
                     "total": invoice.total, "paid": invoice.amount_paid - invoice.amount_refunded,
                     "balance": balance, "late": late, "status": invoice.get_status_display()})
        outstanding += balance
        if late:
            overdue += balance
        key = ("Not yet due" if not late else "1-30 days overdue" if late <= 30 else "31-60 days overdue"
               if late <= 60 else "61-90 days overdue" if late <= 90 else "Over 90 days overdue")
        buckets[key] += balance
    summary = [("Invoices", "int", len(rows)), ("Outstanding", "money", outstanding), ("Overdue", "money", overdue)]
    summary += [(label, "money", value) for label, value in buckets.items()]
    columns = [Column("number", "Invoice"), Column("client", "Client"), Column("issued", "Issued", "date"),
               Column("due", "Due", "date"), Column("currency", "Currency"), Column("total", "Total", "money"),
               Column("paid", "Paid", "money"), Column("balance", "Balance", "money"),
               Column("late", "Days overdue", "int"), Column("status", "Status")]
    return _result(definition, params, columns, rows, summary=summary,
                   notes=[f"As at {today.isoformat()}. Amounts are in each invoice's own currency."])


@report("transactions", "Transactions", "financial", "Every payment and refund recorded in the period.",
        ("view_billing",), uses=("range",))
def transactions(definition, params, user):
    queryset = Transaction.objects.filter(occurred_at__gte=params.start_at, occurred_at__lt=params.end_at
                                          ).select_related("invoice", "client").order_by("-occurred_at", "-id")
    rows = _rows(queryset, lambda t: {
        "code": t.code, "date": timezone.localtime(t.occurred_at), "type": t.get_type_display(),
        "status": t.get_status_display(), "invoice": t.invoice.number or f"#{t.invoice_id}",
        "client": t.client.display_name, "method": t.method_name, "reference": t.reference, "currency": t.currency,
        "amount": t.amount})
    ok = queryset.filter(status=TransactionStatus.SUCCEEDED)
    summary = [("Transactions", "int", queryset.count()),
               ("Payments received", "money", calc.money(ok.filter(type=TransactionType.PAYMENT).aggregate(
                   t=Sum("amount"))["t"] or 0)),
               ("Refunded", "money", calc.money(ok.filter(type=TransactionType.REFUND).aggregate(
                   t=Sum("amount"))["t"] or 0)),
               ("Awaiting confirmation", "int", queryset.filter(status=TransactionStatus.PENDING).count()),
               ("Failed or rejected", "int", queryset.filter(status=TransactionStatus.FAILED).count())]
    columns = [Column("code", "Transaction"), Column("date", "Date", "datetime"), Column("type", "Type"),
               Column("status", "Status"), Column("invoice", "Invoice"), Column("client", "Client"),
               Column("method", "Method"), Column("reference", "Reference"), Column("currency", "Currency"),
               Column("amount", "Amount", "money")]
    return _result(definition, params, columns, rows, summary=summary)


@report("refunds-disputes", "Refunds and failed payments", "financial",
        "Money given back, and payments that failed or were rejected.", ("view_billing",), uses=("range",))
def refunds_disputes(definition, params, user):
    base = Transaction.objects.filter(occurred_at__gte=params.start_at, occurred_at__lt=params.end_at)
    refunds = base.filter(type=TransactionType.REFUND, status=TransactionStatus.SUCCEEDED)
    failed = base.filter(type=TransactionType.PAYMENT, status=TransactionStatus.FAILED)
    queryset = base.filter(Q(pk__in=refunds.values("pk")) | Q(pk__in=failed.values("pk"))).select_related(
        "invoice", "client").order_by("-occurred_at", "-id")
    rows = _rows(queryset, lambda t: {
        "date": timezone.localtime(t.occurred_at),
        "kind": "Refund" if t.type == TransactionType.REFUND else "Failed or rejected payment",
        "invoice": t.invoice.number or f"#{t.invoice_id}", "client": t.client.display_name, "method": t.method_name,
        "currency": t.currency, "amount": t.amount, "reason": t.note if t.type == TransactionType.REFUND
        else t.failure_reason})
    summary = [("Refunds", "int", refunds.count()),
               ("Refunded", "money", calc.money(refunds.aggregate(t=Sum("amount"))["t"] or 0)),
               ("Failed or rejected payments", "int", failed.count()),
               ("Their value", "money", calc.money(failed.aggregate(t=Sum("amount"))["t"] or 0))]
    columns = [Column("date", "Date", "datetime"), Column("kind", "Kind"), Column("invoice", "Invoice"),
               Column("client", "Client"), Column("method", "Method"), Column("currency", "Currency"),
               Column("amount", "Amount", "money"), Column("reason", "Reason")]
    return _result(definition, params, columns, rows, summary=summary,
                   notes=["The portal has no chargeback or dispute records; a rejected payment is the closest thing."])


# --- Services ------------------------------------------------------------------------------------------------------

def _hosting(params):
    return params.scope in ("all", "hosting")


def _domains(params):
    return params.scope in ("all", "domain")


def _days_left(when, now):
    return (when - now).days if when else None


@report("active-services", "Active services", "services", "Active hosting accounts and domains.",
        ("view_hosting", "view_domains"), uses=("scope",))
def active_services(definition, params, user):
    now = timezone.now()
    rows, hosting_n, domain_n = [], 0, 0
    if _hosting(params):
        accounts = HostingAccount.objects.filter(status=HostingStatus.ACTIVE).select_related("client", "product",
                                                                                            "server").order_by("domain")
        hosting_n = accounts.count()
        rows += _rows(accounts, lambda a: {
            "type": "Hosting", "name": a.domain, "client": a.client.display_name,
            "detail": f"{a.product.name}" + (f" on {a.server.name}" if a.server_id else ""),
            "expires": a.expires_at, "left": _days_left(a.expires_at, now)})
    if _domains(params):
        domains = Domain.objects.filter(status=DomainStatus.ACTIVE).select_related("client", "registrar").order_by("name")
        domain_n = domains.count()
        rows += _rows(domains, lambda d: {
            "type": "Domain", "name": d.name, "client": d.client.display_name,
            "detail": ("Auto-renews" if d.auto_renew else "Does not auto-renew"),
            "expires": d.expires_at, "left": _days_left(d.expires_at, now)})
    columns = [Column("type", "Type"), Column("name", "Service"), Column("client", "Client"),
               Column("detail", "Plan / renewal"), Column("expires", "Paid through", "datetime"),
               Column("left", "Days left", "int")]
    return _result(definition, params, columns, rows, summary=[("Active hosting", "int", hosting_n),
                                                                ("Active domains", "int", domain_n)])


@report("expiring-services", "Expiring services", "services",
        "Hosting and domains whose paid period ends soon (or already has).", ("view_hosting", "view_domains"),
        uses=("days", "scope"))
def expiring_services(definition, params, user):
    now = timezone.now()
    horizon = now + timedelta(days=params.days)
    pending = ServiceChange.objects.filter(status=ChangeStatus.PENDING)
    hosting_pending = set(pending.filter(hosting_account__isnull=False).values_list("hosting_account_id", flat=True))
    domain_pending = set(pending.filter(domain__isnull=False).values_list("domain_id", flat=True))
    rows = []
    if _hosting(params):
        accounts = HostingAccount.objects.filter(
            status__in=(HostingStatus.ACTIVE, HostingStatus.SUSPENDED), expires_at__isnull=False,
            expires_at__lte=horizon).select_related("client", "product").order_by("expires_at")
        rows += _rows(accounts, lambda a: {
            "type": "Hosting", "name": a.domain, "client": a.client.display_name, "detail": a.product.name,
            "status": a.get_status_display(), "expires": a.expires_at, "left": _days_left(a.expires_at, now),
            "invoiced": "Yes" if a.pk in hosting_pending else "No"})
    if _domains(params):
        domains = Domain.objects.filter(status=DomainStatus.ACTIVE, expires_at__isnull=False,
                                        expires_at__lte=horizon).select_related("client").order_by("expires_at")
        rows += _rows(domains, lambda d: {
            "type": "Domain", "name": d.name, "client": d.client.display_name,
            "detail": "Auto-renews" if d.auto_renew else "Does not auto-renew", "status": d.get_status_display(),
            "expires": d.expires_at, "left": _days_left(d.expires_at, now),
            "invoiced": "Yes" if d.pk in domain_pending else "No"})
    rows.sort(key=lambda r: r["expires"])
    lapsed = sum(1 for r in rows if r["left"] is not None and r["left"] < 0)
    columns = [Column("type", "Type"), Column("name", "Service"), Column("client", "Client"),
               Column("detail", "Plan / renewal"), Column("status", "Status"),
               Column("expires", "Paid through", "datetime"), Column("left", "Days left", "int"),
               Column("invoiced", "Renewal invoiced")]
    summary = [("Expiring within the period", "int", len(rows) - lapsed), ("Already past their date", "int", lapsed),
               ("Without a renewal invoice", "int", sum(1 for r in rows if r["invoiced"] == "No"))]
    return _result(definition, params, columns, rows, summary=summary,
                   notes=["A negative number of days means the paid period has already ended."])


@report("suspended-services", "Suspended services", "services", "Hosting accounts that are suspended, and why.",
        ("view_hosting",))
def suspended_services(definition, params, user):
    queryset = HostingAccount.objects.filter(status=HostingStatus.SUSPENDED).select_related("client", "product"
                                                                                           ).order_by("expires_at")
    rows = _rows(queryset, lambda a: {
        "name": a.domain, "client": a.client.display_name, "plan": a.product.name,
        "cause": "Non-payment (automatic)" if a.suspended_for_nonpayment else "Suspended by staff",
        "reason": a.suspend_reason, "expires": a.expires_at, "since": timezone.localtime(a.updated_at)})
    summary = [("Suspended", "int", queryset.count()),
               ("For non-payment", "int", queryset.filter(suspended_for_nonpayment=True).count())]
    columns = [Column("name", "Hosting"), Column("client", "Client"), Column("plan", "Plan"),
               Column("cause", "Cause"), Column("reason", "Reason"), Column("expires", "Paid through", "datetime"),
               Column("since", "Last changed", "datetime")]
    return _result(definition, params, columns, rows, summary=summary)


@report("cancelled-services", "Cancelled services", "services",
        "Cancellation requests carried out in the period, with reasons and refunds.",
        ("view_hosting", "view_domains"), uses=("range",))
def cancelled_services(definition, params, user):
    queryset = CancellationRequest.objects.filter(
        status=CancellationStatus.COMPLETED, completed_at__gte=params.start_at, completed_at__lt=params.end_at
    ).select_related("client", "hosting_account", "domain", "refund_transaction").order_by("-completed_at", "-id")
    rows = _rows(queryset, lambda c: {
        "code": c.code, "service": c.service_name, "type": "Hosting" if c.kind == "hosting" else "Domain",
        "client": c.client.display_name, "reason": c.get_reason_code_display(), "timing": c.get_timing_display(),
        "asked": timezone.localtime(c.created_at), "done": timezone.localtime(c.completed_at),
        "refund": c.refund_transaction.amount if c.refund_transaction_id else ZERO})
    counts = defaultdict(int)
    for row in rows:
        counts[row["reason"]] += 1
    summary = [("Cancellations", "int", queryset.count()),
               ("Refunded", "money", sum((r["refund"] for r in rows), ZERO))]
    summary += [(f"Reason: {reason}", "int", n) for reason, n in sorted(counts.items())]
    columns = [Column("code", "Request"), Column("service", "Service"), Column("type", "Type"),
               Column("client", "Client"), Column("reason", "Reason"), Column("timing", "Ended"),
               Column("asked", "Asked", "datetime"), Column("done", "Done", "datetime"),
               Column("refund", "Refund", "money")]
    return _result(definition, params, columns, rows, summary=summary,
                   notes=["Only cancellations requested through the portal; an account ended by hand does not appear."])


# --- Support -------------------------------------------------------------------------------------------------------

def _hours(delta):
    return round(delta.total_seconds() / 3600, 1)


@report("open-tickets", "Open tickets", "support", "Every ticket that is not resolved or closed.", ("view_support",))
def open_tickets(definition, params, user):
    now = timezone.now()
    queryset = Ticket.objects.exclude(status__in=(TicketStatus.RESOLVED, TicketStatus.CLOSED)).select_related(
        "client", "department", "assigned_to").order_by("created_at")
    rows = _rows(queryset, lambda t: {
        "reference": t.reference, "subject": t.subject, "client": t.client.display_name,
        "department": t.department.name, "priority": t.get_priority_display(), "status": t.get_status_display(),
        "assignee": t.assigned_to.email if t.assigned_to_id else "Unassigned",
        "opened": timezone.localtime(t.created_at), "age": (now - t.created_at).days,
        "activity": timezone.localtime(t.last_activity_at)})
    waiting = queryset.filter(status__in=(TicketStatus.OPEN, TicketStatus.CUSTOMER_REPLY)).count()
    summary = [("Open tickets", "int", queryset.count()), ("Waiting for a reply from us", "int", waiting),
               ("Unassigned", "int", queryset.filter(assigned_to__isnull=True).count()),
               ("Urgent", "int", queryset.filter(priority="urgent").count())]
    columns = [Column("reference", "Ticket"), Column("subject", "Subject"), Column("client", "Client"),
               Column("department", "Department"), Column("priority", "Priority"), Column("status", "Status"),
               Column("assignee", "Assigned to"), Column("opened", "Opened", "datetime"),
               Column("age", "Age (days)", "int"), Column("activity", "Last activity", "datetime")]
    return _result(definition, params, columns, rows, summary=summary)


@report("resolved-tickets", "Resolved tickets", "support", "Tickets resolved in the period and how long they took.",
        ("view_support",), uses=("range",))
def resolved_tickets(definition, params, user):
    queryset = Ticket.objects.filter(resolved_at__gte=params.start_at, resolved_at__lt=params.end_at).select_related(
        "client", "department").order_by("-resolved_at", "-id")
    rows = _rows(queryset, lambda t: {
        "reference": t.reference, "subject": t.subject, "client": t.client.display_name,
        "department": t.department.name, "opened": timezone.localtime(t.created_at),
        "resolved": timezone.localtime(t.resolved_at), "hours": _hours(t.resolved_at - t.created_at)})
    average = round(sum(r["hours"] for r in rows) / len(rows), 1) if rows else 0
    summary = [("Resolved", "int", queryset.count()), ("Average time to resolve", "hours", average)]
    columns = [Column("reference", "Ticket"), Column("subject", "Subject"), Column("client", "Client"),
               Column("department", "Department"), Column("opened", "Opened", "datetime"),
               Column("resolved", "Resolved", "datetime"), Column("hours", "Hours to resolve", "hours")]
    return _result(definition, params, columns, rows, summary=summary)


@report("department-stats", "Department statistics", "support", "Tickets opened, resolved and still open, per department.",
        ("view_support",), uses=("range",))
def department_stats(definition, params, user):
    tickets = Ticket.objects
    rows = []
    for department in Department.objects.order_by("name"):
        opened = tickets.filter(department=department, created_at__gte=params.start_at, created_at__lt=params.end_at)
        resolved = list(tickets.filter(department=department, resolved_at__gte=params.start_at,
                                       resolved_at__lt=params.end_at).values_list("created_at", "resolved_at"))
        still_open = tickets.filter(department=department).exclude(status__in=(TicketStatus.RESOLVED,
                                                                                TicketStatus.CLOSED)).count()
        average = round(sum(_hours(r - c) for c, r in resolved) / len(resolved), 1) if resolved else None
        rows.append({"department": department.name, "opened": opened.count(), "resolved": len(resolved),
                     "open": still_open, "hours": average})
    summary = [("Opened", "int", sum(r["opened"] for r in rows)), ("Resolved", "int", sum(r["resolved"] for r in rows)),
               ("Open now", "int", sum(r["open"] for r in rows))]
    columns = [Column("department", "Department"), Column("opened", "Opened", "int"),
               Column("resolved", "Resolved", "int"), Column("open", "Open now", "int"),
               Column("hours", "Average hours to resolve", "hours")]
    return _result(definition, params, columns, rows, summary=summary)


def all_reports():
    return list(REGISTRY.values())

