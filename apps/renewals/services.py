"""
Renewals and upgrades of existing services (roadmap Phase 08).

The shape of every flow is the same: work out the price on the server, create an invoice whose
lines show the calculation, remember what the invoice is *for* as a ``ServiceChange`` (with the
figures frozen on it), and apply the change when - and only when - that invoice is paid. Nothing the
browser sends is ever a price, credit, expiry or date.

The explicit renewal rule ("same-plan renewal follows explicit renewal rules"): a renewal is charged
at the plan's **current catalogue price for the account's billing cycle**, without setup fee and
without coupons, taxed under the client's current tax rules, and the price is fixed once invoiced.
Renewing a service that has not yet expired adds the new term to the end of the paid period (no time
is lost); renewing one that has expired starts a fresh term from the payment (no back-billing).
"""
import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.accounts.roles import perm
from apps.audit import services as audit
from apps.billing import calculations as calc
from apps.billing import invoicing
from apps.billing.models import BillingSettings, Invoice
from apps.core.exceptions import ServiceError
from apps.domains import services as domain_services
from apps.domains.models import Domain, DomainStatus
from apps.hosting import services as hosting_services
from apps.hosting.models import HostingAccount, HostingStatus
from apps.notifications import services as notifications
from apps.products import services as product_services
from apps.products.models import STANDARD_CYCLE_MONTHS, BillingCycle, CatalogStatus, Product

from .models import ChangeKind, ChangeStatus, ServiceChange
from .proration import Credit, add_months, unused_credit
from .signals import service_renewed

logger = logging.getLogger(__name__)

RECURRING_CYCLES = tuple(STANDARD_CYCLE_MONTHS) + (BillingCycle.CUSTOM,)


def _denied(message="You do not have permission to perform this action."):
    return ServiceError(message, code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)


def _require_billing(actor):
    if not actor.has_perm(perm("manage_billing")):
        raise _denied()


def cycle_months(billing_cycle, custom_months=0):
    """Months in a recurring billing cycle, or None if it is not a recurring cycle."""
    if billing_cycle == BillingCycle.CUSTOM:
        return custom_months or None
    return STANDARD_CYCLE_MONTHS.get(billing_cycle)


def cycle_label(billing_cycle, custom_months=0):
    if billing_cycle == BillingCycle.CUSTOM:
        return f"{custom_months} months"
    try:
        return BillingCycle(billing_cycle).label
    except ValueError:
        return billing_cycle or ""


def _day(moment):
    return timezone.localdate(moment).isoformat()


# --- Visibility -------------------------------------------------------------------------------------

def visible_changes_for_user(user):
    """Staff with view_billing see every change; a customer sees their own clients' changes."""
    queryset = ServiceChange.objects.select_related("client", "invoice", "hosting_account", "domain")
    if invoicing.is_staff_biller(user):
        return queryset
    if user and user.is_authenticated:
        return queryset.filter(client_id__in=invoicing._own_clients(user))
    return queryset.none()


# --- The paid term of a hosting account ---------------------------------------------------------------

@transaction.atomic
def set_hosting_term(actor, account, *, billing_cycle, custom_months=0, term_start, expires_at, term_paid,
                     request=None):
    """
    Staff: record the paid term of an existing hosting account (until Phase 09 fulfilment starts terms
    automatically). ``term_paid`` is what the client actually paid for the term, excluding tax and
    setup fees; it is the ceiling on any upgrade credit, so this is a billing decision.
    """
    _require_billing(actor)
    account = HostingAccount.objects.select_for_update().get(pk=account.pk)
    if cycle_months(billing_cycle, custom_months) is None:
        raise ValidationError({"billing_cycle": "Choose a recurring billing cycle (custom needs a month count)."})
    if billing_cycle != BillingCycle.CUSTOM:
        custom_months = 0
    if term_start is None or expires_at is None or expires_at <= term_start:
        raise ValidationError({"expires_at": "The paid-through date must be after the term start."})
    before = {"billing_cycle": account.billing_cycle, "expires_at": _day(account.expires_at) if account.expires_at
              else "", "term_paid": str(account.term_paid)}
    try:
        paid = calc.money(invoicing._decimal(term_paid, "Amount paid"))
    except ArithmeticError:
        raise ValidationError({"term_paid": "Enter a valid amount."})
    account.billing_cycle, account.custom_months = billing_cycle, custom_months
    account.term_start, account.expires_at, account.term_paid = term_start, expires_at, paid
    account.full_clean()
    account.save(update_fields=["billing_cycle", "custom_months", "term_start", "expires_at", "term_paid",
                                "updated_at"])
    audit.record("hosting.term_set", actor=actor, target=account,
                 metadata={"client_id": account.client_id, "from": before, "billing_cycle": billing_cycle,
                           "custom_months": custom_months, "term_start": _day(term_start),
                           "expires_at": _day(expires_at), "term_paid": str(account.term_paid)},
                 request=request)
    return account


def start_hosting_term(account, *, billing_cycle, custom_months=0, term_paid, now=None):
    """
    System entry point: begin the first paid term of a freshly provisioned account (called by order
    fulfilment). Idempotent - an account that already has a term is left alone, so running fulfilment twice
    can never restart or double a term. ``term_paid`` is what was paid for the plan, excluding tax and setup fees.
    """
    months = cycle_months(billing_cycle, custom_months)
    if months is None:
        raise ServiceError("This billing cycle has no term to start.", code="term_not_set")
    account = HostingAccount.objects.select_for_update().get(pk=account.pk)
    if account.expires_at is not None:
        return account
    now = now or timezone.now()
    account.billing_cycle = billing_cycle
    account.custom_months = custom_months if billing_cycle == BillingCycle.CUSTOM else 0
    account.term_start, account.expires_at, account.term_paid = now, add_months(now, months), calc.money(term_paid)
    account.save(update_fields=["billing_cycle", "custom_months", "term_start", "expires_at", "term_paid",
                                "updated_at"])
    audit.record("hosting.term_started", target=account,
                 metadata={"client_id": account.client_id, "billing_cycle": billing_cycle,
                           "expires_at": _day(account.expires_at), "term_paid": str(account.term_paid)})
    return account


def require_upgradeable(account):
    """Raise ServiceError unless ``account`` can be offered upgrades at all."""
    if account.status != HostingStatus.ACTIVE:
        raise ServiceError("Only an active account can be upgraded.", code="invalid_status")
    _require_term(account)
    _require_not_cancelling(account)


def _require_term(account):
    if not account.billing_cycle or account.expires_at is None or account.term_start is None:
        raise ServiceError("This account's billing term has not been set up yet. Please contact us.",
                           code="term_not_set")
    if cycle_months(account.billing_cycle, account.custom_months) is None:
        raise ServiceError("This account's billing cycle cannot be renewed online.", code="term_not_set")


# --- Creating the invoice and the change ------------------------------------------------------------------

def _require_not_cancelling(service):
    """A service with an open cancellation request is not renewed or upgraded (withdraw the request first)."""
    if service.cancellation_requests.filter(status__in=("pending", "approved")).exists():
        raise ServiceError("There is a cancellation request for this service. Withdraw it before renewing.",
                           code="cancellation_open")


def _pending_change(*, hosting=None, domain=None):
    queryset = ServiceChange.objects.filter(status=ChangeStatus.PENDING).select_related("invoice")
    return (queryset.filter(hosting_account=hosting) if hosting is not None else queryset.filter(domain=domain)).first()


def _open_change_error(pending):
    return ServiceError(f"An {pending.get_kind_display().lower()} invoice ({pending.invoice.reference}) is already "
                        "awaiting payment for this service. Pay or cancel it first.", code="change_pending")


def _save_change(**fields):
    change = ServiceChange(**fields)
    change.full_clean()
    change.save()
    return change


def _hosting_renewal(actor, account, *, request=None, notify=True):
    account = HostingAccount.objects.select_for_update().get(pk=account.pk)
    if account.status not in (HostingStatus.ACTIVE, HostingStatus.SUSPENDED):
        raise ServiceError("Only an active or suspended account can be renewed.", code="invalid_status")
    _require_term(account)
    _require_not_cancelling(account)
    pending = _pending_change(hosting=account)
    if pending is not None:
        if pending.kind == ChangeKind.RENEWAL:
            return pending  # asking twice gives the same invoice, never a second one
        raise _open_change_error(pending)
    months = cycle_months(account.billing_cycle, account.custom_months)
    row = product_services.get_effective_price(account.product, account.billing_cycle, account.custom_months)
    price = calc.money(row.price)
    now = timezone.now()
    start = account.expires_at if account.expires_at > now else now
    end = add_months(start, months)
    description = (f"Renewal: {account.product.name} ({cycle_label(account.billing_cycle, account.custom_months)})"
                   f" - {account.domain}, {_day(start)} to {_day(end)}")[:255]
    invoice = invoicing.create_draft_invoice(
        actor, account.client, lines=[{"description": description, "quantity": 1, "unit_price": price}],
        notes="Renewal at the plan's current price. The new term is added to your paid period.", request=request)
    change = _save_change(
        client=account.client, invoice=invoice, kind=ChangeKind.RENEWAL, hosting_account=account,
        from_product=account.product, to_product=account.product, billing_cycle=account.billing_cycle,
        custom_months=account.custom_months, period_months=months, paid_value=price,
        expected_expires_at=account.expires_at,
        calculation={"price": str(price), "months": months, "start": _day(start), "end": _day(end)})
    audit.record("service.renewal_invoiced", actor=actor, target=account,
                 metadata={"client_id": account.client_id, "invoice_id": invoice.pk, "price": str(price),
                           "months": months}, request=request)
    invoicing.issue_draft_invoice(actor, invoice, notify=notify, request=request)
    change.refresh_from_db()
    return change


@transaction.atomic
def create_hosting_renewal(actor, account, *, request=None):
    """Invoice the renewal of a hosting account (its client's contacts, or staff who manage hosting)."""
    if not hosting_services.can_self_service(actor, account):
        raise _denied()
    return _hosting_renewal(actor, account, request=request)


def _domain_renewal(actor, domain, years, *, request=None, notify=True):
    domain = Domain.objects.select_for_update().get(pk=domain.pk)
    if domain.status != DomainStatus.ACTIVE:
        raise ServiceError("Only an active domain can be renewed.", code="invalid_status")
    _require_not_cancelling(domain)
    pricing = domain_services.get_tld_pricing(domain.tld, require_active=False)
    if not isinstance(years, int) or not (1 <= years <= pricing.max_years):
        raise ServiceError(f"Choose a term between 1 and {pricing.max_years} years.", code="invalid_term")
    pending = _pending_change(domain=domain)
    if pending is not None:
        if pending.kind == ChangeKind.RENEWAL and pending.period_months == years * 12:
            return pending
        raise _open_change_error(pending)
    price = calc.money(pricing.renew_price * years)
    base = domain.expires_at if domain.expires_at and domain.expires_at > timezone.now() else timezone.now()
    invoice = invoicing.create_draft_invoice(
        actor, domain.client, lines=[{"description": f"Domain renewal: {domain.name} ({years} year"
                                                     f"{'s' if years != 1 else ''})",
                                      "quantity": 1, "unit_price": price}],
        notes="Renewal at the TLD's current renewal price.", request=request)
    change = _save_change(
        client=domain.client, invoice=invoice, kind=ChangeKind.RENEWAL, domain=domain, period_months=years * 12,
        paid_value=price, expected_expires_at=domain.expires_at,
        calculation={"price": str(price), "years": years, "start": _day(base)})
    audit.record("service.renewal_invoiced", actor=actor, target=domain,
                 metadata={"client_id": domain.client_id, "invoice_id": invoice.pk, "price": str(price),
                           "years": years}, request=request)
    invoicing.issue_draft_invoice(actor, invoice, notify=notify, request=request)
    change.refresh_from_db()
    return change


@transaction.atomic
def create_domain_renewal(actor, domain, years, *, request=None):
    """Invoice the renewal of a domain for ``years`` years."""
    if not domain_services.can_self_service(actor, domain):
        raise _denied()
    return _domain_renewal(actor, domain, years, request=request)


# --- Upgrades ----------------------------------------------------------------------------------------------

@dataclass
class UpgradePreview:
    account: HostingAccount
    from_product: Product
    to_product: Product
    billing_cycle: str
    custom_months: int
    months: int
    current_price: Decimal
    new_price: Decimal
    credit: Credit
    applied_credit: Decimal
    forfeited: Decimal
    net_payable: Decimal
    new_expiry: object

    def calculation(self):
        """Everything needed to re-derive the credit later, as JSON-safe values."""
        account = self.account
        return {
            "term_paid": str(self.credit.term_paid), "term_days": self.credit.term_days,
            "remaining_days": self.credit.remaining_days, "credit": str(self.credit.amount),
            "applied_credit": str(self.applied_credit), "forfeited": str(self.forfeited),
            "new_price": str(self.new_price), "net_payable": str(self.net_payable),
            "term_start": _day(account.term_start), "expires_at": _day(account.expires_at),
            "billing_cycle": self.billing_cycle, "custom_months": self.custom_months,
            "months": self.months, "from_product_id": self.from_product.pk, "to_product_id": self.to_product.pk,
        }

    @property
    def explanation(self):
        text = (f"Upgrade from {self.from_product.name} to {self.to_product.name} "
                f"({cycle_label(self.billing_cycle, self.custom_months)}). {self.credit.formula} "
                f"New plan price {self.new_price} - credit {self.applied_credit} = {self.net_payable} before tax.")
        if self.forfeited:
            text += f" {self.forfeited} of credit exceeds the new price and is not carried forward."
        return text


def preview_upgrade(account, new_product, *, now=None):
    """The server-side calculation of upgrading ``account`` to ``new_product``; raises ServiceError if not allowed."""
    now = now or timezone.now()
    if account.status != HostingStatus.ACTIVE:
        raise ServiceError("Only an active account can be upgraded.", code="invalid_status")
    _require_term(account)
    if new_product.pk == account.product_id:
        raise ServiceError("The account is already on this plan.", code="same_plan")
    if (new_product.status != CatalogStatus.ACTIVE or not new_product.whm_package_name
            or new_product.type != account.product.type):
        raise ServiceError("This plan is not available for an upgrade.", code="product_unavailable")
    months = cycle_months(account.billing_cycle, account.custom_months)
    try:
        new_row = product_services.get_effective_price(new_product, account.billing_cycle, account.custom_months)
    except ServiceError:
        raise ServiceError("This plan is not offered for your billing cycle.", code="price_not_found")
    try:
        current_row = product_services.get_effective_price(account.product, account.billing_cycle,
                                                           account.custom_months)
    except ServiceError:
        current_row = None
    new_price = calc.money(new_row.price)
    if current_row is not None and new_price < calc.money(current_row.price):
        raise ServiceError("Downgrades are not available online. Please contact us.", code="downgrade_not_allowed")
    if (new_product.servers.exists() and account.server_id
            and not new_product.servers.filter(pk=account.server_id).exists()):
        raise ServiceError("This plan is not available on the server your account is hosted on.",
                           code="server_mismatch")
    credit = unused_credit(account.term_paid, account.term_start, account.expires_at, now=now)
    applied = min(credit.amount, new_price)
    return UpgradePreview(
        account=account, from_product=account.product, to_product=new_product,
        billing_cycle=account.billing_cycle, custom_months=account.custom_months, months=months,
        current_price=calc.money(current_row.price) if current_row is not None else calc.ZERO,
        new_price=new_price, credit=credit, applied_credit=applied, forfeited=credit.amount - applied,
        net_payable=new_price - applied, new_expiry=add_months(now, months))


def available_upgrades(account, *, now=None):
    """Previews for every plan the account could move to (for the customer's chooser)."""
    previews = []
    candidates = Product.objects.filter(status=CatalogStatus.ACTIVE, type=account.product.type).exclude(
        pk=account.product_id).exclude(whm_package_name="").order_by("name")
    for product in candidates:
        try:
            previews.append(preview_upgrade(account, product, now=now))
        except ServiceError:
            continue
    return previews


@transaction.atomic
def create_upgrade(actor, account, new_product, *, request=None):
    """Invoice an upgrade. The credit is recomputed here from the stored term - never taken from the caller."""
    if not hosting_services.can_self_service(actor, account):
        raise _denied()
    account = HostingAccount.objects.select_for_update().get(pk=account.pk)
    pending = _pending_change(hosting=account)
    if pending is not None:
        if pending.kind == ChangeKind.UPGRADE and pending.to_product_id == new_product.pk:
            return pending
        raise _open_change_error(pending)
    preview = preview_upgrade(account, new_product)
    description = (f"Upgrade to {new_product.name} ({cycle_label(preview.billing_cycle, preview.custom_months)})"
                   f" - {account.domain}, new {preview.months}-month term")[:255]
    credit_label = (f"Credit for unused time on {preview.from_product.name} "
                    f"({preview.credit.remaining_days} of {preview.credit.term_days} days)")
    invoice = invoicing.create_draft_invoice(
        actor, account.client, lines=[{"description": description, "quantity": 1, "unit_price": preview.new_price}],
        discount_type="fixed" if preview.applied_credit > 0 else "",
        discount_value=preview.applied_credit if preview.applied_credit > 0 else None,
        discount_label=credit_label, notes=preview.explanation, request=request)
    change = _save_change(
        client=account.client, invoice=invoice, kind=ChangeKind.UPGRADE, hosting_account=account,
        from_product=preview.from_product, to_product=new_product, billing_cycle=preview.billing_cycle,
        custom_months=preview.custom_months, period_months=preview.months, paid_value=preview.new_price,
        expected_expires_at=account.expires_at, calculation=preview.calculation())
    audit.record("service.upgrade_invoiced", actor=actor, target=account,
                 metadata={"client_id": account.client_id, "invoice_id": invoice.pk,
                           "from": preview.from_product.name, "to": new_product.name,
                           "credit": str(preview.applied_credit), "net_payable": str(preview.net_payable)},
                 request=request)
    invoicing.issue_draft_invoice(actor, invoice, request=request)
    change.refresh_from_db()
    return change


# --- Applying and voiding -----------------------------------------------------------------------------------

def _apply_hosting(change):
    account = HostingAccount.objects.select_for_update().get(pk=change.hosting_account_id)
    now = timezone.now()
    months = change.period_months
    if change.kind == ChangeKind.RENEWAL:
        if account.status not in (HostingStatus.ACTIVE, HostingStatus.SUSPENDED):
            raise ServiceError(f"The account is {account.get_status_display().lower()} and cannot be renewed.")
        if account.expires_at and account.expires_at > now:
            account.expires_at = add_months(account.expires_at, months)
            account.term_paid += change.paid_value
            account.term_start = account.term_start or now
        else:
            account.term_start, account.expires_at, account.term_paid = now, add_months(now, months), change.paid_value
    else:
        if (account.status != HostingStatus.ACTIVE or account.product_id != change.from_product_id
                or account.expires_at != change.expected_expires_at):
            raise ServiceError("The service changed after this invoice was issued, so the upgrade was not applied. "
                               "Refund the invoice or issue a new upgrade.")
        hosting_services.change_package_as_system(account, change.to_product)
        account.term_start, account.expires_at, account.term_paid = now, add_months(now, months), change.paid_value
    account.save(update_fields=["term_start", "expires_at", "term_paid", "updated_at"])
    return account, account.expires_at


def _apply_domain(change):
    domain = Domain.objects.select_for_update().get(pk=change.domain_id)
    domain_services.renew_domain_as_system(domain, change.period_months // 12)
    domain.refresh_from_db()
    return domain, domain.expires_at


def _announce_applied(change, service, expires):
    from django.urls import reverse

    if change.hosting_account_id:
        link, name = reverse("hosting_customer:detail", args=[service.pk]), f"Hosting for {service.domain}"
    else:
        link, name = reverse("domains_customer:detail", args=[service.pk]), service.name
    renewal = change.kind == ChangeKind.RENEWAL
    notifications.dispatch_client(
        "service.renewed" if renewal else "service.upgraded", change.client,
        title=f"{name} has been {'renewed' if renewal else 'upgraded'}", link=link,
        context={"service": name, "new_expiry": expires, "link": link,
                 "plan": change.to_product.name if change.to_product_id else ""})


def _apply(change, actor=None):
    """Apply one change. A failure is recorded on the change (the payment stands) rather than raised."""
    try:
        with transaction.atomic():  # a savepoint: a half-done apply is undone before the failure is recorded
            service, expires = _apply_hosting(change) if change.hosting_account_id else _apply_domain(change)
    except ServiceError as exc:
        error = exc.message
    except Exception:  # noqa: BLE001 - the payment has been taken; never fail it because a service call broke
        logger.exception("Applying service change %s failed", change.pk)
        error = "An unexpected error occurred while applying this change."
    else:
        change.status, change.applied_at, change.error = ChangeStatus.APPLIED, timezone.now(), ""
        change.save(update_fields=["status", "applied_at", "error", "updated_at"])
        audit.record(f"service.{'renewed' if change.kind == ChangeKind.RENEWAL else 'upgraded'}", actor=actor,
                     target=service, metadata={"client_id": change.client_id, "invoice_id": change.invoice_id,
                                               "change_id": change.pk, "new_expiry": _day(expires),
                                               "months": change.period_months})
        _announce_applied(change, service, expires)
        service_renewed.send(sender=ServiceChange, change=change, service=service)
        return change
    change.status, change.error = ChangeStatus.FAILED, error[:500]
    change.save(update_fields=["status", "error", "updated_at"])
    audit.record("service.change_failed", actor=actor, target=change.service,
                 metadata={"client_id": change.client_id, "invoice_id": change.invoice_id, "change_id": change.pk,
                           "kind": change.kind, "error": change.error})
    return change


def apply_paid_invoice(invoice, *, actor=None):
    """Called when an invoice becomes fully paid: apply each of its pending changes (idempotent)."""
    changes = ServiceChange.objects.select_for_update().filter(invoice=invoice, status=ChangeStatus.PENDING)
    for change in list(changes.order_by("id")):
        _apply(change, actor)


def void_changes(invoice):
    for change in ServiceChange.objects.select_for_update().filter(invoice=invoice, status=ChangeStatus.PENDING):
        change.status = ChangeStatus.VOID
        change.save(update_fields=["status", "updated_at"])
        audit.record("service.change_voided", target=change.service,
                     metadata={"change_id": change.pk, "invoice_id": invoice.pk})


def retry_change(actor, change, *, request=None):
    """
    Staff: try again to apply a paid change that failed (e.g. after fixing the registrar or server).
    If it fails again the new outcome is recorded and reported, not hidden.
    """
    _require_billing(actor)
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().get(pk=change.invoice_id)  # invoice first, as everywhere
        change = ServiceChange.objects.select_for_update().get(pk=change.pk)
        if change.status != ChangeStatus.FAILED:
            raise ServiceError("Only a change that failed after payment can be retried.", code="invalid_status")
        if invoice.amount_paid < invoice.total:
            raise ServiceError("The invoice is not fully paid.", code="invoice_not_paid")
        result = _apply(change, actor)
    if result.status == ChangeStatus.FAILED:
        raise ServiceError(f"It still cannot be applied: {result.error}", code="apply_failed")
    return result


@transaction.atomic
def dismiss_change(actor, change, *, note="", request=None):
    """Staff: close a failed change that was resolved by hand (e.g. refunded), so it stops needing attention."""
    _require_billing(actor)
    invoice = Invoice.objects.select_for_update().get(pk=change.invoice_id)  # noqa: F841 - lock order only
    change = ServiceChange.objects.select_for_update().get(pk=change.pk)
    if change.status != ChangeStatus.FAILED:
        raise ServiceError("Only a change that failed after payment can be dismissed.", code="invalid_status")
    change.status = ChangeStatus.VOID
    change.save(update_fields=["status", "updated_at"])
    audit.record("service.change_dismissed", actor=actor, target=change.service,
                 metadata={"change_id": change.pk, "note": note[:500]}, request=request)
    return change


# --- Scheduled renewal invoicing --------------------------------------------------------------------------------

def due_for_renewal(*, now=None, lead_days=None):
    """(hosting accounts, domains) whose paid period ends within the lead time and have no open change."""
    now = now or timezone.now()
    lead = BillingSettings.load().renewal_invoice_days if lead_days is None else lead_days
    horizon = now + timedelta(days=lead)
    pending = ServiceChange.objects.filter(status=ChangeStatus.PENDING)
    hosting = (HostingAccount.objects.filter(status=HostingStatus.ACTIVE, expires_at__isnull=False,
                                             expires_at__lte=horizon)
               .exclude(billing_cycle="").exclude(pk__in=pending.filter(hosting_account__isnull=False)
                                                  .values("hosting_account_id"))
               .exclude(cancellation_requests__status__in=("pending", "approved")).order_by("expires_at"))
    domains = (Domain.objects.filter(status=DomainStatus.ACTIVE, auto_renew=True, expires_at__isnull=False,
                                     expires_at__lte=horizon)
               .exclude(pk__in=pending.filter(domain__isnull=False).values("domain_id"))
               .exclude(cancellation_requests__status__in=("pending", "approved")).order_by("expires_at"))
    return list(hosting), list(domains)


def generate_renewal_invoices(*, now=None, lead_days=None):
    """
    Create the renewal invoice for every service due within the lead time (run daily). Safe to run
    repeatedly: a service with an open change is skipped. One failure never stops the rest.
    """
    hosting, domains = due_for_renewal(now=now, lead_days=lead_days)
    result = {"hosting": 0, "domains": 0, "errors": []}
    for kind, items, create in (("hosting", hosting, _hosting_renewal),
                                ("domains", domains, lambda actor, obj: _domain_renewal(actor, obj, 1))):
        for obj in items:
            try:
                with transaction.atomic():
                    create(None, obj)
                result[kind] += 1
            except (ServiceError, ValidationError) as exc:
                message = exc.message if isinstance(exc, ServiceError) else "; ".join(exc.messages)
                result["errors"].append(f"{kind} {obj.pk}: {message}")
            except Exception:  # noqa: BLE001
                logger.exception("Renewal invoice for %s %s failed", kind, obj.pk)
                result["errors"].append(f"{kind} {obj.pk}: unexpected error")
    return result
