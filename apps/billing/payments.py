"""
Payments, refunds and gateway webhooks.

The single source of truth for what has been paid is the set of *succeeded*
``Transaction`` rows. ``recompute_invoice`` rebuilds an invoice's paid/refunded
amounts and status from them, always under the invoice's row lock, so no
sequence of payments, refunds or redelivered webhooks can leave the two out of
step. Every function locks in the same order (invoice first, then transaction).
"""
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import ServiceError
from apps.notifications import services as notifications

from . import calculations as calc
from .gateways import PaymentError, get_adapter
from .invoicing import MAX_AMOUNT, _own_clients, _require_client_access, _require_manage, is_staff_biller
from .models import (OPEN_STATUSES, Invoice, InvoiceStatus, PaymentMethod, Transaction, TransactionStatus,
                     TransactionType, WebhookEvent, WebhookStatus)
from .signals import invoice_paid, invoice_refunded


def visible_transactions_for_user(user):
    queryset = Transaction.objects.select_related("invoice", "client", "payment_method")
    if is_staff_biller(user):
        return queryset
    if user and user.is_authenticated:
        return queryset.filter(client_id__in=_own_clients(user))
    return queryset.none()


def _lock_invoice(pk):
    # Plain row lock only (no select_related): PostgreSQL rejects FOR UPDATE across a nullable join.
    return Invoice.objects.select_for_update().get(pk=pk)


def _amount(value, label="Amount"):
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise ValidationError(f"{label} must be a number.")
    if not amount.is_finite() or amount <= 0:
        raise ValidationError(f"{label} must be greater than zero.")
    if amount > MAX_AMOUNT or calc.money(amount) != amount:
        raise ValidationError(f"{label} must be a valid amount with at most two decimal places.")
    return calc.money(amount)


def _require_open(invoice):
    if invoice.status not in OPEN_STATUSES or invoice.balance_due <= 0:
        raise ServiceError("This invoice has nothing left to pay.", code="invoice_not_payable")


def _require_amount_within_balance(invoice, amount):
    if amount > invoice.balance_due:
        raise ServiceError(f"The amount cannot be more than the balance due ({invoice.currency} "
                           f"{invoice.balance_due}).", code="amount_exceeds_balance")


# --- Deriving the invoice position from its transactions ----------------------------------------

def recompute_invoice(invoice, *, actor=None):
    """
    Rebuild ``amount_paid`` / ``amount_refunded`` / ``status`` from the succeeded
    transactions. The caller holds the invoice's row lock. Sends ``invoice_paid``
    when the invoice becomes fully paid. A cancelled invoice keeps its status
    even if money arrives for it (the money is recorded, and flagged by
    ``Invoice.overpaid``).
    """
    sums = {row["type"]: row["total"] for row in invoice.transactions.filter(
        status=TransactionStatus.SUCCEEDED).values("type").annotate(total=Sum("amount"))}
    paid = sums.get(TransactionType.PAYMENT) or calc.ZERO
    refunded = sums.get(TransactionType.REFUND) or calc.ZERO
    previous, previous_refunded = invoice.status, invoice.amount_refunded
    invoice.amount_paid, invoice.amount_refunded = paid, refunded
    if previous not in (InvoiceStatus.DRAFT, InvoiceStatus.CANCELLED):
        if paid > 0 and refunded >= paid:
            invoice.status = InvoiceStatus.REFUNDED
        elif paid >= invoice.total:
            invoice.status = InvoiceStatus.PAID
        elif paid > 0:
            invoice.status = InvoiceStatus.PARTIALLY_PAID
        else:
            invoice.status = InvoiceStatus.UNPAID
    if invoice.status in (InvoiceStatus.PAID, InvoiceStatus.REFUNDED):
        invoice.paid_at = invoice.paid_at or timezone.now()
    else:
        invoice.paid_at = None
    invoice.save(update_fields=["amount_paid", "amount_refunded", "status", "paid_at", "updated_at"])
    if invoice.status == InvoiceStatus.PAID and previous in OPEN_STATUSES:
        invoice_paid.send(sender=Invoice, invoice=invoice, actor=actor)
    if refunded != previous_refunded:
        invoice_refunded.send(sender=Invoice, invoice=invoice, actor=actor)
    return invoice


def fail_pending_transactions(invoice, reason):
    invoice.transactions.filter(status=TransactionStatus.PENDING).update(
        status=TransactionStatus.FAILED, failure_reason=reason[:500], updated_at=timezone.now())


def _create_transaction(**fields):
    """Validate, then write. The unique idempotency/gateway-id constraints turn a race into a clean error."""
    tx = Transaction(**fields)
    tx.full_clean()
    try:
        with transaction.atomic():
            tx.save()
    except IntegrityError:
        raise ServiceError("A transaction with this reference already exists.", code="duplicate_transaction",
                           status_code=409)
    return tx


def _tell_customer(event, invoice, **context):
    notifications.dispatch(
        event, email=invoice.billing_email or invoice.client.email,
        context={"invoice": invoice, "link": reverse("billing_customer:invoice_detail", args=[invoice.pk]),
                 **context})


def _notify_payment(invoice, tx):
    _tell_customer("payment.received", invoice, payment=tx)


def _audit_payment(action, actor, tx, invoice, request=None, **extra):
    audit.record(action, actor=actor, target=tx,
                 metadata={"invoice_id": invoice.pk, "amount": str(tx.amount), "currency": tx.currency,
                           "method": tx.method_name, "invoice_status": invoice.status,
                           "overpaid": str(invoice.overpaid) if invoice.overpaid else "", **extra},
                 request=request)


# --- Manual payments -------------------------------------------------------------------------------

@transaction.atomic
def record_payment(actor, invoice, *, amount, method=None, reference="", note="", occurred_at=None,
                   idempotency_key="", notify=True, request=None):
    """
    Staff: record money received against an invoice (bank transfer, cash, ...).
    Safe to retry with the same ``idempotency_key``: a repeat returns the
    original transaction instead of recording the payment twice.
    """
    _require_manage(actor)
    amount = _amount(amount)
    if occurred_at is not None and occurred_at > timezone.now() + timedelta(minutes=5):
        raise ServiceError("The date received cannot be in the future.", code="payment_date_invalid")
    invoice = _lock_invoice(invoice.pk)
    key = (idempotency_key or "").strip()[:100]
    if key:
        existing = Transaction.objects.filter(idempotency_key=key).first()
        if existing is not None:
            if existing.invoice_id != invoice.pk or existing.amount != amount:
                raise ServiceError("This idempotency key was already used for a different payment.",
                                   code="idempotency_conflict", status_code=409)
            return existing
    _require_open(invoice)
    _require_amount_within_balance(invoice, amount)
    tx = _create_transaction(
        invoice=invoice, client_id=invoice.client_id, type=TransactionType.PAYMENT,
        status=TransactionStatus.SUCCEEDED, amount=amount, currency=invoice.currency, payment_method=method,
        method_name=(method.name if method else "Manual")[:100], reference=reference.strip()[:200],
        note=note.strip()[:500], idempotency_key=key, recorded_by=actor,
        occurred_at=occurred_at or timezone.now())
    recompute_invoice(invoice, actor=actor)
    _audit_payment("payment.recorded", actor, tx, invoice, request)
    if notify:
        _notify_payment(invoice, tx)
    return tx


@transaction.atomic
def report_payment(actor, invoice, *, method, amount=None, reference="", note="", request=None):
    """
    Customer: say "I have paid this by <offline method>". Creates a *pending*
    transaction that changes nothing until staff confirm it.
    """
    invoice = _lock_invoice(invoice.pk)
    _require_client_access(actor, invoice.client)
    _require_open(invoice)
    if method is None or not method.is_active or method.provider_id:
        raise ServiceError("Choose a valid payment method.", code="payment_method_invalid")
    amount = _amount(amount) if amount not in (None, "") else invoice.balance_due
    _require_amount_within_balance(invoice, amount)
    if invoice.transactions.filter(status=TransactionStatus.PENDING, provider__isnull=True,
                                   type=TransactionType.PAYMENT).exists():
        raise ServiceError("You have already reported a payment for this invoice. It is awaiting confirmation.",
                           code="payment_already_reported")
    tx = _create_transaction(
        invoice=invoice, client_id=invoice.client_id, type=TransactionType.PAYMENT,
        status=TransactionStatus.PENDING, amount=amount, currency=invoice.currency, payment_method=method,
        method_name=method.name[:100], reference=reference.strip()[:200], note=note.strip()[:500],
        recorded_by=actor)
    _audit_payment("payment.reported", actor, tx, invoice, request)
    notifications.notify_team(
        "payment.reported", "manage_billing", exclude=actor,
        title=f"{invoice.reference}: {invoice.client.display_name} reports a payment of {tx.currency} {tx.amount}",
        link=reverse("billing_staff:invoice_detail", args=[invoice.pk]))
    return tx


@transaction.atomic
def confirm_payment(actor, tx, *, request=None):
    """Staff: a reported (pending, offline) payment has arrived."""
    _require_manage(actor)
    invoice = _lock_invoice(tx.invoice_id)
    tx = Transaction.objects.select_for_update().get(pk=tx.pk)
    if tx.type != TransactionType.PAYMENT or tx.status != TransactionStatus.PENDING or tx.provider_id:
        raise ServiceError("Only a reported offline payment can be confirmed here.", code="invalid_status")
    _require_open(invoice)
    _require_amount_within_balance(invoice, tx.amount)
    tx.status, tx.occurred_at = TransactionStatus.SUCCEEDED, timezone.now()
    tx.save(update_fields=["status", "occurred_at", "updated_at"])
    recompute_invoice(invoice, actor=actor)
    _audit_payment("payment.confirmed", actor, tx, invoice, request)
    _notify_payment(invoice, tx)
    return tx


@transaction.atomic
def reject_payment(actor, tx, *, reason="", request=None):
    """Staff: a reported payment never arrived."""
    _require_manage(actor)
    invoice = _lock_invoice(tx.invoice_id)
    tx = Transaction.objects.select_for_update().get(pk=tx.pk)
    if tx.type != TransactionType.PAYMENT or tx.status != TransactionStatus.PENDING or tx.provider_id:
        raise ServiceError("Only a reported offline payment can be rejected here.", code="invalid_status")
    tx.status, tx.failure_reason = TransactionStatus.FAILED, reason.strip()[:500]
    tx.save(update_fields=["status", "failure_reason", "updated_at"])
    _audit_payment("payment.rejected", actor, tx, invoice, request, reason=tx.failure_reason)
    _tell_customer("payment.rejected", invoice, payment=tx)
    return tx


# --- Refunds -------------------------------------------------------------------------------------

def refundable_amount(payment):
    refunded = payment.refunds.filter(status=TransactionStatus.SUCCEEDED).aggregate(t=Sum("amount"))["t"]
    return payment.amount - (refunded or calc.ZERO)


@transaction.atomic
def refund_payment(actor, payment, *, amount=None, reason="", request=None):
    """Staff: refund all or part of a succeeded payment (gateway payments are refunded through the gateway)."""
    _require_manage(actor)
    invoice = _lock_invoice(payment.invoice_id)
    payment = Transaction.objects.select_for_update().get(pk=payment.pk)
    if payment.type != TransactionType.PAYMENT or payment.status != TransactionStatus.SUCCEEDED:
        raise ServiceError("Only a succeeded payment can be refunded.", code="invalid_status")
    available = refundable_amount(payment)
    amount = _amount(amount) if amount not in (None, "") else available
    if amount > available:
        raise ServiceError(f"At most {payment.currency} {available} can still be refunded on this payment.",
                           code="refund_exceeds_payment")
    external_id = ""
    if payment.provider_id:
        try:
            external_id = get_adapter(payment.provider).refund(
                external_id=payment.external_id, amount=amount, currency=payment.currency)
        except PaymentError as exc:
            raise ServiceError(f"The gateway could not refund this payment: {exc}", code="refund_failed")
    refund = _create_transaction(
        invoice=invoice, client_id=invoice.client_id, type=TransactionType.REFUND,
        status=TransactionStatus.SUCCEEDED, amount=amount, currency=payment.currency,
        payment_method=payment.payment_method, method_name=payment.method_name, provider=payment.provider,
        external_id=external_id, parent=payment, note=reason.strip()[:500], recorded_by=actor)
    recompute_invoice(invoice, actor=actor)
    _audit_payment("payment.refunded", actor, refund, invoice, request, parent_id=payment.pk)
    _tell_customer("payment.refunded", invoice, refund=refund)
    return refund


# --- Gateway payments ----------------------------------------------------------------------------

@transaction.atomic
def start_gateway_payment(actor, invoice, method, *, request=None):
    """
    Customer: pay the balance online. Creates a pending transaction and returns
    ``(transaction, redirect_url)``; the transaction only succeeds when the
    gateway's signed webhook says so - never because the browser came back.
    """
    invoice = _lock_invoice(invoice.pk)
    _require_client_access(actor, invoice.client)
    _require_open(invoice)
    provider = method.provider if method and method.is_active else None
    if provider is None or not provider.is_active:
        raise ServiceError("Online payment is not available with this method.", code="payment_method_invalid")
    try:
        adapter = get_adapter(provider)
    except PaymentError as exc:
        raise ServiceError(str(exc), code="gateway_unavailable")
    balance = invoice.balance_due
    existing = invoice.transactions.filter(
        status=TransactionStatus.PENDING, type=TransactionType.PAYMENT, provider=provider, amount=balance).first()
    if existing is not None:
        return existing, adapter.checkout_url(existing.external_id)
    try:
        session = adapter.create_payment(reference=invoice.reference, amount=balance, currency=invoice.currency,
                                         return_url=reverse("billing_customer:invoice_detail", args=[invoice.pk]))
    except PaymentError as exc:
        raise ServiceError(f"The payment could not be started: {exc}", code="gateway_error")
    tx = _create_transaction(
        invoice=invoice, client_id=invoice.client_id, type=TransactionType.PAYMENT,
        status=TransactionStatus.PENDING, amount=balance, currency=invoice.currency, payment_method=method,
        method_name=method.name[:100], provider=provider, external_id=session.external_id, recorded_by=actor)
    _audit_payment("payment.started", actor, tx, invoice, request, provider_id=provider.pk)
    return tx, session.redirect_url


@dataclass
class WebhookResult:
    status: str  # "processed" | "ignored" | "failed" | "duplicate"
    detail: str = ""


def _apply_event(provider, event):
    """Apply one verified event; returns (WebhookStatus, detail). Unexpected errors propagate so the gateway retries."""
    if event.type not in ("payment.succeeded", "payment.failed"):
        return WebhookStatus.IGNORED, "Unhandled event type."
    tx = Transaction.objects.filter(provider=provider, external_id=event.external_id,
                                    type=TransactionType.PAYMENT).first() if event.external_id else None
    if tx is None:
        return WebhookStatus.IGNORED, "No matching transaction."
    invoice = _lock_invoice(tx.invoice_id)
    tx = Transaction.objects.select_for_update().get(pk=tx.pk)

    if event.type == "payment.failed":
        if tx.status != TransactionStatus.PENDING:
            return WebhookStatus.IGNORED, "The transaction is no longer pending."
        tx.status, tx.failure_reason = TransactionStatus.FAILED, (event.reason or "Payment failed.")[:500]
        tx.save(update_fields=["status", "failure_reason", "updated_at"])
        _audit_payment("payment.failed", None, tx, invoice)
        _tell_customer("payment.failed", invoice, payment=tx)
        return WebhookStatus.PROCESSED, "Payment marked failed."

    if tx.status == TransactionStatus.SUCCEEDED:
        return WebhookStatus.IGNORED, "Already applied."
    if event.amount is None or calc.money(event.amount) != tx.amount or event.currency != tx.currency:
        audit.record("payment.webhook_mismatch", target=tx,
                     metadata={"invoice_id": invoice.pk, "expected": f"{tx.amount} {tx.currency}",
                               "received": f"{event.amount} {event.currency}"})
        return WebhookStatus.FAILED, "Amount or currency does not match the transaction."
    # A payment the gateway confirms is real money even if we had marked it failed or the invoice was
    # cancelled meanwhile, so it is recorded truthfully (and flagged) rather than dropped.
    tx.status, tx.failure_reason, tx.occurred_at = TransactionStatus.SUCCEEDED, "", timezone.now()
    tx.save(update_fields=["status", "failure_reason", "occurred_at", "updated_at"])
    recompute_invoice(invoice)
    _audit_payment("payment.succeeded", None, tx, invoice, invoice_cancelled=invoice.status == InvoiceStatus.CANCELLED)
    if invoice.status != InvoiceStatus.CANCELLED:
        _notify_payment(invoice, tx)
    return WebhookStatus.PROCESSED, "Payment applied."


def handle_webhook(provider, body, headers):
    """
    Verify and apply a gateway webhook. Raises ``WebhookVerificationError`` for an
    inauthentic request (nothing is recorded). A verified event is recorded by
    the gateway's event id first, so a redelivery is a no-op; if applying it
    fails unexpectedly everything rolls back (including the event record) and the
    gateway's retry gets another go.
    """
    try:
        adapter = get_adapter(provider)
    except PaymentError as exc:
        raise ServiceError(str(exc), code="gateway_unavailable", status_code=503)
    event = adapter.parse_webhook(body, headers)
    with transaction.atomic():
        try:
            with transaction.atomic():
                record = WebhookEvent.objects.create(provider=provider, event_id=event.event_id,
                                                     event_type=event.type)
        except IntegrityError:
            return WebhookResult("duplicate", "This event was already received.")
        status, detail = _apply_event(provider, event)
        record.status, record.detail = status, detail[:500]
        record.save(update_fields=["status", "detail", "updated_at"])
    return WebhookResult(status, detail)


def active_payment_methods_for(invoice):
    """Methods a customer can pay ``invoice`` with: (offline methods, online methods)."""
    methods = PaymentMethod.objects.filter(is_active=True).select_related("provider")
    online = [m for m in methods if m.provider_id and m.provider.is_active]
    offline = [m for m in methods if not m.provider_id]
    return offline, online

