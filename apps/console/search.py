"""
Global staff search (roadmap Section 11.1): one box that finds clients, invoices, orders, domains, hosting accounts and
tickets. Each kind uses that area's own search function and appears only if the person may open that area, so search can
never reveal what a menu would hide.
"""
from dataclasses import dataclass, field

from django.urls import reverse

from apps.accounts.roles import perm
from apps.billing import invoicing
from apps.billing.models import Invoice
from apps.clients import services as client_services
from apps.clients.models import Client
from apps.domains import services as domain_services
from apps.domains.models import Domain
from apps.hosting import services as hosting_services
from apps.hosting.models import HostingAccount
from apps.orders import services as order_services
from apps.orders.models import Order
from apps.support import services as support_services
from apps.support.models import Ticket

MIN_LENGTH = 2
LIMIT = 6


@dataclass
class Hit:
    title: str
    detail: str
    url: str


@dataclass
class Group:
    label: str
    icon: str
    hits: list = field(default_factory=list)
    total: int = 0
    all_url: str = ""


def _group(label, icon, queryset, list_name, term, make):
    rows = queryset
    return Group(label, icon, [make(row) for row in rows[:LIMIT]], rows.count(), f"{reverse(list_name)}?q={term}")


def search(user, term):
    """``[Group]`` for ``term``; empty when the term is too short. Only areas the person may open are searched."""
    term = (term or "").strip()
    if len(term) < MIN_LENGTH:
        return []
    groups = []

    def allowed(codename):
        return user.has_perm(perm(codename))

    if allowed("view_clients"):
        rows = client_services.search_clients(Client.objects.all(), term).order_by("-created_at")
        groups.append(_group("Clients", "bi-people", rows, "clients_staff:list", term, lambda c: Hit(
            f"{c.display_name}", f"{c.reference} · {c.email}", reverse("clients_staff:detail", args=[c.pk]))))
    if allowed("view_billing"):
        rows = invoicing.search_invoices(Invoice.objects.select_related("client"), term).order_by("-created_at")
        groups.append(_group("Invoices", "bi-receipt", rows, "billing_staff:invoice_list", term, lambda i: Hit(
            i.number or f"Draft #{i.pk}", f"{i.client.display_name} · {i.get_status_display()}",
            reverse("billing_staff:invoice_detail", args=[i.pk]))))
    if allowed("view_orders"):
        rows = order_services.search_orders(Order.objects.select_related("client"), term).order_by("-created_at")
        groups.append(_group("Orders", "bi-bag", rows, "orders_staff:list", term, lambda o: Hit(
            o.reference, f"{o.client.display_name} · {o.get_status_display()}", reverse("orders_staff:detail", args=[o.pk]))))
    if allowed("view_domains"):
        rows = domain_services.search_domains(Domain.objects.select_related("client"), term).order_by("name")
        groups.append(_group("Domains", "bi-globe", rows, "domains_staff:list", term, lambda d: Hit(
            d.name, f"{d.client.display_name} · {d.get_status_display()}", reverse("domains_staff:detail", args=[d.pk]))))
    if allowed("view_hosting"):
        rows = hosting_services.search_hosting_accounts(HostingAccount.objects.select_related("client", "product"), term
                                                        ).order_by("domain")
        groups.append(_group("Hosting", "bi-hdd-network", rows, "hosting_staff:list", term, lambda a: Hit(
            a.domain, f"{a.client.display_name} · {a.product.name} · {a.get_status_display()}",
            reverse("hosting_staff:detail", args=[a.pk]))))
    if support_services.can_view_all(user):
        rows = support_services.search_tickets(Ticket.objects.select_related("client"), term).order_by("-last_activity_at")
        groups.append(_group("Tickets", "bi-life-preserver", rows, "support_staff:tickets", term, lambda t: Hit(
            f"{t.reference} {t.subject}", f"{t.client.display_name} · {t.get_status_display()}",
            reverse("support_staff:ticket", args=[t.pk]))))
    return [g for g in groups if g.total]
