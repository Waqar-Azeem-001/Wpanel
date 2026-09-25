"""Stages, cancellation requests and their review, ending services, refunds, and the unpaid-service lifecycle."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.billing import payments
from apps.billing.models import Invoice, InvoiceStatus, Transaction
from apps.core.exceptions import ServiceError
from apps.domains.models import Domain, DomainStatus
from apps.hosting import services as hosting_services
from apps.hosting.models import HostingAccount, HostingStatus
from apps.lifecycle import services
from apps.lifecycle.models import (CancellationRequest, CancellationStatus, LifecycleNotice, LifecycleSettings, Stage)
from apps.notifications.models import Notification
from apps.renewals import services as renewals
from apps.renewals.models import ChangeStatus

from .conftest import expire, pay, paid_account_payment

pytestmark = pytest.mark.django_db

D = Decimal
END = "end_of_term"
NOW = "immediate"


def ask(actor, service, **kw):
    kw.setdefault("reason_code", "not_needed")
    kw.setdefault("timing", END)
    return services.request_cancellation(actor, service, **kw)


def reload(obj):
    obj.refresh_from_db()
    return obj


# --- Stages ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("days_ago,stage", [
    (-200, Stage.ACTIVE), (-15, Stage.ACTIVE), (-14, Stage.RENEWAL_DUE), (-1, Stage.RENEWAL_DUE),
    (0, Stage.OVERDUE), (2, Stage.OVERDUE), (3, Stage.GRACE), (40, Stage.GRACE)])
def test_the_stage_of_an_active_account_follows_its_paid_through_date(account, days_ago, stage):
    assert services.stage_of(expire(account, days_ago)) == stage


def test_stages_for_other_states_and_accounts_without_a_term(account, manager):
    hosting_services.suspend_account(manager, account, reason="Abuse")
    assert services.stage_of(reload(account)) == Stage.SUSPENDED
    hosting_services.terminate_account(manager, account)
    assert services.stage_of(reload(account)) == Stage.TERMINATED
    HostingAccount.objects.filter(pk=account.pk).update(status=HostingStatus.PENDING)
    assert services.stage_of(reload(account)) is None
    HostingAccount.objects.filter(pk=account.pk).update(status=HostingStatus.ACTIVE, billing_cycle="", expires_at=None)
    assert services.stage_of(reload(account)) == Stage.ACTIVE  # no paid term: nothing can lapse


def test_domain_stages(domain):
    assert services.stage_of(domain) in (Stage.ACTIVE, Stage.RENEWAL_DUE)
    assert services.stage_of(expire(domain, 1)) == Stage.OVERDUE
    Domain.objects.filter(pk=domain.pk).update(status=DomainStatus.EXPIRED)
    assert services.stage_of(reload(domain)) == Stage.EXPIRED


def test_the_stage_changes_with_the_settings(account, admin):
    expire(account, 4)
    assert services.stage_of(account) == Stage.GRACE
    services.save_settings(admin, grace_after_days=10, suspend_after_days=20, terminate_after_days=40)
    assert services.stage_of(account) == Stage.OVERDUE


def test_the_timeline_reflects_what_is_switched_on(account, admin):
    expire(account, 0)
    when = services.timeline(account)
    assert when["suspend"] == account.expires_at + timedelta(days=7) and when["terminate"] is None  # off by default
    services.save_settings(admin, auto_terminate=True)
    assert services.timeline(account)["terminate"] == account.expires_at + timedelta(days=30)


# --- Settings ---------------------------------------------------------------------------------------------------------

def test_settings_are_validated_and_audited(admin, manager):
    row = services.save_settings(admin, grace_after_days=2, suspend_after_days=5, terminate_after_days=20)
    assert (row.grace_after_days, row.suspend_after_days, row.terminate_after_days) == (2, 5, 20)
    assert AuditEvent.objects.filter(action="lifecycle.settings_changed").exists()
    for bad in ({"grace_after_days": 9}, {"suspend_after_days": 2}, {"terminate_after_days": 5},
                {"grace_after_days": 0}, {"terminate_after_days": 9999}):
        with pytest.raises(Exception) as exc:
            services.save_settings(admin, **bad)
        assert exc.type.__name__ == "ValidationError", bad
    assert LifecycleSettings.load().suspend_after_days == 5  # nothing partial was saved
    with pytest.raises(ServiceError) as exc:
        services.save_settings(manager, auto_terminate=True)  # managers cannot change system settings
    assert exc.value.code == "permission_denied"


def test_termination_is_off_and_lifting_suspensions_is_on_by_default():
    row = LifecycleSettings.load()
    assert (row.auto_suspend, row.auto_terminate, row.unsuspend_on_payment) == (True, False, True)


# --- Requesting -------------------------------------------------------------------------------------------------------

def test_the_owner_can_ask_and_the_team_and_customer_are_told(account, owner, manager, agent):
    mail.outbox.clear()
    cr = ask(owner, account, reason_code="too_expensive", reason_text="Found a cheaper plan")
    assert (cr.status, cr.timing, cr.client_id, cr.on_behalf) == (CancellationStatus.PENDING, END, account.client_id, False)
    assert cr.term_expires_at == account.expires_at and cr.code.startswith("C")
    assert [m.subject for m in mail.outbox] == [f"We received your request to cancel {account.domain} - Wpanel"]
    assert Notification.objects.filter(user=owner, event="cancellation.requested").exists()
    team = {n.user for n in Notification.objects.filter(event="cancellation.requested_team")}
    assert manager in team and agent not in team  # only people who can act on hosting
    assert AuditEvent.objects.filter(action="cancellation.requested", metadata__kind="hosting").exists()


def test_only_the_owner_of_the_account_can_ask(account, owner, billing_contact, stranger, customer):
    for user in (billing_contact, stranger, customer):
        with pytest.raises(ServiceError) as exc:
            ask(user, account)
        assert exc.value.code == "permission_denied", user.email
    assert not CancellationRequest.objects.exists()
    assert ask(owner, account).status == CancellationStatus.PENDING


def test_staff_can_ask_on_the_customers_behalf(account, manager):
    cr = ask(manager, account, timing=NOW)
    assert cr.on_behalf and cr.requested_by == manager


def test_a_request_needs_a_valid_reason_timing_and_a_live_service(account, owner, domain):
    for kwargs, code in (({"reason_code": "nonsense"}, "reason_invalid"), ({"reason_code": "other"}, "reason_required"),
                         ({"timing": "someday"}, "timing_invalid"), ({"reason_text": "x" * 1001}, "reason_invalid")):
        with pytest.raises(ServiceError) as exc:
            ask(owner, account, **kwargs)
        assert exc.value.code == code, kwargs
    with pytest.raises(ServiceError) as exc:
        ask(owner, domain, timing=NOW)  # a registered domain cannot be ended early
    assert exc.value.code == "timing_invalid"
    HostingAccount.objects.filter(pk=account.pk).update(status=HostingStatus.PENDING)
    with pytest.raises(ServiceError) as exc:
        ask(owner, reload(account))
    assert exc.value.code == "service_not_live"
    assert not CancellationRequest.objects.exists()


def test_one_open_request_per_service_but_a_new_one_after_it_closes(account, owner, manager):
    first = ask(owner, account)
    with pytest.raises(ServiceError) as exc:
        ask(owner, account)
    assert exc.value.code == "already_requested"
    services.withdraw(owner, first)
    assert ask(owner, account).pk != first.pk


# --- Approving hosting ------------------------------------------------------------------------------------------------

def test_approving_at_the_end_of_the_term_schedules_it_and_changes_nothing_yet(account, owner, manager):
    cr = ask(owner, account)
    mail.outbox.clear()
    cr = services.approve(manager, cr, note="Sorry to see you go")
    assert cr.status == CancellationStatus.APPROVED and cr.effective_at == account.expires_at
    assert (cr.reviewed_by, cr.review_note) == (manager, "Sorry to see you go")
    assert reload(account).status == HostingStatus.ACTIVE
    assert "was approved" in mail.outbox[0].subject and "Sorry to see you go" in mail.outbox[0].body


def test_approving_immediately_ends_the_account_now(account, owner, manager):
    cr = services.approve(manager, ask(owner, account, timing=NOW))
    assert cr.status == CancellationStatus.COMPLETED and cr.completed_at and cr.claimed_at is None
    assert reload(account).status == HostingStatus.TERMINATED
    assert AuditEvent.objects.filter(action="hosting.terminated").exists()
    assert AuditEvent.objects.filter(action="cancellation.completed").exists()
    assert "terminated" in mail.outbox[-1].subject  # the account's own email, sent once


def test_staff_can_change_the_timing_when_approving(account, owner, manager):
    cr = services.approve(manager, ask(owner, account, timing=END), timing=NOW)
    assert cr.timing == NOW and cr.status == CancellationStatus.COMPLETED


def test_an_account_already_past_its_term_ends_immediately_even_at_end_of_term(account, owner, manager):
    expire(account, 5)
    cr = services.approve(manager, ask(owner, account))
    assert cr.status == CancellationStatus.COMPLETED and reload(account).status == HostingStatus.TERMINATED


def test_a_suspended_account_can_be_cancelled(account, owner, manager):
    hosting_services.suspend_account(manager, account, reason="Abuse")
    cr = services.approve(manager, ask(owner, reload(account), timing=NOW))
    assert cr.status == CancellationStatus.COMPLETED and reload(account).status == HostingStatus.TERMINATED


def test_approval_is_final_billing_open_renewal_invoices_are_cancelled(account, owner, manager):
    change = renewals.create_hosting_renewal(manager, account)
    invoice = change.invoice
    services.approve(manager, ask(owner, reload(account)))
    assert reload(invoice).status == InvoiceStatus.CANCELLED
    assert reload(change).status == ChangeStatus.VOID
    assert AuditEvent.objects.filter(action="cancellation.final_billing").exists()


def test_a_renewal_invoice_with_a_payment_is_left_for_billing_to_review(account, owner, manager, bank):
    change = renewals.create_hosting_renewal(manager, account)
    payments.record_payment(manager, change.invoice, amount="10.00", method=bank)  # a part payment
    services.approve(manager, ask(owner, reload(account)))
    assert reload(change.invoice).status != InvoiceStatus.CANCELLED
    note = AuditEvent.objects.get(action="cancellation.final_billing").metadata["notes"][0]
    assert "partially paid" in note and "review it in Billing" in note


def test_no_renewal_is_invoiced_or_offered_for_a_service_being_cancelled(account, owner, manager):
    cr = ask(owner, account)
    with pytest.raises(ServiceError) as exc:
        renewals.create_hosting_renewal(manager, account)
    assert exc.value.code == "cancellation_open"
    expire(account, -3)
    assert renewals.generate_renewal_invoices()["hosting"] == 0 and not Invoice.objects.filter(
        service_changes__hosting_account=account).exists()
    services.withdraw(owner, cr)
    assert renewals.generate_renewal_invoices()["hosting"] == 1  # back to normal once withdrawn


def test_only_staff_who_manage_the_service_can_review(account, owner, agent, customer, manager, admin):
    cr = ask(owner, account)
    for user in (agent, customer, owner):
        with pytest.raises(ServiceError) as exc:
            services.approve(user, cr)
        assert exc.value.code == "permission_denied", user.email
        with pytest.raises(ServiceError):
            services.reject(user, cr, note="No")
    assert services.approve(admin, cr).status == CancellationStatus.APPROVED
    assert reload(cr).reviewed_by == admin


def test_a_request_can_only_be_approved_once(account, owner, manager):
    cr = ask(owner, account)
    services.approve(manager, cr)
    with pytest.raises(ServiceError) as exc:
        services.approve(manager, cr)
    assert exc.value.code == "invalid_status"


# --- Rejecting and withdrawing -----------------------------------------------------------------------------------------

def test_rejecting_needs_a_reason_tells_the_customer_and_changes_nothing(account, owner, manager):
    cr = ask(owner, account, timing=NOW)
    with pytest.raises(ServiceError) as exc:
        services.reject(manager, cr, note="  ")
    assert exc.value.code == "note_required"
    mail.outbox.clear()
    cr = services.reject(manager, cr, note="Please pay the outstanding invoice first")
    assert cr.status == CancellationStatus.REJECTED and cr.review_note.startswith("Please pay")
    assert reload(account).status == HostingStatus.ACTIVE
    assert "declined" in mail.outbox[0].subject and "outstanding invoice" in mail.outbox[0].body
    with pytest.raises(ServiceError):
        services.approve(manager, cr)  # a closed request cannot be revived
    assert ask(owner, account).status == CancellationStatus.PENDING  # but the customer may ask again


def test_withdrawing_a_pending_and_an_approved_request(account, domain, owner, manager, billing_contact, stranger):
    pending = ask(owner, account)
    for user in (billing_contact, stranger):
        with pytest.raises(ServiceError):
            services.withdraw(user, pending)
    assert services.withdraw(owner, pending).status == CancellationStatus.WITHDRAWN
    scheduled = services.approve(manager, ask(owner, domain))
    assert reload(domain).auto_renew is False
    services.withdraw(owner, scheduled)
    assert reload(domain).auto_renew is True  # the setting it had before is restored
    assert reload(scheduled).status == CancellationStatus.WITHDRAWN


def test_a_completed_request_cannot_be_withdrawn(account, owner, manager):
    cr = services.approve(manager, ask(owner, account, timing=NOW))
    with pytest.raises(ServiceError) as exc:
        services.withdraw(owner, cr)
    assert exc.value.code == "invalid_status"


def test_a_request_being_carried_out_cannot_be_withdrawn(account, owner, manager):
    cr = services.approve(manager, ask(owner, account))
    CancellationRequest.objects.filter(pk=cr.pk).update(claimed_at=timezone.now())
    with pytest.raises(ServiceError) as exc:
        services.withdraw(owner, cr)
    assert exc.value.code == "in_progress"


# --- Refunds ----------------------------------------------------------------------------------------------------------

def test_the_suggested_refund_is_the_unused_part_of_the_term(account):
    assert services.suggested_refund(account) == D("72.60")  # 100.00 x 265 of 365 days
    assert services.suggested_refund(expire(account, 1)) == D("0.00")


def test_refund_options_list_the_payments_for_this_service_only(account, manager, bank, client_obj):
    tx = paid_account_payment(manager, account)
    other = hosting_services.request_hosting(manager, client_obj, account.product, "other.com")
    assert [(t.pk, avail) for t, avail in services.refundable_payments(account)] == [(tx.pk, tx.amount)]
    assert services.refundable_payments(other) == []


def test_an_immediate_cancellation_can_refund_part_of_a_payment(account, owner, manager):
    tx = paid_account_payment(manager, account)
    cr = ask(owner, reload(account), timing=NOW)
    cr = services.approve(manager, cr, refund_amount="60.00", refund_payment=tx)
    assert cr.status == CancellationStatus.COMPLETED and cr.refund_amount == D("60.00")
    refund = cr.refund_transaction
    assert (refund.type, refund.amount, refund.parent_id, refund.status) == ("refund", D("60.00"), tx.pk, "succeeded")
    assert payments.refundable_amount(reload(tx)) == tx.amount - D("60.00")
    assert reload(account).status == HostingStatus.TERMINATED


@pytest.mark.parametrize("kwargs,code", [
    ({"refund_amount": "1.00", "timing": END}, "refund_not_allowed"),
    ({"refund_amount": "1.00"}, "refund_payment_required"),
    ({"refund_amount": "99999.00"}, "refund_exceeds_payment"),
])
def test_refund_rules(account, owner, manager, kwargs, code):
    tx = paid_account_payment(manager, account)
    cr = ask(owner, reload(account), timing=NOW)
    options = {"timing": NOW, "refund_payment": None if code == "refund_payment_required" else tx} | kwargs
    with pytest.raises(ServiceError) as exc:
        services.approve(manager, cr, **options)
    assert exc.value.code == code
    assert reload(cr).status == CancellationStatus.PENDING and reload(account).status == HostingStatus.ACTIVE
    assert not Transaction.objects.filter(type="refund").exists()


def test_a_refund_must_come_from_a_payment_for_this_service(account, owner, manager, agent, client_obj, bank):
    from apps.billing import invoicing

    tx = paid_account_payment(manager, account)
    cr = ask(owner, reload(account), timing=NOW)
    with pytest.raises(ServiceError):
        services.approve(agent, cr, refund_amount="5.00", refund_payment=tx)  # an agent cannot approve, let alone refund

    other = invoicing.issue_invoice(manager, invoicing.create_invoice(
        manager, client_obj, lines=[{"description": "Other", "quantity": 1, "unit_price": "20.00"}]))
    other_tx = payments.record_payment(manager, other, amount="20.00", method=bank)
    with pytest.raises(ServiceError) as exc:
        services.approve(manager, cr, refund_amount="5.00", refund_payment=other_tx)
    assert exc.value.code == "refund_payment_invalid"  # a payment on some other invoice of the client
    assert reload(cr).status == CancellationStatus.PENDING


def test_a_failed_refund_is_recorded_and_the_service_still_ends(account, owner, manager, monkeypatch):
    tx = paid_account_payment(manager, account)

    def broken(*args, **kwargs):
        raise ServiceError("The gateway is down.", code="refund_failed")

    monkeypatch.setattr(payments, "refund_payment", broken)
    cr = services.approve(manager, ask(owner, reload(account), timing=NOW), refund_amount="10.00", refund_payment=tx)
    assert cr.status == CancellationStatus.COMPLETED and cr.refund_transaction is None
    assert cr.refund_error == "The gateway is down." and reload(account).status == HostingStatus.TERMINATED
    assert AuditEvent.objects.filter(action="cancellation.refund_failed").exists()


# --- Domains ----------------------------------------------------------------------------------------------------------

def test_a_domain_is_set_not_to_renew_and_ends_when_it_expires(domain, owner, manager):
    cr = services.approve(manager, ask(owner, domain))
    assert cr.status == CancellationStatus.APPROVED and cr.effective_at == domain.expires_at
    assert reload(domain).auto_renew is False and reload(domain).status == DomainStatus.ACTIVE
    assert services.run_due_cancellations()["completed"] == 0  # not yet
    mail.outbox.clear()
    assert services.run_due_cancellations(now=cr.effective_at + timedelta(days=1)) == {"completed": 1, "errors": []}
    assert reload(domain).status == DomainStatus.CANCELLED and reload(cr).status == CancellationStatus.COMPLETED
    assert "has been cancelled" in mail.outbox[0].subject


def test_a_domain_that_already_lapsed_is_cancelled_at_approval(domain, owner, manager):
    expire(domain, 3)
    cr = services.approve(manager, ask(owner, reload(domain)))
    assert cr.status == CancellationStatus.COMPLETED and reload(domain).status == DomainStatus.CANCELLED


def test_domain_reviewers_need_domain_permission(domain, owner, staff):
    cr = ask(owner, domain)
    # a support agent has neither manage_domains nor manage_hosting
    with pytest.raises(ServiceError):
        services.approve(staff(Role.SUPPORT_AGENT), cr)


# --- Carrying it out --------------------------------------------------------------------------------------------------

def test_a_failed_termination_leaves_the_request_approved_and_retryable(account, owner, manager, monkeypatch):
    cr = services.approve(manager, ask(owner, account))  # scheduled
    later = cr.effective_at + timedelta(days=1)
    real = hosting_services.terminate_account
    calls = []

    def broken(*args, **kwargs):
        calls.append(1)
        raise ServiceError("The server did not answer.", code="adapter_error")

    monkeypatch.setattr(hosting_services, "terminate_account", broken)
    result = services.run_due_cancellations(now=later)
    assert result["completed"] == 0 and "The server did not answer." in result["errors"][0]
    cr = reload(cr)
    assert cr.status == CancellationStatus.APPROVED and cr.last_error == "The server did not answer."
    assert cr.claimed_at is None and cr.needs_attention
    monkeypatch.setattr(hosting_services, "terminate_account", real)
    assert services.retry(manager, cr).status == CancellationStatus.COMPLETED
    assert reload(account).status == HostingStatus.TERMINATED and reload(cr).last_error == ""


def test_a_request_someone_else_is_carrying_out_is_not_run_twice(account, owner, manager, monkeypatch):
    cr = services.approve(manager, ask(owner, account))
    CancellationRequest.objects.filter(pk=cr.pk).update(claimed_at=timezone.now())
    ended = []
    monkeypatch.setattr(hosting_services, "terminate_account", lambda *a, **k: ended.append(1))
    assert services.execute(cr).status == CancellationStatus.APPROVED and ended == []
    CancellationRequest.objects.filter(pk=cr.pk).update(claimed_at=timezone.now() - timedelta(minutes=30))
    monkeypatch.undo()
    assert services.execute(cr).status == CancellationStatus.COMPLETED  # a stale claim (a dead worker) is taken over


def test_a_service_that_already_ended_just_completes_the_request(account, owner, manager):
    cr = services.approve(manager, ask(owner, account))
    hosting_services.terminate_account(manager, account)
    assert services.retry(manager, reload(cr)).status == CancellationStatus.COMPLETED


def test_only_people_who_manage_the_service_can_retry(account, owner, manager, agent):
    cr = services.approve(manager, ask(owner, account))
    with pytest.raises(ServiceError):
        services.retry(agent, cr)


def test_the_daily_job_only_carries_out_requests_that_are_due(account, domain, owner, manager):
    cr = services.approve(manager, ask(owner, account))  # ends in 265 days
    assert services.run_lifecycle()["cancelled"] == 0 and reload(account).status == HostingStatus.ACTIVE
    assert services.run_lifecycle(now=cr.effective_at + timedelta(hours=1))["cancelled"] == 1
    assert reload(account).status == HostingStatus.TERMINATED


# --- The unpaid-service lifecycle -------------------------------------------------------------------------------------

def test_a_final_notice_goes_out_once_when_the_grace_period_starts(account, owner):
    expire(account, 2)
    mail.outbox.clear()
    assert services.run_lifecycle()["notices"] == 0  # still just overdue
    expire(account, 3)
    assert services.run_lifecycle()["notices"] == 1
    assert services.run_lifecycle()["notices"] == 0  # not repeated
    assert len(mail.outbox) == 1 and "Final notice" in mail.outbox[0].subject
    assert "will be suspended" in mail.outbox[0].body
    assert Notification.objects.filter(user=owner, event="service.grace_notice").count() == 1
    assert LifecycleNotice.objects.count() == 1


def test_a_lapse_after_a_renewal_gets_its_own_notice(account, manager):
    expire(account, 4)
    services.run_lifecycle()
    HostingAccount.objects.filter(pk=account.pk).update(expires_at=timezone.now() + timedelta(days=200))
    expire(reload(account), 4)  # a later term lapses
    HostingAccount.objects.filter(pk=account.pk).update(expires_at=timezone.now() - timedelta(days=4, hours=1))
    assert services.run_lifecycle()["notices"] == 1
    assert LifecycleNotice.objects.count() == 2


def test_an_unpaid_account_is_suspended_after_the_configured_days(account, admin):
    expire(account, 6)
    assert services.run_lifecycle()["suspended"] == 0
    expire(account, 7)
    result = services.run_lifecycle()
    assert result["suspended"] == 1 and result["errors"] == []
    account = reload(account)
    assert account.status == HostingStatus.SUSPENDED and account.suspended_for_nonpayment
    assert "Renewal unpaid" in account.suspend_reason
    assert "suspended" in mail.outbox[-1].subject
    event = AuditEvent.objects.get(action="hosting.suspended")
    assert event.actor is None and event.metadata["for_nonpayment"] is True  # done by the system


def test_suspension_can_be_switched_off_and_timings_are_configurable(account, admin):
    services.save_settings(admin, auto_suspend=False)
    expire(account, 60)
    assert services.run_lifecycle()["suspended"] == 0 and reload(account).status == HostingStatus.ACTIVE
    services.save_settings(admin, auto_suspend=True, grace_after_days=10, suspend_after_days=90, terminate_after_days=120)
    assert services.run_lifecycle()["suspended"] == 0
    expire(account, 90)
    assert services.run_lifecycle()["suspended"] == 1


def test_nothing_is_terminated_unless_that_is_switched_on(account, admin):
    expire(account, 50)
    services.run_lifecycle()
    assert reload(account).status == HostingStatus.SUSPENDED
    assert services.run_lifecycle()["terminated"] == 0 and reload(account).status == HostingStatus.SUSPENDED
    services.save_settings(admin, auto_terminate=True)
    result = services.run_lifecycle()
    assert result["terminated"] == 1 and reload(account).status == HostingStatus.TERMINATED


def test_only_a_suspension_for_non_payment_is_ever_terminated_automatically(account, manager, admin):
    services.save_settings(admin, auto_terminate=True)
    hosting_services.suspend_account(manager, account, reason="Abuse report")  # by a person, not for non-payment
    expire(account, 90)
    assert services.run_lifecycle()["terminated"] == 0 and reload(account).status == HostingStatus.SUSPENDED


def test_a_payment_waiting_for_confirmation_stops_suspension(account, owner, manager, bank):
    change = renewals.create_hosting_renewal(manager, account)
    payments.report_payment(owner, change.invoice, method=bank, reference="TT-9")  # reported, not yet confirmed
    expire(account, 20)
    assert services.run_lifecycle()["suspended"] == 0 and reload(account).status == HostingStatus.ACTIVE
    tx = change.invoice.transactions.get()
    payments.reject_payment(manager, tx, reason="Not found")
    assert services.run_lifecycle()["suspended"] == 1


def test_accounts_without_a_term_and_other_states_are_left_alone(account, manager, client_obj, starter):
    HostingAccount.objects.filter(pk=account.pk).update(billing_cycle="", expires_at=timezone.now() - timedelta(days=90))
    pending = hosting_services.request_hosting(manager, client_obj, starter, "pending.com")
    HostingAccount.objects.filter(pk=pending.pk).update(billing_cycle="annual",
                                                        expires_at=timezone.now() - timedelta(days=90))
    result = services.run_lifecycle()
    assert result["suspended"] == 0 and result["notices"] == 0


def test_a_dry_run_reports_and_changes_nothing(account, admin):
    services.save_settings(admin, auto_terminate=True)
    expire(account, 50)
    mail.outbox.clear()
    result = services.run_lifecycle(dry_run=True)
    assert result["dry_run"] and result["suspended"] == 1 and result["notices"] == 1
    assert reload(account).status == HostingStatus.ACTIVE and mail.outbox == [] and not LifecycleNotice.objects.exists()


def test_one_failing_account_does_not_stop_the_others(account, manager, client_obj, starter, monkeypatch):
    second = hosting_services.request_hosting(manager, client_obj, starter, "second.com")
    hosting_services.complete_provisioning(manager, second)
    for a in (account, second):
        HostingAccount.objects.filter(pk=a.pk).update(billing_cycle="annual",
                                                      expires_at=timezone.now() - timedelta(days=10))
    real = hosting_services.suspend_account

    def flaky(actor, acct, **kwargs):
        if acct.pk == account.pk:
            raise ServiceError("Server unreachable.", code="adapter_error")
        return real(actor, acct, **kwargs)

    monkeypatch.setattr(hosting_services, "suspend_account", flaky)
    result = services.run_lifecycle()
    assert result["suspended"] == 1 and len(result["errors"]) == 1 and "Server unreachable." in result["errors"][0]
    assert reload(second).status == HostingStatus.SUSPENDED and reload(account).status == HostingStatus.ACTIVE


def test_paying_the_renewal_lifts_a_non_payment_suspension(account, manager):
    expire(account, 10)
    services.run_lifecycle()
    assert reload(account).status == HostingStatus.SUSPENDED
    change = renewals.create_hosting_renewal(manager, account)
    mail.outbox.clear()
    pay(manager, change.invoice)
    account = reload(account)
    assert account.status == HostingStatus.ACTIVE and not account.suspended_for_nonpayment
    assert account.expires_at > timezone.now()
    assert any("active again" in m.subject for m in mail.outbox)


def test_paying_never_lifts_a_suspension_made_for_another_reason_or_when_switched_off(account, manager, admin):
    hosting_services.suspend_account(manager, account, reason="Abuse report")
    change = renewals.create_hosting_renewal(manager, reload(account))
    pay(manager, change.invoice)
    assert reload(account).status == HostingStatus.SUSPENDED  # a person suspended it; a person must lift it

    hosting_services.unsuspend_account(manager, reload(account))
    services.save_settings(admin, unsuspend_on_payment=False)
    expire(account, 10)
    services.run_lifecycle()
    assert reload(account).suspended_for_nonpayment
    pay(manager, renewals.create_hosting_renewal(manager, reload(account)).invoice)
    assert reload(account).status == HostingStatus.SUSPENDED


def test_a_failure_lifting_the_suspension_never_undoes_the_payment(account, manager, monkeypatch):
    expire(account, 10)
    services.run_lifecycle()
    change = renewals.create_hosting_renewal(manager, reload(account))

    def broken(*args, **kwargs):
        raise ServiceError("Server unreachable.", code="adapter_error")

    monkeypatch.setattr(hosting_services, "unsuspend_account", broken)
    pay(manager, change.invoice)
    assert reload(change).status == ChangeStatus.APPLIED and reload(account).status == HostingStatus.SUSPENDED
    assert AuditEvent.objects.filter(action="hosting.unsuspend_failed").exists()


def test_the_overview_counts_services_by_stage(account, manager, client_obj, starter, domain):
    other = hosting_services.request_hosting(manager, client_obj, starter, "late.com")
    hosting_services.complete_provisioning(manager, other)
    HostingAccount.objects.filter(pk=other.pk).update(billing_cycle="annual",
                                                      expires_at=timezone.now() - timedelta(days=5))
    expire(account, -3)
    data = services.overview()
    assert data["counts"][Stage.RENEWAL_DUE] == 1 and data["counts"][Stage.GRACE] == 1
    assert [a.pk for a in data["rows"][Stage.GRACE]] == [other.pk]
    ask(client_obj.contacts.get().user, account)
    assert services.overview()["pending_cancellations"] == 1


def test_visibility_of_requests(account, domain, owner, billing_contact, stranger, manager, agent, customer):
    cr = ask(owner, account)
    assert list(services.visible_requests_for_user(owner)) == [cr]
    assert list(services.visible_requests_for_user(billing_contact)) == [cr]  # any contact may look
    assert list(services.visible_requests_for_user(stranger)) == [] and list(services.visible_requests_for_user(customer)) == []
    assert list(services.visible_requests_for_user(manager)) == [cr] and list(services.visible_requests_for_user(agent)) == [cr]
