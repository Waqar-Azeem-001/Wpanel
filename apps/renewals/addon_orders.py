"""
Buying an extra (an SSL certificate, WHOIS privacy) for something the customer already has: an invoice made on the spot, then a
person sets it up once it is paid. Prices come from the catalogue, never from the browser.
"""
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from apps.audit import services as audit
from apps.billing import calculations as calc
from apps.billing import invoicing
from apps.core.exceptions import ServiceError
from apps.core.locks import single_flight
from apps.domains import services as domain_services
from apps.domains.models import DomainStatus
from apps.hosting import services as hosting_services
from apps.hosting.models import HostingStatus
from apps.notifications import services as notifications
from apps.products.categories import lowest_price
from apps.products.models import Addon, AddonKind, CatalogStatus

from .models import AddonOrder, AddonOrderStatus

OPEN = (AddonOrderStatus.PENDING, AddonOrderStatus.PAID)


def open_order(addon_kind, *, hosting_account=None, domain=None):
    """The order still in progress (or already active) for this kind of extra on this service, or None."""
    queryset = AddonOrder.objects.filter(addon__kind=addon_kind, status__in=(*OPEN, AddonOrderStatus.DONE)).select_related("addon", "invoice")
    return (queryset.filter(hosting_account=hosting_account) if hosting_account is not None else queryset.filter(domain=domain)).first()


def buy_addon(actor, addon, *, hosting_account=None, domain=None, request=None):
    """Invoice one extra for one of the customer's own services and return the order (asking twice returns the same one, even at the same instant)."""
    target = hosting_account or domain
    kind = getattr(addon, "kind", "")
    with single_flight(f"addon-order:{'h' if hosting_account is not None else 'd'}{getattr(target, 'pk', 0)}:{kind}"):  # outside the transaction, so a twin sees the first one's committed order
        return _buy_addon(actor, addon, hosting_account=hosting_account, domain=domain, request=request)


@transaction.atomic
def _buy_addon(actor, addon, *, hosting_account=None, domain=None, request=None):
    if (hosting_account is None) == (domain is None):
        raise ServiceError("Choose a hosting plan or a domain.", code="invalid_target")
    target = hosting_account or domain
    allowed = hosting_services.can_self_service(actor, target) if hosting_account is not None else domain_services.can_self_service(actor, target)
    if not allowed:
        raise ServiceError("You do not have permission to perform this action.", code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)
    if not isinstance(addon, Addon) or addon.status != CatalogStatus.ACTIVE:
        raise ServiceError("This extra is not available right now.", code="addon_unavailable")
    if (addon.applies_to == "domain") != (domain is not None):
        raise ServiceError("This extra cannot be added to that.", code="addon_mismatch")
    if hosting_account is not None and hosting_account.status not in (HostingStatus.ACTIVE, HostingStatus.SUSPENDED):
        raise ServiceError("Only an active hosting plan can get this extra.", code="invalid_status")
    if domain is not None and domain.status != DomainStatus.ACTIVE:
        raise ServiceError("Only an active domain can get this extra.", code="invalid_status")
    existing = open_order(addon.kind, hosting_account=hosting_account, domain=domain)
    if existing is not None:
        if existing.status == AddonOrderStatus.DONE:
            raise ServiceError(f"{existing.addon.name} is already active here.", code="addon_active")
        return existing  # still waiting for payment or set-up: never a second invoice
    row = lowest_price(addon)
    if row is None:
        raise ServiceError("This extra has no price yet.", code="addon_unpriced")
    price = calc.money(row.price)
    period = row.get_billing_cycle_display()
    label = hosting_account.domain if hosting_account is not None else domain.name
    invoice = invoicing.create_draft_invoice(
        actor, target.client, lines=[{"description": f"{addon.name} for {label} ({period.lower()})"[:255], "quantity": 1, "unit_price": price}],
        notes="An extra for something you already have. We set it up as soon as the invoice is paid.", request=request)
    order = AddonOrder.objects.create(client=target.client, invoice=invoice, addon=addon, hosting_account=hosting_account, domain=domain,
                                      price=price, period_label=period)
    audit.record("addon.ordered", actor=actor, target=target, metadata={"addon": addon.name, "invoice_id": invoice.pk, "price": str(price)}, request=request)
    invoicing.issue_draft_invoice(actor, invoice, notify=True, request=request)
    order.refresh_from_db()
    return order


def apply_paid_invoice(invoice):
    for order in AddonOrder.objects.select_for_update().filter(invoice=invoice, status=AddonOrderStatus.PENDING):
        order.status, order.paid_at = AddonOrderStatus.PAID, timezone.now()
        order.save(update_fields=["status", "paid_at", "updated_at"])
        audit.record("addon.paid", target=order.hosting_account or order.domain, metadata={"addon": order.addon.name, "invoice_id": invoice.pk})
        notifications.notify_team(
            "addon.ordered", "manage_billing", title=f"{order.addon.name} paid for {order.service_label}: set it up",
            body=f"{order.client.display_name} bought {order.addon.name} for {order.service_label} ({order.invoice.reference}). Set it up, then mark it done.",
            link=reverse("renewals_staff:addon_orders"))


def void_orders(invoice):
    for order in AddonOrder.objects.select_for_update().filter(invoice=invoice, status=AddonOrderStatus.PENDING):
        order.status = AddonOrderStatus.VOID
        order.save(update_fields=["status", "updated_at"])


@transaction.atomic
def mark_done(actor, order, *, request=None):
    if not actor.has_perm("accounts.manage_billing"):
        raise ServiceError("You do not have permission to perform this action.", code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)
    order = AddonOrder.objects.select_for_update().get(pk=order.pk)
    if order.status != AddonOrderStatus.PAID:
        raise ServiceError("Only a paid order can be marked done.", code="invalid_status")
    order.status, order.done_at, order.done_by = AddonOrderStatus.DONE, timezone.now(), actor
    order.save(update_fields=["status", "done_at", "done_by", "updated_at"])
    audit.record("addon.done", actor=actor, target=order.hosting_account or order.domain, metadata={"addon": order.addon.name}, request=request)
    notifications.dispatch_client("addon.ready", order.client, title=f"{order.addon.name} is ready", link=reverse("billing_customer:invoice_detail", args=[order.invoice_id]),
                                  context={"addon": order.addon.name, "service": order.service_label})
    return order
