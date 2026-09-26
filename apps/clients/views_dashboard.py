"""The customer's home page (roadmap Section 10.3): what needs attention, and a way into everything else."""
from collections import defaultdict
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.affiliates import services as affiliate_services
from apps.billing import invoicing
from apps.billing.models import OPEN_STATUSES, QuoteStatus
from apps.core import portal
from apps.domains import services as domain_services
from apps.domains.models import DomainStatus
from apps.hosting import services as hosting_services
from apps.hosting.models import HostingStatus
from apps.support import services as support_services
from apps.support.models import TicketStatus




def primary_client(user):
    """The account the sidebar describes: the one they own, else the first they belong to."""
    contacts = user.client_contacts.select_related("client").order_by("role", "id")
    contact = contacts.first()
    return contact.client if contact else None


def overdue_summary(invoices):
    """What is overdue, per currency: ``[(currency, total)]``. An overdue invoice is derived, never stored."""
    overdue = invoicing.filter_invoices_by_status(invoices, "overdue")
    totals = defaultdict(lambda: Decimal("0.00"))
    for invoice in overdue:
        totals[invoice.currency] += invoice.balance_due
    return overdue, sorted(totals.items())


@login_required
def dashboard(request):
    user = request.user
    if user.is_staff:  # the staff dashboard is Phase D4
        return redirect("accounts:profile")
    client = primary_client(user)
    context = {"client": client, "side_panels": []}
    if client is None:
        context["side_panels"] = [portal.Panel("Shortcuts", [
            portal.link(request, "Browse plans", "catalog:product_list", icon="bi-box"),
            portal.link(request, "Profile & security", "accounts:profile", icon="bi-person")])]
        return render(request, "clients/customer/dashboard.html", context)

    hosting = hosting_services.visible_hosting_accounts_for_user(user)
    domains = domain_services.visible_domains_for_user(user)
    invoices = invoicing.visible_invoices_for_user(user)
    quotes = invoicing.visible_quotes_for_user(user)
    tickets = support_services.visible_tickets_for_user(user)
    overdue, overdue_totals = overdue_summary(invoices)
    unpaid = invoices.filter(status__in=OPEN_STATUSES)
    affiliate = affiliate_services.get_affiliate(user)

    context.update({
        "tiles": [
            {"label": "Services", "value": hosting.filter(status=HostingStatus.ACTIVE).count(), "icon": "bi-hdd-network",
             "url": reverse("hosting_customer:list") + "?status=active"},
            {"label": "Domains", "value": domains.filter(status=DomainStatus.ACTIVE).count(), "icon": "bi-globe",
             "url": reverse("domains_customer:list") + "?status=active"},
            {"label": "Quotes", "value": quotes.filter(status=QuoteStatus.SENT).count(), "icon": "bi-file-earmark-text",
             "url": reverse("billing_customer:quote_list") + "?status=sent"},
            {"label": "Tickets", "value": tickets.exclude(status=TicketStatus.CLOSED).count(), "icon": "bi-life-preserver",
             "url": reverse("support_customer:list")},
            {"label": "Invoices", "value": unpaid.count(), "icon": "bi-receipt",
             "url": reverse("billing_customer:invoice_list") + "?status=unpaid"},
        ],
        "overdue_count": overdue.count(),
        "overdue_totals": overdue_totals,
        "overdue_first": overdue.order_by("due_date", "id").first(),
        "services": hosting.filter(status__in=(HostingStatus.ACTIVE, HostingStatus.SUSPENDED)).order_by("expires_at", "id")[:5],
        "recent_tickets": tickets.order_by("-last_activity_at", "-id")[:5],
        "affiliate": affiliate,
        "affiliate_balances": affiliate_services.balances(affiliate) if affiliate else None,
        "side_panels": [portal.your_info(client), portal.contacts_panel(request, client), portal.shortcuts(request)],
    })
    return render(request, "clients/customer/dashboard.html", context)
