"""Payments, refunds, the reported-payment flow and gateway webhooks."""
import json
import time
from decimal import Decimal

import pytest
from django.core import mail

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.billing import gateways, integrity, invoicing, payments
from apps.billing.models import (Invoice, InvoiceStatus, Transaction, TransactionStatus, TransactionType,
                                 WebhookEvent)
from apps.core.exceptions import ServiceError

from .conftest import WEBHOOK_SECRET, lines, signed_event

pytestmark = pytest.mark.django_db

D = Decimal


@pytest.fixture
def invoice(manager, client_obj):
    """An issued 110.00 invoice (100.00 + 10% tax)."""
    return invoicing.issue_invoice(manager, invoicing.create_invoice(
        manager, client_obj, lines=lines(("Hosting", 1, "100.00"))))


@pytest.fixture
def taxed_invoice(vat, manager, client_obj):
    return invoicing.issue_invoice(manager, invoicing.create_invoice(
        manager, client_obj, lines=lines(("Hosting", 1, "100.00"))))


def reload(invoice):
    invoice.refresh_from_db()
    return invoice


# --- Recording payments -----------------------------------------------------------------------

def test_partial_then_full_payment(manager, invoice):
    payments.record_payment(manager, invoice, amount="40.00", reference="TT-1")
    reload(invoice)
    assert invoice.status == InvoiceStatus.PARTIALLY_PAID and invoice.balance_due == D("60.00")
    assert invoice.paid_at is None
    payments.record_payment(manager, invoice, amount="60.00")
    reload(invoice)
    assert invoice.status == InvoiceStatus.PAID and invoice.balance_due == 0 and invoice.paid_at
    assert invoice.amount_paid == D("100.00")
    assert integrity.verify_all() == []
    assert AuditEvent.objects.filter(action="payment.recorded").count() == 2


def test_a_payment_cannot_exceed_the_balance_or_be_non_positive(manager, invoice):
    for bad, code in (("100.01", "amount_exceeds_balance"),):
        with pytest.raises(ServiceError) as exc:
            payments.record_payment(manager, invoice, amount=bad)
        assert exc.value.code == code
    for bad in ("0", "-5", "abc", "NaN", "1.234", "99999999999"):
        with pytest.raises(Exception) as exc:
            payments.record_payment(manager, invoice, amount=bad)
        assert exc.type.__name__ == "ValidationError", bad
    assert not Transaction.objects.exists()


def test_only_billing_managers_can_record_payments(customer, staff, invoice):
    for actor in (customer, staff(Role.SUPPORT_AGENT)):
        with pytest.raises(ServiceError) as exc:
            payments.record_payment(actor, invoice, amount="1.00")
        assert exc.value.code == "permission_denied"


def test_payment_needs_an_open_invoice(manager, client_obj):
    draft = invoicing.create_invoice(manager, client_obj, lines=lines(("x", 1, "10")))
    with pytest.raises(ServiceError) as exc:
        payments.record_payment(manager, draft, amount="5")
    assert exc.value.code == "invoice_not_payable"
    issued = invoicing.issue_invoice(manager, draft)
    payments.record_payment(manager, issued, amount="10")
    with pytest.raises(ServiceError):
        payments.record_payment(manager, issued, amount="1")  # already paid


def test_the_same_idempotency_key_records_the_payment_once(manager, invoice):
    first = payments.record_payment(manager, invoice, amount="30.00", idempotency_key="abc-123")
    again = payments.record_payment(manager, invoice, amount="30.00", idempotency_key="abc-123")
    assert first.pk == again.pk and Transaction.objects.count() == 1
    assert reload(invoice).amount_paid == D("30.00")
    with pytest.raises(ServiceError) as exc:  # same key, different amount: refuse rather than guess
        payments.record_payment(manager, invoice, amount="31.00", idempotency_key="abc-123")
    assert exc.value.code == "idempotency_conflict" and exc.value.status_code == 409


def test_recording_sends_a_receipt(manager, invoice):
    mail.outbox.clear()
    payments.record_payment(manager, invoice, amount="100.00")
    assert len(mail.outbox) == 1 and "Payment received" in mail.outbox[0].subject
    assert "paid in full" in mail.outbox[0].body


def test_a_paid_invoice_marks_its_order_paid_through_the_signal(manager, invoice):
    received = []
    from apps.billing.signals import invoice_paid

    invoice_paid.connect(lambda sender, invoice, **kw: received.append(invoice.pk), weak=False,
                         dispatch_uid="test-recv")
    try:
        payments.record_payment(manager, invoice, amount="50.00")
        assert received == []
        payments.record_payment(manager, invoice, amount="50.00")
        assert received == [invoice.pk]
    finally:
        invoice_paid.disconnect(dispatch_uid="test-recv")


# --- Reported (offline) payments ----------------------------------------------------------------

def test_customer_reports_then_staff_confirm(manager, owner, invoice, bank):
    tx = payments.report_payment(owner, invoice, method=bank, reference="TT-99")
    assert tx.status == TransactionStatus.PENDING and tx.amount == D("100.00")
    assert reload(invoice).status == InvoiceStatus.UNPAID  # a claim changes nothing
    payments.confirm_payment(manager, tx)
    tx.refresh_from_db()
    assert tx.status == TransactionStatus.SUCCEEDED
    assert reload(invoice).status == InvoiceStatus.PAID
    assert integrity.verify_all() == []


def test_staff_can_reject_a_reported_payment(manager, owner, invoice, bank):
    tx = payments.report_payment(owner, invoice, method=bank, amount="20.00")
    payments.reject_payment(manager, tx, reason="Never arrived")
    tx.refresh_from_db()
    assert tx.status == TransactionStatus.FAILED and tx.failure_reason == "Never arrived"
    assert reload(invoice).amount_paid == 0
    with pytest.raises(ServiceError):
        payments.confirm_payment(manager, tx)  # a decided report cannot be confirmed later


def test_only_one_reported_payment_may_be_pending(owner, invoice, bank):
    payments.report_payment(owner, invoice, method=bank)
    with pytest.raises(ServiceError) as exc:
        payments.report_payment(owner, invoice, method=bank)
    assert exc.value.code == "payment_already_reported"


def test_reporting_is_limited_to_contacts_and_offline_methods(customer, owner, invoice, bank, card):
    with pytest.raises(ServiceError) as exc:
        payments.report_payment(customer, invoice, method=bank)
    assert exc.value.code == "permission_denied"
    with pytest.raises(ServiceError) as exc:
        payments.report_payment(owner, invoice, method=card)  # an online method must go through the gateway
    assert exc.value.code == "payment_method_invalid"
    with pytest.raises(ServiceError):
        payments.report_payment(owner, invoice, method=None)
    with pytest.raises(ServiceError):
        payments.report_payment(owner, invoice, method=bank, amount="500.00")


def test_confirming_after_the_invoice_was_settled_is_refused(manager, owner, invoice, bank):
    tx = payments.report_payment(owner, invoice, method=bank)
    payments.record_payment(manager, invoice, amount="100.00")
    with pytest.raises(ServiceError):
        payments.confirm_payment(manager, tx)
    assert reload(invoice).amount_paid == D("100.00")


# --- Refunds --------------------------------------------------------------------------------------

def test_partial_and_full_refunds(manager, invoice):
    payment = payments.record_payment(manager, invoice, amount="100.00")
    part = payments.refund_payment(manager, payment, amount="30.00", reason="Goodwill")
    assert part.type == TransactionType.REFUND and part.parent_id == payment.pk
    reload(invoice)
    assert invoice.status == InvoiceStatus.PAID and invoice.amount_refunded == D("30.00")
    assert invoice.balance_due == 0  # a refund never reopens the invoice
    assert payments.refundable_amount(payment) == D("70.00")
    payments.refund_payment(manager, payment)  # the rest
    reload(invoice)
    assert invoice.status == InvoiceStatus.REFUNDED and invoice.amount_refunded == D("100.00")
    assert integrity.verify_all() == []
    with pytest.raises(ServiceError) as exc:
        payments.refund_payment(manager, payment, amount="0.01")
    assert exc.value.code == "refund_exceeds_payment"


def test_only_a_succeeded_payment_can_be_refunded(manager, owner, invoice, bank):
    tx = payments.report_payment(owner, invoice, method=bank)
    with pytest.raises(ServiceError):
        payments.refund_payment(manager, tx)
    with pytest.raises(ServiceError):
        payments.refund_payment(owner, tx)


# --- Gateway --------------------------------------------------------------------------------------

def start(owner, invoice, card):
    tx, url = payments.start_gateway_payment(owner, invoice, card)
    return tx, url


def test_start_creates_a_pending_transaction_and_reuses_it(owner, invoice, card, provider):
    tx, url = start(owner, invoice, card)
    assert tx.status == TransactionStatus.PENDING and tx.provider == provider and tx.amount == D("100.00")
    assert tx.external_id.startswith("tgw_") and tx.external_id in url
    again, url2 = start(owner, invoice, card)
    assert again.pk == tx.pk and url2 == url  # a second click resumes the same payment
    assert Transaction.objects.count() == 1


def test_start_is_limited_to_contacts_and_online_methods(customer, owner, invoice, card, bank):
    with pytest.raises(ServiceError) as exc:
        payments.start_gateway_payment(customer, invoice, card)
    assert exc.value.code == "permission_denied"
    with pytest.raises(ServiceError) as exc:
        payments.start_gateway_payment(owner, invoice, bank)
    assert exc.value.code == "payment_method_invalid"


def test_a_signed_success_webhook_pays_the_invoice(owner, invoice, card, provider):
    tx, _ = start(owner, invoice, card)
    body, headers = signed_event(provider, "evt_1", "payment.succeeded", tx.external_id, "100.00")
    result = payments.handle_webhook(provider, body, headers)
    assert result.status == "processed"
    tx.refresh_from_db()
    assert tx.status == TransactionStatus.SUCCEEDED
    assert reload(invoice).status == InvoiceStatus.PAID
    assert WebhookEvent.objects.get(event_id="evt_1").status == "processed"
    assert integrity.verify_all() == []


def test_a_redelivered_webhook_is_a_no_op(owner, invoice, card, provider):
    tx, _ = start(owner, invoice, card)
    body, headers = signed_event(provider, "evt_dup", "payment.succeeded", tx.external_id, "100.00")
    payments.handle_webhook(provider, body, headers)
    mail.outbox.clear()
    again = payments.handle_webhook(provider, body, headers)
    assert again.status == "duplicate"
    assert reload(invoice).amount_paid == D("100.00") and Transaction.objects.count() == 1
    assert WebhookEvent.objects.count() == 1 and mail.outbox == []


def test_a_new_event_for_an_already_applied_payment_is_ignored(owner, invoice, card, provider):
    tx, _ = start(owner, invoice, card)
    payments.handle_webhook(provider, *signed_event(provider, "evt_a", "payment.succeeded", tx.external_id, "100.00"))
    result = payments.handle_webhook(
        provider, *signed_event(provider, "evt_b", "payment.succeeded", tx.external_id, "100.00"))
    assert result.status == "ignored"
    assert reload(invoice).amount_paid == D("100.00")


@pytest.mark.parametrize("mutate", ["body", "secret", "missing", "garbage", "stale"])
def test_an_inauthentic_webhook_is_rejected_and_changes_nothing(owner, invoice, card, provider, mutate):
    tx, _ = start(owner, invoice, card)
    body, headers = signed_event(provider, "evt_x", "payment.succeeded", tx.external_id, "100.00")
    if mutate == "body":
        body = body.replace(b"100.00", b"1.00")
    elif mutate == "secret":
        provider.set_webhook_secret("another-secret")
        provider.save()
        body, headers = signed_event(provider, "evt_x", "payment.succeeded", tx.external_id, "100.00")
        provider.set_webhook_secret(WEBHOOK_SECRET)
        provider.save()
    elif mutate == "missing":
        headers = {}
    elif mutate == "garbage":
        headers = {gateways.SIGNATURE_HEADER: "t=abc,v1=zzz"}
    else:
        body, headers = signed_event(provider, "evt_x", "payment.succeeded", tx.external_id, "100.00",
                                     timestamp=int(time.time()) - 3600)
    with pytest.raises(gateways.WebhookVerificationError):
        payments.handle_webhook(provider, body, headers)
    tx.refresh_from_db()
    assert tx.status == TransactionStatus.PENDING and not WebhookEvent.objects.exists()
    assert reload(invoice).amount_paid == 0


def test_a_provider_without_a_secret_verifies_nothing(owner, invoice, card, provider):
    tx, _ = start(owner, invoice, card)
    body, headers = signed_event(provider, "evt_1", "payment.succeeded", tx.external_id, "100.00")
    provider.webhook_secret_encrypted = ""
    provider.save()
    with pytest.raises(gateways.WebhookVerificationError):
        payments.handle_webhook(provider, body, headers)


def test_an_amount_mismatch_is_not_applied(owner, invoice, card, provider):
    tx, _ = start(owner, invoice, card)
    result = payments.handle_webhook(
        provider, *signed_event(provider, "evt_m", "payment.succeeded", tx.external_id, "1.00"))
    assert result.status == "failed"
    tx.refresh_from_db()
    assert tx.status == TransactionStatus.PENDING and reload(invoice).amount_paid == 0
    assert AuditEvent.objects.filter(action="payment.webhook_mismatch").exists()
    wrong_currency = payments.handle_webhook(
        provider, *signed_event(provider, "evt_c", "payment.succeeded", tx.external_id, "100.00", currency="EUR"))
    assert wrong_currency.status == "failed"


def test_unknown_payments_and_event_types_are_acknowledged_but_ignored(provider):
    unknown = payments.handle_webhook(provider, *signed_event(provider, "e1", "payment.succeeded", "tgw_nope", "1"))
    other = payments.handle_webhook(provider, *signed_event(provider, "e2", "customer.created", "", "1"))
    assert (unknown.status, other.status) == ("ignored", "ignored")


def test_a_failed_payment_event_marks_the_attempt_failed(owner, invoice, card, provider):
    tx, _ = start(owner, invoice, card)
    payments.handle_webhook(provider, *signed_event(provider, "evt_f", "payment.failed", tx.external_id, "100.00"))
    tx.refresh_from_db()
    assert tx.status == TransactionStatus.FAILED and reload(invoice).status == InvoiceStatus.UNPAID
    # A new attempt is possible afterwards.
    retry, _ = start(owner, invoice, card)
    assert retry.pk != tx.pk and retry.status == TransactionStatus.PENDING


def test_money_that_arrives_after_cancellation_is_recorded_and_flagged(manager, owner, invoice, card, provider):
    tx, _ = start(owner, invoice, card)
    invoicing.cancel_invoice(manager, invoice, reason="Mistake")
    tx.refresh_from_db()
    assert tx.status == TransactionStatus.FAILED  # cancelling closed the open attempt
    payments.handle_webhook(provider, *signed_event(provider, "evt_late", "payment.succeeded", tx.external_id, "100.00"))
    tx.refresh_from_db()
    reload(invoice)
    assert tx.status == TransactionStatus.SUCCEEDED  # the gateway says it was paid, so we record it truthfully
    assert invoice.status == InvoiceStatus.CANCELLED and invoice.amount_paid == D("100.00")
    assert invoice.overpaid == D("100.00")
    assert integrity.verify_all() == []
    payments.refund_payment(manager, tx)  # and staff can give it back
    assert reload(invoice).overpaid == 0


def test_gateway_refunds_go_through_the_adapter(manager, owner, invoice, card, provider):
    tx, _ = start(owner, invoice, card)
    payments.handle_webhook(provider, *signed_event(provider, "evt_r", "payment.succeeded", tx.external_id, "100.00"))
    refund = payments.refund_payment(manager, tx, amount="25.00")
    assert refund.provider == provider and refund.external_id.startswith("trf_")
    assert reload(invoice).amount_refunded == D("25.00")


def test_the_test_gateway_is_unusable_when_disabled(settings, owner, invoice, card, provider):
    settings.ALLOW_TEST_PAYMENT_GATEWAY = False
    with pytest.raises(ServiceError) as exc:
        payments.start_gateway_payment(owner, invoice, card)
    assert exc.value.code == "gateway_unavailable"
    with pytest.raises(ServiceError) as exc:
        payments.handle_webhook(provider, b"{}", {})
    assert exc.value.code == "gateway_unavailable" and exc.value.status_code == 503
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        provider.full_clean()  # it cannot even be left active


def test_credentials_are_encrypted_at_rest(provider):
    provider.set_credentials({"api_key": "sk_live_secret"})
    provider.save()
    provider.refresh_from_db()
    assert "sk_live_secret" not in provider.credentials_encrypted
    assert WEBHOOK_SECRET not in provider.webhook_secret_encrypted
    assert provider.get_credentials() == {"api_key": "sk_live_secret"}
    assert provider.get_webhook_secret() == WEBHOOK_SECRET


def test_events_carry_no_secrets_in_the_audit_trail(owner, invoice, card, provider):
    tx, _ = start(owner, invoice, card)
    payments.handle_webhook(provider, *signed_event(provider, "evt_s", "payment.succeeded", tx.external_id, "100.00"))
    dump = json.dumps(list(AuditEvent.objects.values_list("metadata", flat=True)), default=str)
    assert WEBHOOK_SECRET not in dump


def test_full_lifecycle_verifies_clean(manager, owner, taxed_invoice, bank, card, provider):
    tx, _ = start(owner, taxed_invoice, card)
    payments.handle_webhook(provider, *signed_event(provider, "e", "payment.succeeded", tx.external_id, "110.00"))
    payments.refund_payment(manager, tx, amount="10.00")
    assert integrity.verify_all() == []
    Invoice.objects.filter(pk=taxed_invoice.pk).update(amount_paid=D("1.00"))  # tamper
    assert any("amount paid" in p for p in integrity.verify_all())


def test_a_manual_payment_records_the_account_the_transaction_and_the_date_received(manager, invoice, bank):
    from datetime import timedelta

    from django.utils import timezone

    received = timezone.now() - timedelta(days=3)
    tx = payments.record_payment(manager, invoice, amount="60.00", method=bank, reference="TXN-778812",
                                 note="Deposited at the branch", occurred_at=received)
    assert (tx.method_name, tx.reference, tx.occurred_at, tx.status) == (
        bank.name, "TXN-778812", received, TransactionStatus.SUCCEEDED)
    assert tx.recorded_by == manager and reload(invoice).amount_paid == D("60.00")
    assert AuditEvent.objects.filter(action="payment.recorded").exists()


def test_the_date_received_cannot_be_in_the_future(manager, invoice):
    from datetime import timedelta

    from django.utils import timezone

    with pytest.raises(ServiceError) as exc:
        payments.record_payment(manager, invoice, amount="10.00", occurred_at=timezone.now() + timedelta(days=2))
    assert exc.value.code == "payment_date_invalid" and not Transaction.objects.exists()
