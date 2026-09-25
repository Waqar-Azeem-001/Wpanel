"""
The staff client profile as tabs (roadmap Section 11.3): every tab is its own URL and appears only for staff who may open
what it lists (Rule 5.6). Other apps are imported lazily: they depend on ``clients``, not the other way round.
"""
from dataclasses import dataclass

from django.urls import reverse

from apps.accounts.roles import perm

PAGE_SIZE = 25


@dataclass(frozen=True)
class Tab:
    key: str
    label: str
    permissions: tuple  # any one opens it; () = anyone who may open the profile
    url_name: str
    icon: str = ""

    def url(self, client):
        if self.url_name == "clients_staff:tab":
            return reverse(self.url_name, args=[client.pk, self.key])
        return reverse(self.url_name, args=[client.pk])


TABS = [
    Tab("summary", "Summary", (), "clients_staff:detail", "bi-person-vcard"),
    Tab("profile", "Profile", ("manage_clients",), "clients_staff:edit", "bi-pencil-square"),
    Tab("contacts", "Contacts", (), "clients_staff:contact_add", "bi-people"),
    Tab("services", "Products/Services", ("view_hosting",), "clients_staff:tab", "bi-hdd-network"),
    Tab("domains", "Domains", ("view_domains",), "clients_staff:tab", "bi-globe"),
    Tab("billable", "Billable Items", ("view_billing",), "clients_staff:tab", "bi-cart-plus"),
    Tab("invoices", "Invoices", ("view_billing",), "clients_staff:tab", "bi-receipt"),
    Tab("quotes", "Quotes", ("view_billing",), "clients_staff:tab", "bi-file-earmark-text"),
    Tab("transactions", "Transactions", ("view_billing",), "clients_staff:tab", "bi-cash-coin"),
    Tab("tickets", "Tickets", ("view_support",), "clients_staff:tab", "bi-life-preserver"),
    Tab("emails", "Emails", ("view_settings",), "clients_staff:tab", "bi-envelope"),
    Tab("cancellations", "Cancellations", ("view_hosting", "view_domains"), "clients_staff:tab", "bi-x-circle"),
    Tab("affiliate", "Affiliate", ("view_affiliates",), "clients_staff:tab", "bi-share"),
    Tab("notes", "Notes", (), "clients_staff:tab", "bi-sticky"),
    Tab("log", "Log", ("view_audit_log",), "clients_staff:tab", "bi-clock-history"),
]
BY_KEY = {t.key: t for t in TABS}


def allowed(user, tab):
    return not tab.permissions or any(user.has_perm(perm(p)) for p in tab.permissions)


def visible_tabs(user):
    return [t for t in TABS if allowed(user, t)]


# --- Cells: what a table shows -------------------------------------------------------------------------------------------

def text(value, url="", right=False):
    return {"text": value, "url": url, "right": right}


def status(key, label):
    return {"status": key, "label": label}


def _sources(client, user):
    """Each list tab: (queryset, columns, row builder). Built lazily, so a tab only queries its own records."""
    from apps.audit.models import AuditEvent
    from apps.notifications.models import EmailMessage

    def hosting():
        rows = client.hosting_accounts.select_related("product", "server").order_by("domain")
        return rows, ["Domain", "Product", "Status", "Paid through", "Server"], lambda h: [
            text(h.domain, reverse("hosting_staff:detail", args=[h.pk])), text(h.product.name), status(h.status, h.get_status_display()),
            text(h.expires_at, ""), text(h.server.name if h.server else "—")]

    def domains():
        rows = client.domains.order_by("name")
        return rows, ["Domain", "Status", "Registered", "Expires", "Auto-renew"], lambda d: [
            text(d.name, reverse("domains_staff:detail", args=[d.pk])), status(d.status, d.get_status_display()),
            text(d.registered_at), text(d.expires_at), text("Yes" if d.auto_renew else "No")]

    def billable():
        rows = client.billable_items.select_related("invoice").order_by("-created_at", "-id")
        return rows, ["Description", "Quantity", "Unit price", "Invoiced"], lambda b: [
            text(b.description), text(b.quantity, right=True), text(b.unit_price, right=True),
            text(b.invoice.reference, reverse("billing_staff:invoice_detail", args=[b.invoice_id])) if b.invoice_id else text("Not yet")]

    def invoices():
        rows = client.invoices.order_by("-created_at", "-id")
        return rows, ["Invoice", "Issued", "Due", "Status", "Total", "Balance"], lambda i: [
            text(i.reference, reverse("billing_staff:invoice_detail", args=[i.pk])), text(i.issue_date), text(i.due_date),
            status(i.display_status, i.display_status_label), text(i.total, right=True), text(i.balance_due, right=True)]

    def quotes():
        rows = client.quotes.order_by("-created_at", "-id")
        return rows, ["Quote", "Issued", "Valid until", "Status", "Total"], lambda q: [
            text(q.reference, reverse("billing_staff:quote_detail", args=[q.pk])), text(q.issue_date), text(q.valid_until),
            status(q.status, q.display_status_label), text(q.total, right=True)]

    def transactions():
        rows = client.transactions.select_related("invoice").order_by("-occurred_at", "-id")
        return rows, ["Date", "Type", "Invoice", "Method", "Status", "Amount"], lambda t: [
            text(t.occurred_at), text(t.get_type_display()),
            text(t.invoice.reference, reverse("billing_staff:invoice_detail", args=[t.invoice_id])),
            text(t.method_name or "—"), status(t.status, t.get_status_display()), text(t.amount, right=True)]

    def tickets():
        rows = client.tickets.select_related("department").order_by("-last_activity_at", "-id")
        return rows, ["Ticket", "Department", "Status", "Last activity"], lambda t: [
            text(f"{t.reference} {t.subject}", reverse("support_staff:ticket", args=[t.pk])), text(t.department.name),
            status(t.status, t.get_status_display()), text(t.last_activity_at)]

    def emails():
        from django.db.models import Q

        users = list(client.contacts.values_list("user_id", flat=True))
        addresses = [client.email, *client.contacts.values_list("user__email", flat=True)]
        rows = EmailMessage.objects.filter(Q(user_id__in=users) | Q(to_email__in=addresses)).order_by("-created_at", "-id")
        return rows, ["Sent", "To", "Subject", "Status", "Opened"], lambda m: [
            text(m.created_at), text(m.to_email), text(m.subject, reverse("notifications_staff:email", args=[m.pk])),
            status(m.status, m.get_status_display()), text(f"{m.open_count}×" if m.open_count else "—")]

    def cancellations():
        from apps.lifecycle import services as lifecycle

        rows = lifecycle.visible_requests_for_user(user).filter(client=client)
        return rows, ["Request", "Service", "When", "Status", "Sent"], lambda c: [
            text(c.code, reverse("lifecycle_staff:cancellation", args=[c.pk])), text(c.service_name),
            text(c.get_timing_display()), status(c.status, c.get_status_display()), text(c.created_at)]

    def affiliate():
        from apps.affiliates.models import Commission

        rows = Commission.objects.filter(referral__client=client).select_related("invoice").order_by("-created_at")
        return rows, ["Commission", "Invoice", "Status", "Amount", "Date"], lambda c: [
            text(c.code, reverse("affiliates_staff:commissions") + f"?q={c.invoice.number}"),
            text(c.invoice.reference, reverse("billing_staff:invoice_detail", args=[c.invoice_id])),
            status(c.status, c.get_status_display()), text(c.amount, right=True), text(c.created_at)]

    def log():
        from . import services

        rows = services.client_activity(client).order_by("-created_at", "-id")
        return rows, ["When", "Who", "What", "Detail"], lambda e: [
            text(e.created_at), text(e.actor_repr or "system"), text(e.action),
            text(", ".join(f"{k}: {v}" for k, v in (e.metadata or {}).items() if k != "client_id")[:160])]

    return {"services": hosting, "domains": domains, "billable": billable, "invoices": invoices, "quotes": quotes,
            "transactions": transactions, "tickets": tickets, "emails": emails, "cancellations": cancellations,
            "affiliate": affiliate, "log": log}


def table(user, client, key, page_number=1):
    """``{"columns", "page", "rows"}`` for one list tab: 25 rows a page, each row a list of cells."""
    from django.core.paginator import Paginator

    queryset, columns, make_row = _sources(client, user)[key]()
    page = Paginator(queryset, PAGE_SIZE).get_page(page_number)
    return {"columns": columns, "page": page, "rows": [make_row(row) for row in page]}


def counts(user, client):
    """``{tab key: how many records}`` for the tabs that list records (shown next to the tab's name)."""
    made = _sources(client, user)
    result = {}
    for tab in visible_tabs(user):
        if tab.key in made:
            result[tab.key] = made[tab.key]()[0].count()
    return result
