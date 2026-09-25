"""
Cancellation and the service lifecycle (roadmap Phase 12).

Two separate things share this module because they end the same way:

* **Cancellation** - a customer (or staff on their behalf) asks to end one service; staff review it, choose
  immediate or end-of-term, optionally refund part of what was paid, and the service is ended. Approval does the
  *final billing* (unpaid renewal invoices for the service are cancelled, a domain stops auto-renewing), then the
  service is ended right away or, for end-of-term, by the daily job on the day the paid period runs out.
* **The lifecycle of an unpaid service** - ``Active -> Renewal due -> Overdue -> Grace -> Suspended -> Terminated``.
  The stage is *derived* from the paid-through date and the service's status (like an invoice being overdue); the
  daily job acts on it (final notice, suspension, and - only if switched on - termination) using the timings in
  ``LifecycleSettings``, and paying the renewal lifts a non-payment suspension.

Rules that do not bend: the payment is never undone by a failure here; every status change is one audited
``transition``; ending a service is claimed with a lock-free update so two workers never end it twice; money moves only
through ``billing.payments.refund_payment``.
"""
import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from apps.accounts.roles import perm
from apps.audit import services as audit
from apps.billing import invoicing, payments
from apps.billing.models import (BillingSettings, Invoice, InvoiceStatus, Transaction, TransactionStatus,
                                 TransactionType)
from apps.clients.models import ContactRole
from apps.clients.services import contact_role
from apps.core.exceptions import ServiceError
from apps.core.system import SYSTEM
from apps.domains.models import Domain, DomainStatus
from apps.hosting import services as hosting_services
from apps.hosting.models import HostingAccount, HostingStatus
from apps.notifications import services as notifications
from apps.orders.models import OrderItem
from apps.renewals.models import ChangeStatus, ServiceChange
from apps.renewals.proration import unused_credit

from . import transitions
from .models import (OPEN_STATUSES, CancellationReason, CancellationRequest, CancellationStatus, LifecycleNotice,
                     LifecycleSettings, Stage, Timing)

logger = logging.getLogger(__name__)

CLAIM_TIMEOUT = timedelta(minutes=10)


def _denied(message="You do not have permission to perform this action."):
    return ServiceError(message, code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)


def kind_of(service):
    return "hosting" if isinstance(service, HostingAccount) else "domain"


def _manage_codename(service):
    return "manage_hosting" if isinstance(service, HostingAccount) else "manage_domains"


def _view_codename(service):
    return "view_hosting" if isinstance(service, HostingAccount) else "view_domains"


def _name(service):
    return service.domain if isinstance(service, HostingAccount) else service.name


def can_manage(actor, service):
    return bool(actor and actor.is_authenticated and actor.has_perm(perm(_manage_codename(service))))


def _require_manage(actor, service):
    if not can_manage(actor, service):
        raise _denied()


def is_staff_reviewer(user):
    return bool(user and user.is_authenticated
                and (user.has_perm(perm("view_hosting")) or user.has_perm(perm("view_domains"))))


# --- Stages ---------------------------------------------------------------------------------------------------

def stage_of(service, *, now=None, config=None, lead_days=None):
    """The service's ``Stage`` (or None when it is not a live service: pending, failed, cancelled)."""
    now = now or timezone.now()
    config = config or LifecycleSettings.load()
    if isinstance(service, HostingAccount):
        if service.status == HostingStatus.TERMINATED:
            return Stage.TERMINATED
        if service.status == HostingStatus.SUSPENDED:
            return Stage.SUSPENDED
        if service.status != HostingStatus.ACTIVE:
            return None
    else:
        if service.status == DomainStatus.EXPIRED:
            return Stage.EXPIRED
        if service.status != DomainStatus.ACTIVE:
            return None
    expires = service.expires_at
    if expires is None or (isinstance(service, HostingAccount) and not service.billing_cycle):
        return Stage.ACTIVE  # no paid term recorded: nothing can lapse
    if now < expires:
        lead = BillingSettings.load().renewal_invoice_days if lead_days is None else lead_days
        return Stage.RENEWAL_DUE if now >= expires - timedelta(days=lead) else Stage.ACTIVE
    days_past = (now - expires).days
    return Stage.OVERDUE if days_past < config.grace_after_days else Stage.GRACE


def timeline(service, *, config=None):
    """The dates the settings imply for this service (None where they do not apply)."""
    config = config or LifecycleSettings.load()
    if not isinstance(service, HostingAccount) or service.expires_at is None or not service.billing_cycle:
        return {"grace": None, "suspend": None, "terminate": None}
    return {"grace": service.expires_at + timedelta(days=config.grace_after_days),
            "suspend": service.expires_at + timedelta(days=config.suspend_after_days) if config.auto_suspend else None,
            "terminate": (service.expires_at + timedelta(days=config.terminate_after_days)
                          if config.auto_terminate else None)}


def open_request_for(service):
    field = "hosting_account" if isinstance(service, HostingAccount) else "domain"
    return CancellationRequest.objects.filter(**{field: service}, status__in=OPEN_STATUSES).first()


def overview(*, now=None):
    """What the staff Lifecycle page shows: services by stage, and cancellations waiting."""
    now = now or timezone.now()
    config = LifecycleSettings.load()
    lead = BillingSettings.load().renewal_invoice_days
    rows = {stage: [] for stage in (Stage.RENEWAL_DUE, Stage.OVERDUE, Stage.GRACE, Stage.SUSPENDED)}
    counts = {stage: 0 for stage in Stage}
    hosting = HostingAccount.objects.filter(status__in=(HostingStatus.ACTIVE, HostingStatus.SUSPENDED)
                                            ).select_related("client", "product").order_by("expires_at")
    for account in hosting:
        stage = stage_of(account, now=now, config=config, lead_days=lead)
        counts[stage] += 1
        if stage in rows:
            rows[stage].append(account)
    domains = Domain.objects.filter(status__in=(DomainStatus.ACTIVE, DomainStatus.EXPIRED)).select_related("client")
    for domain in domains.order_by("expires_at"):
        stage = stage_of(domain, now=now, config=config, lead_days=lead)
        counts[stage] += 1
    return {"config": config, "counts": counts, "rows": rows,
            "pending_cancellations": CancellationRequest.objects.filter(status=CancellationStatus.PENDING).count(),
            "scheduled_cancellations": CancellationRequest.objects.filter(status=CancellationStatus.APPROVED).count(),
            "needing_attention": CancellationRequest.objects.filter(status=CancellationStatus.APPROVED
                                                                    ).exclude(last_error="").count()}


# --- Settings -------------------------------------------------------------------------------------------------

@transaction.atomic
def save_settings(actor, *, request=None, **values):
    if not actor.has_perm(perm("manage_settings")):
        raise _denied()
    row = LifecycleSettings.objects.select_for_update().get_or_create(pk=1)[0]
    fields = ("grace_after_days", "suspend_after_days", "terminate_after_days", "auto_suspend", "auto_terminate",
              "unsuspend_on_payment")
    before = {f: getattr(row, f) for f in fields}
    for name in fields:
        if name in values:
            setattr(row, name, values[name])
    row.full_clean()
    row.save()
    changed = {f: [before[f], getattr(row, f)] for f in fields if before[f] != getattr(row, f)}
    if changed:
        audit.record("lifecycle.settings_changed", actor=actor, target=row, metadata={"changes": changed},
                     request=request)
    return row


# --- Visibility -----------------------------------------------------------------------------------------------

def visible_requests_for_user(user):
    if not user.is_authenticated:
        return CancellationRequest.objects.none()
    queryset = CancellationRequest.objects.select_related("client", "hosting_account", "domain")
    if is_staff_reviewer(user):
        conditions = Q()
        if user.has_perm(perm("view_hosting")):
            conditions |= Q(hosting_account__isnull=False)
        if user.has_perm(perm("view_domains")):
            conditions |= Q(domain__isnull=False)
        return queryset.filter(conditions)
    return queryset.filter(client__contacts__user=user).distinct()


def can_request(user, service):
    """Staff who manage the service, or the *owner* of the client it belongs to (ending a service is not for every contact)."""
    if not (user and user.is_authenticated):
        return False
    return can_manage(user, service) or contact_role(user, service.client) == ContactRole.OWNER


def _require_request_access(actor, service):
    if not can_request(actor, service):
        raise _denied("Only the owner of the account can cancel a service.")


# --- Requesting ------------------------------------------------------------------------------------------------

def _clean_timing(service, timing):
    if timing not in Timing.values:
        raise ServiceError("Choose when the service should end.", code="timing_invalid")
    if isinstance(service, Domain) and timing != Timing.END_OF_TERM:
        raise ServiceError("A domain registration cannot be cancelled early; it is set not to renew and ends when "
                           "it expires.", code="timing_invalid")
    return timing


def _clean_reason(code, text):
    if code not in CancellationReason.values:
        raise ServiceError("Choose a reason.", code="reason_invalid")
    text = (text or "").strip()
    if code == CancellationReason.OTHER and not text:
        raise ServiceError("Please tell us the reason.", code="reason_required")
    if len(text) > 1000:
        raise ServiceError("Please keep the reason under 1000 characters.", code="reason_invalid")
    return text


def _lock(service):
    model = HostingAccount if isinstance(service, HostingAccount) else Domain
    return model.objects.select_for_update().get(pk=service.pk)


def is_live(service):
    if isinstance(service, HostingAccount):
        return service.status in (HostingStatus.ACTIVE, HostingStatus.SUSPENDED)
    return service.status == DomainStatus.ACTIVE


def request_cancellation(actor, service, *, reason_code, reason_text="", timing=Timing.END_OF_TERM, request=None):
    """
    Ask to cancel one service. The owner of the account (or staff on their behalf) may ask; a service that is not
    live, or that already has an open request, is refused.
    """
    _require_request_access(actor, service)
    timing = _clean_timing(service, timing)
    reason_text = _clean_reason(reason_code, reason_text)
    on_behalf = can_manage(actor, service) and contact_role(actor, service.client) != ContactRole.OWNER
    with transaction.atomic():
        service = _lock(service)
        if not is_live(service):
            raise ServiceError(f"{_name(service)} is {service.get_status_display().lower()} and cannot be "
                               "cancelled.", code="service_not_live")
        if open_request_for(service):
            raise ServiceError("There is already a cancellation request for this service.", code="already_requested")
        try:
            with transaction.atomic():
                cr = CancellationRequest.objects.create(
                    client=service.client, requested_by=actor, on_behalf=on_behalf, reason_code=reason_code,
                    reason_text=reason_text, timing=timing, term_expires_at=service.expires_at,
                    **{"hosting_account" if kind_of(service) == "hosting" else "domain": service})
        except IntegrityError:  # a second request slipped in between the check and the insert
            raise ServiceError("There is already a cancellation request for this service.", code="already_requested")
        audit.record("cancellation.requested", actor=actor, target=cr,
                     metadata={"service": _name(service), "kind": cr.kind, "timing": timing, "reason": reason_code,
                               "on_behalf": on_behalf, "client_id": service.client_id}, request=request)
    _tell_customer("cancellation.requested", cr, title=f"We received your request to cancel {_name(service)}")
    _tell_team(cr)
    return cr


def _customer_link(cr):
    return reverse("lifecycle_customer:detail", args=[cr.pk])


def _tell_customer(event, cr, *, title, **context):
    link = _customer_link(cr)
    notifications.dispatch_client(event, cr.client, title=title, link=link, body=cr.review_note,
                                  context={"cancellation": cr, "service": cr.service_name, "link": link, **context})


def _tell_team(cr):
    codename = "manage_hosting" if cr.kind == "hosting" else "manage_domains"
    link = reverse("lifecycle_staff:cancellation", args=[cr.pk])
    notifications.notify_team(
        "cancellation.requested_team", codename, exclude=cr.requested_by,
        title=f"Cancellation requested: {cr.service_name}",
        body=f"{cr.client.display_name} - {cr.get_reason_code_display()} ({cr.get_timing_display().lower()}).",
        link=link, context={"cancellation": cr, "link": link})


# --- Refund options -------------------------------------------------------------------------------------------

def _service_invoice_ids(service):
    """The invoices that paid for this service: the order it was bought on and every renewal/upgrade applied to it."""
    field = "hosting_account" if isinstance(service, HostingAccount) else "domain"
    ids = set(Invoice.objects.filter(order__items__in=OrderItem.objects.filter(**{field: service})
                                     ).values_list("pk", flat=True))
    ids |= set(ServiceChange.objects.filter(**{field: service}, status=ChangeStatus.APPLIED
                                            ).values_list("invoice_id", flat=True))
    return ids


def refundable_payments(service):
    """Succeeded payments toward this service that still have money to give back, newest first."""
    rows = []
    payments_qs = Transaction.objects.filter(invoice_id__in=_service_invoice_ids(service), type=TransactionType.PAYMENT,
                                             status=TransactionStatus.SUCCEEDED).select_related("invoice")
    for tx in payments_qs.order_by("-occurred_at", "-id"):
        available = payments.refundable_amount(tx)
        if available > 0:
            rows.append((tx, available))
    return rows


def suggested_refund(service, *, now=None):
    """The unused part of the paid term (ex tax), by the same proration rule as an upgrade credit."""
    if not isinstance(service, HostingAccount):
        return Decimal("0.00")
    return unused_credit(service.term_paid, service.term_start, service.expires_at, now=now).amount


def refund_options(cr):
    service = cr.service
    return {"suggested": suggested_refund(service), "payments": refundable_payments(service)}


def _clean_refund(actor, cr, service, amount, payment, timing):
    try:
        amount = Decimal(str(amount or 0)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValidationError("The refund must be a number.")
    if amount < 0:
        raise ValidationError("The refund cannot be negative.")
    if amount == 0:
        return amount, None
    if not actor.has_perm(perm("manage_billing")):
        raise _denied("Refunding money needs billing permission.")
    if timing != Timing.IMMEDIATE:
        raise ServiceError("A refund can only be given when the service ends immediately.", code="refund_not_allowed")
    if payment is None:
        raise ServiceError("Choose which payment to refund.", code="refund_payment_required")
    options = dict((tx.pk, available) for tx, available in refundable_payments(service))
    if payment.pk not in options:
        raise ServiceError("That payment cannot be refunded for this service.", code="refund_payment_invalid")
    if amount > options[payment.pk]:
        raise ServiceError(f"At most {payment.currency} {options[payment.pk]} can still be refunded on that payment.",
                           code="refund_exceeds_payment")
    return amount, payment


# --- Reviewing --------------------------------------------------------------------------------------------------

def _cancel_open_renewals(service, actor):
    """Final billing: an unpaid renewal invoice for a service that is ending must not stay open. Returns notes."""
    field = "hosting_account" if isinstance(service, HostingAccount) else "domain"
    notes = []
    for change in ServiceChange.objects.filter(**{field: service}, status=ChangeStatus.PENDING):
        invoice = Invoice.objects.select_for_update().get(pk=change.invoice_id)
        if invoice.status in (InvoiceStatus.CANCELLED, InvoiceStatus.PAID):
            continue
        if invoice.status not in (InvoiceStatus.UNPAID, InvoiceStatus.DRAFT):
            notes.append(f"Invoice {invoice.number or invoice.pk} is {invoice.get_status_display().lower()}; "
                         "review it in Billing.")
            continue
        try:
            with transaction.atomic():
                invoicing.cancel_invoice_internal(invoice, actor=SYSTEM,
                                                  reason=f"{_name(service)} is being cancelled.")
            notes.append(f"Cancelled unpaid invoice {invoice.number or invoice.pk}.")
        except ServiceError:
            notes.append(f"Invoice {invoice.number or invoice.pk} has a payment on it; review it in Billing.")
    return notes


def approve(actor, cr, *, timing=None, refund_amount=0, refund_payment=None, note="", request=None):
    """
    Staff: approve a request. Does the final billing now; then ends the service immediately or leaves it scheduled
    for the day its paid period ends. Returns the request (completed, or approved and scheduled).
    """
    service = cr.service
    _require_manage(actor, service)
    timing = _clean_timing(service, timing or cr.timing)
    amount, payment = _clean_refund(actor, cr, service, refund_amount, refund_payment, timing)
    note = (note or "").strip()[:1000]
    now = timezone.now()
    with transaction.atomic():
        cr = CancellationRequest.objects.select_for_update().get(pk=cr.pk)
        if cr.status != CancellationStatus.PENDING:
            raise ServiceError("This request has already been dealt with.", code="invalid_status")
        service = _lock(cr.service)
        if not is_live(service):
            raise ServiceError(f"{_name(service)} is {service.get_status_display().lower()}, so there is nothing "
                               "to cancel.", code="service_not_live")
        ends = now if timing == Timing.IMMEDIATE or not service.expires_at or service.expires_at <= now \
            else service.expires_at
        fields = {"timing": timing, "reviewed_by": actor, "reviewed_at": now, "review_note": note,
                  "effective_at": ends, "refund_amount": amount, "refund_payment": payment, "last_error": ""}
        if isinstance(service, Domain):
            fields["auto_renew_before"] = service.auto_renew
            if service.auto_renew:
                service.auto_renew = False
                service.save(update_fields=["auto_renew", "updated_at"])
        billing_notes = _cancel_open_renewals(service, actor)
        transitions.transition(cr, CancellationStatus.APPROVED, actor=actor, request=request, **fields)
        if billing_notes:
            audit.record("cancellation.final_billing", actor=actor, target=cr, metadata={"notes": billing_notes},
                         request=request)
    when = "today" if ends <= now else f"on {timezone.localtime(ends):%d %b %Y}"
    _tell_customer("cancellation.approved", cr, title=f"Your cancellation of {cr.service_name} was approved",
                   ends=ends, ends_text=when)
    if ends <= now:
        return execute(cr, actor=actor, request=request)
    return cr


@transaction.atomic
def reject(actor, cr, *, note, request=None):
    service = cr.service
    _require_manage(actor, service)
    note = (note or "").strip()
    if not note:
        raise ServiceError("Tell the customer why the request was declined.", code="note_required")
    cr = CancellationRequest.objects.select_for_update().get(pk=cr.pk)
    transitions.transition(cr, CancellationStatus.REJECTED, actor=actor, request=request, reviewed_by=actor,
                           reviewed_at=timezone.now(), review_note=note[:1000])
    _tell_customer("cancellation.rejected", cr, title=f"Your request to cancel {cr.service_name} was declined")
    return cr


def withdraw(actor, cr, *, request=None):
    """The owner (or staff) takes a request back, while it is waiting or scheduled and not yet being carried out."""
    _require_request_access(actor, cr.service)
    with transaction.atomic():
        cr = CancellationRequest.objects.select_for_update().get(pk=cr.pk)
        if cr.status == CancellationStatus.APPROVED and cr.claimed_at and \
                cr.claimed_at > timezone.now() - CLAIM_TIMEOUT:
            raise ServiceError("This is being carried out right now, so it can no longer be withdrawn.",
                               code="in_progress")
        service = _lock(cr.service)
        transitions.transition(cr, CancellationStatus.WITHDRAWN, actor=actor, request=request)
        if isinstance(service, Domain) and cr.auto_renew_before is not None:
            service.auto_renew = cr.auto_renew_before
            service.save(update_fields=["auto_renew", "updated_at"])
    return cr


# --- Carrying it out ---------------------------------------------------------------------------------------------

def _claim(cr, now):
    """Take the right to carry the request out, without holding a lock while the server is called."""
    stale = now - CLAIM_TIMEOUT
    return CancellationRequest.objects.filter(pk=cr.pk, status=CancellationStatus.APPROVED).filter(
        Q(claimed_at__isnull=True) | Q(claimed_at__lt=stale)).update(claimed_at=now)


def _release(cr, error):
    CancellationRequest.objects.filter(pk=cr.pk).update(claimed_at=None, last_error=error[:500],
                                                        updated_at=timezone.now())
    audit.record("cancellation.failed", target=cr, metadata={"error": error[:500], "service": cr.service_name})


def _end_service(cr, actor, request):
    service = cr.service
    if isinstance(service, HostingAccount):
        if service.status in (HostingStatus.ACTIVE, HostingStatus.SUSPENDED):
            hosting_services.terminate_account(actor, service, request=request)
        elif service.status not in (HostingStatus.TERMINATED, HostingStatus.CANCELLED):
            raise ServiceError(f"The account is {service.get_status_display().lower()} and cannot be ended.",
                               code="invalid_status")
    else:
        if service.status in (DomainStatus.ACTIVE, DomainStatus.EXPIRED):
            with transaction.atomic():
                domain = Domain.objects.select_for_update().get(pk=service.pk)
                domain.status, domain.auto_renew = DomainStatus.CANCELLED, False
                domain.save(update_fields=["status", "auto_renew", "updated_at"])
                audit.record("domain.cancelled", actor=actor, target=domain,
                             metadata={"reason": "Cancellation request", "request_id": cr.pk}, request=request)
        elif service.status != DomainStatus.CANCELLED:
            raise ServiceError(f"The domain is {service.get_status_display().lower()} and cannot be ended.",
                               code="invalid_status")


def _give_refund(cr, actor, request):
    """Refund what staff decided at approval. A failure is recorded on the request; the service has already ended."""
    if cr.refund_amount <= 0 or cr.refund_transaction_id:
        return cr
    try:
        refund = payments.refund_payment(actor, cr.refund_payment, amount=cr.refund_amount,
                                         reason=f"Cancellation {cr.code}", request=request)
    except (ServiceError, ValidationError) as exc:
        message = exc.message if isinstance(exc, ServiceError) else "; ".join(exc.messages)
        cr.refund_error = message[:500]
        audit.record("cancellation.refund_failed", actor=actor, target=cr, metadata={"error": cr.refund_error})
    else:
        cr.refund_transaction, cr.refund_error = refund, ""
    return cr


def execute(cr, *, actor=SYSTEM, request=None):
    """
    End the service an approved request is about. Idempotent and safe to run twice at once: it is claimed first, and a
    service that has already ended just completes the request. A failure is kept on the request (and raised to a
    person, logged for a job) and the request stays approved so it can be retried.
    """
    now = timezone.now()
    if not _claim(cr, now):
        return CancellationRequest.objects.get(pk=cr.pk)
    cr = CancellationRequest.objects.select_related("hosting_account", "domain", "client", "refund_payment"
                                                    ).get(pk=cr.pk)
    try:
        _end_service(cr, actor, request)
    except ServiceError as exc:
        _release(cr, exc.message)
        raise
    except Exception:  # noqa: BLE001 - keep the request retryable whatever broke
        logger.exception("Ending the service for cancellation %s failed", cr.pk)
        _release(cr, "An unexpected error occurred.")
        raise ServiceError("An unexpected error occurred while ending the service.", code="execution_failed")
    cr = _give_refund(cr, actor, request)
    with transaction.atomic():
        locked = CancellationRequest.objects.select_for_update().get(pk=cr.pk)
        transitions.transition(locked, CancellationStatus.COMPLETED, actor=actor, request=request,
                               completed_at=timezone.now(), claimed_at=None, last_error="",
                               refund_transaction=cr.refund_transaction, refund_error=cr.refund_error)
    if locked.kind == "domain":  # a terminated hosting account already sent its own "terminated" email
        _tell_customer("cancellation.completed", locked, title=f"{locked.service_name} has been cancelled")
    return locked


def retry(actor, cr, *, request=None):
    """Staff: try again to carry out an approved request that failed."""
    _require_manage(actor, cr.service)
    if cr.status != CancellationStatus.APPROVED:
        raise ServiceError("Only an approved request can be retried.", code="invalid_status")
    return execute(cr, actor=actor, request=request)


def run_due_cancellations(*, now=None):
    """Daily: carry out approved requests whose day has come (end-of-term cancellations, and retries)."""
    now = now or timezone.now()
    result = {"completed": 0, "errors": []}
    due = CancellationRequest.objects.filter(status=CancellationStatus.APPROVED, effective_at__lte=now)
    for cr in due:
        try:
            if execute(cr).status == CancellationStatus.COMPLETED:
                result["completed"] += 1
        except ServiceError as exc:
            result["errors"].append(f"{cr.code}: {exc.message}")
    return result


# --- The unpaid-service lifecycle ---------------------------------------------------------------------------------

def _payment_awaiting_confirmation(account):
    """A payment the customer has reported but staff have not confirmed yet: do not punish them for waiting."""
    return ServiceChange.objects.filter(hosting_account=account, status=ChangeStatus.PENDING,
                                        invoice__transactions__status=TransactionStatus.PENDING).exists()


def _send_grace_notice(account, config):
    _, created = LifecycleNotice.objects.get_or_create(hosting_account=account, kind="grace",
                                                       term_expires_at=account.expires_at)
    if not created:
        return False
    when = timeline(account, config=config)
    link = reverse("hosting_customer:detail", args=[account.pk])
    notifications.dispatch_client(
        "service.grace_notice", account.client, link=link,
        title=f"Final notice: {account.domain} expired on {timezone.localtime(account.expires_at):%d %b %Y}",
        body="Renew now to keep it running.",
        context={"account": account, "link": link, "suspend_on": when["suspend"], "expired_on": account.expires_at})
    return True


def run_lifecycle(*, now=None, dry_run=False):
    """
    The daily sweep: carry out due cancellations, send final notices, suspend accounts that stayed unpaid past the
    grace period, and (only if switched on) terminate those that stayed suspended. One failure never stops the rest.
    ``dry_run`` reports what would happen and changes nothing.
    """
    now = now or timezone.now()
    config = LifecycleSettings.load()
    result = {"cancelled": 0, "notices": 0, "suspended": 0, "terminated": 0, "errors": [], "dry_run": dry_run}
    if not dry_run:
        due = run_due_cancellations(now=now)
        result["cancelled"] += due["completed"]
        result["errors"].extend(due["errors"])

    lapsed = HostingAccount.objects.filter(status=HostingStatus.ACTIVE, expires_at__isnull=False,
                                           expires_at__lte=now).exclude(billing_cycle="").select_related("client")
    for account in lapsed:
        days_past = (now - account.expires_at).days
        try:
            if days_past >= config.grace_after_days and not _payment_awaiting_confirmation(account):
                if dry_run:
                    result["notices"] += not LifecycleNotice.objects.filter(
                        hosting_account=account, kind="grace", term_expires_at=account.expires_at).exists()
                elif _send_grace_notice(account, config):
                    result["notices"] += 1
            if (config.auto_suspend and days_past >= config.suspend_after_days
                    and not _payment_awaiting_confirmation(account)):
                if not dry_run:
                    hosting_services.suspend_account(
                        SYSTEM, account, for_nonpayment=True,
                        reason=f"Renewal unpaid: expired {timezone.localtime(account.expires_at):%d %b %Y}.")
                result["suspended"] += 1
        except ServiceError as exc:
            result["errors"].append(f"hosting {account.pk}: {exc.message}")
        except Exception:  # noqa: BLE001
            logger.exception("Lifecycle step for hosting account %s failed", account.pk)
            result["errors"].append(f"hosting {account.pk}: unexpected error")

    if config.auto_terminate:
        stale = HostingAccount.objects.filter(
            status=HostingStatus.SUSPENDED, suspended_for_nonpayment=True, expires_at__isnull=False,
            expires_at__lte=now - timedelta(days=config.terminate_after_days))
        for account in stale:
            try:
                if not dry_run:
                    hosting_services.terminate_account(SYSTEM, account)
                result["terminated"] += 1
            except ServiceError as exc:
                result["errors"].append(f"hosting {account.pk}: {exc.message}")
            except Exception:  # noqa: BLE001
                logger.exception("Terminating hosting account %s failed", account.pk)
                result["errors"].append(f"hosting {account.pk}: unexpected error")
    return result


def lift_suspension_after_payment(service):
    """A renewal was paid: a suspension we made for non-payment is lifted (never one staff made for another reason)."""
    if not isinstance(service, HostingAccount):
        return False
    account = HostingAccount.objects.get(pk=service.pk)
    config = LifecycleSettings.load()
    if not (config.unsuspend_on_payment and account.status == HostingStatus.SUSPENDED
            and account.suspended_for_nonpayment and account.expires_at and account.expires_at > timezone.now()):
        return False
    try:
        hosting_services.unsuspend_account(SYSTEM, account)
    except ServiceError as exc:  # the payment stands; staff can unsuspend by hand
        logger.warning("Could not lift the suspension of hosting account %s: %s", account.pk, exc.message)
        audit.record("hosting.unsuspend_failed", target=account, metadata={"error": exc.message, "after": "payment"})
        return False
    return True

