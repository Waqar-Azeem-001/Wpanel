"""Joining, attribution, commissions, refunds, approvals and payouts."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core import mail
from django.utils import timezone

from apps.affiliates import services
from apps.affiliates.models import (Affiliate, AffiliateSettings, AffiliateStatus, Commission, CommissionStatus,
                                    Payout, Referral)
from apps.audit.models import AuditEvent
from apps.billing import payments
from apps.core.exceptions import ServiceError
from apps.notifications.models import Notification

from .conftest import pay_invoice

pytestmark = pytest.mark.django_db

D = Decimal


def reload(obj):
    obj.refresh_from_db()
    return obj


@pytest.fixture
def earned(manager, referred, affiliate, tax):
    """A 1000.00 sale (excl. tax) by the referred customer: a pending commission of 100.00."""
    invoice = pay_invoice(manager, referred[1], "1000.00")
    return Commission.objects.get(invoice=invoice)


# --- Settings --------------------------------------------------------------------------------------------------------

def test_settings_are_validated_permissioned_and_audited(admin, manager):
    row = services.save_settings(admin, commission_kind="fixed", commission_value=D("5"), hold_days=3)
    assert (row.commission_kind, row.commission_value, row.hold_days) == ("fixed", D("5"), 3)
    assert AuditEvent.objects.filter(action="affiliate.settings_changed").exists()
    for bad in ({"commission_kind": "percentage", "commission_value": D("150")}, {"commission_value": D("0")},
                {"commission_kind": "weekly"}, {"cookie_days": 0}, {"cookie_days": 400}, {"hold_days": 91},
                {"recurring_months": 61}):
        with pytest.raises(Exception) as exc:
            services.save_settings(admin, **bad)
        assert exc.type.__name__ == "ValidationError", bad
    assert AffiliateSettings.load().hold_days == 3
    with pytest.raises(ServiceError) as exc:
        services.save_settings(manager, hold_days=1)
    assert exc.value.code == "permission_denied"


def test_the_defaults():
    row = AffiliateSettings.load()
    assert (row.enabled, row.require_approval, row.cookie_days, row.commission_kind, row.commission_value,
            row.recurring_months, row.hold_days, row.minimum_payout) == (True, True, 30, "percentage", 10, 0, 14, 50)


# --- Joining ---------------------------------------------------------------------------------------------------------

def test_joining_waits_for_approval_when_the_programme_asks(make_customer, manager, admin, agent):
    user, _ = make_customer("new@example.com")
    affiliate = services.enrol(user, accept_terms=True, payout_details="Meezan 0011")
    assert affiliate.status == AffiliateStatus.PENDING and len(affiliate.code) == 8
    assert affiliate.payout_details == "Meezan 0011" and affiliate.terms_accepted_at
    alerted = {n.user for n in Notification.objects.filter(event="affiliate.applied")}
    assert alerted == {manager, admin}  # only people who manage affiliates
    assert AuditEvent.objects.filter(action="affiliate.joined").exists()


def test_joining_is_immediate_when_no_approval_is_needed(config, make_customer):
    affiliate = services.enrol(make_customer("new@example.com")[0], accept_terms=True)
    assert affiliate.status == AffiliateStatus.ACTIVE
    assert not Notification.objects.filter(event="affiliate.applied").exists()


def test_joining_rules(config, make_customer, admin, affiliate_user, affiliate):
    with pytest.raises(ServiceError) as exc:
        services.enrol(affiliate_user, accept_terms=True)
    assert exc.value.code == "already_affiliate"
    other = make_customer("other@example.com")[0]
    with pytest.raises(ServiceError) as exc:
        services.enrol(other, accept_terms=False)
    assert exc.value.code == "terms_required"
    with pytest.raises(Exception) as exc:
        services.enrol(other, accept_terms=True, payout_details="x" * 1001)
    assert exc.type.__name__ == "ValidationError"
    services.save_settings(admin, enabled=False)
    with pytest.raises(ServiceError) as exc:
        services.enrol(other, accept_terms=True)
    assert exc.value.code == "program_disabled"
    assert not Affiliate.objects.filter(user=other).exists()


def test_codes_are_unique_and_unambiguous(config, make_customer):
    codes = {services.enrol(make_customer(f"c{i}@example.com")[0], accept_terms=True).code for i in range(8)}
    assert len(codes) == 8 and all(set(c) <= set(services.CODE_ALPHABET) for c in codes)


# --- Reviewing affiliates --------------------------------------------------------------------------------------------

def test_approving_and_rejecting_an_application(make_customer, manager, agent):
    user, _ = make_customer("new@example.com")
    pending = services.enrol(user, accept_terms=True)
    with pytest.raises(ServiceError) as exc:
        services.approve_affiliate(agent, pending)
    assert exc.value.code == "permission_denied"
    mail.outbox.clear()
    active = services.approve_affiliate(manager, pending, note="Welcome")
    assert active.status == AffiliateStatus.ACTIVE and active.reviewed_by == manager and active.review_note == "Welcome"
    assert "affiliate" in mail.outbox[0].subject and services.referral_url(active) in mail.outbox[0].body
    assert Notification.objects.filter(user=user, event="affiliate.approved").exists()

    other, _ = make_customer("other@example.com")
    second = services.enrol(other, accept_terms=True)
    with pytest.raises(ServiceError) as exc:
        services.reject_affiliate(manager, second, note=" ")
    assert exc.value.code == "note_required"
    mail.outbox.clear()
    rejected = services.reject_affiliate(manager, second, note="Not a fit")
    assert rejected.status == AffiliateStatus.REJECTED and "Not a fit" in mail.outbox[0].body


def test_suspending_and_reactivating_and_illegal_moves(config, affiliate, manager):
    assert services.suspend_affiliate(manager, affiliate, note="Spam").status == AffiliateStatus.SUSPENDED
    assert services.reactivate_affiliate(manager, reload(affiliate)).status == AffiliateStatus.ACTIVE
    for action in (services.approve_affiliate, services.reactivate_affiliate):
        with pytest.raises(ServiceError) as exc:
            action(manager, reload(affiliate))
        assert exc.value.code == "invalid_status"
    assert AuditEvent.objects.filter(action="affiliate.suspended").exists()


def test_commission_rules_per_affiliate(affiliate, manager, agent):
    assert services.rule_for(affiliate) == ("percentage", D("10"))
    services.set_override(manager, affiliate, kind="fixed", value="7.50")
    assert services.rule_for(reload(affiliate)) == ("fixed", D("7.50")) and affiliate.has_override
    for kind, value in (("percentage", "101"), ("fixed", "0"), ("weekly", "5"), ("fixed", "abc")):
        with pytest.raises(Exception):
            services.set_override(manager, affiliate, kind=kind, value=value)
    with pytest.raises(ServiceError):
        services.set_override(agent, affiliate, kind="fixed", value="1")
    services.set_override(manager, affiliate, kind="")
    assert services.rule_for(reload(affiliate)) == ("percentage", D("10")) and not affiliate.has_override


def test_changing_a_code(config, affiliate, manager, make_customer):
    other = services.enrol(make_customer("o@example.com")[0], accept_terms=True)
    assert services.set_code(manager, affiliate, "Summer-Deal").code == "summer-deal"
    with pytest.raises(ServiceError) as exc:
        services.set_code(manager, other, "summer-deal")
    assert exc.value.code == "code_taken"
    for bad in ("ab", "has space", "-lead", "trail-", "x" * 33, "semi;colon"):
        with pytest.raises(Exception) as exc:
            services.set_code(manager, other, bad)
        assert exc.type.__name__ == "ValidationError", bad


def test_payout_details_are_edited_by_the_owner_or_staff_only(affiliate, affiliate_user, manager, make_customer):
    services.update_payout_details(affiliate_user, affiliate, "Bank X 123")
    assert reload(affiliate).payout_details == "Bank X 123"
    services.update_payout_details(manager, affiliate, "Bank Y 456")
    with pytest.raises(ServiceError):
        services.update_payout_details(make_customer("x@example.com")[0], affiliate, "steal")
    assert reload(affiliate).payout_details == "Bank Y 456"


# --- Links and attribution ------------------------------------------------------------------------------------------

def test_only_a_live_link_counts(affiliate, manager, admin):
    assert services.live_affiliate(affiliate.code.upper()) == affiliate
    services.count_visit(affiliate)
    services.count_visit(affiliate)
    assert reload(affiliate).visit_count == 2
    assert services.live_affiliate("nope") is None and services.live_affiliate("") is None
    services.suspend_affiliate(manager, affiliate)
    assert services.live_affiliate(affiliate.code) is None
    services.reactivate_affiliate(manager, affiliate)
    services.save_settings(admin, enabled=False)
    assert services.live_affiliate(affiliate.code) is None


def test_signing_up_through_a_link_attributes_the_customer(affiliate, make_customer):
    user, client = make_customer("buyer@example.com", code=affiliate.code.upper())
    referral = Referral.objects.get(client=client)
    assert referral.affiliate == affiliate and referral.source == "link"
    assert referral.label.startswith("Customer R") and "buyer" not in referral.label
    assert AuditEvent.objects.filter(action="referral.attributed", actor=user).exists()


@pytest.mark.parametrize("code", ["", "unknown-code", "   "])
def test_a_missing_or_bad_code_never_stops_signing_up(affiliate, make_customer, code):
    user, client = make_customer("buyer@example.com", code=code)
    assert user.pk and not Referral.objects.exists()


def test_codes_that_do_not_count(affiliate, make_customer, manager, admin):
    services.suspend_affiliate(manager, affiliate)
    make_customer("a@example.com", code=affiliate.code)
    services.reactivate_affiliate(manager, affiliate)
    services.save_settings(admin, enabled=False)
    make_customer("b@example.com", code=affiliate.code)
    assert not Referral.objects.exists()
    services.save_settings(admin, enabled=True, require_approval=True)
    waiting = services.enrol(make_customer("p@example.com")[0], accept_terms=True)
    assert waiting.status == AffiliateStatus.PENDING
    make_customer("d@example.com", code=waiting.code)  # an application not yet approved does not attribute
    assert not Referral.objects.exists()
    make_customer("c@example.com", code=affiliate.code)
    assert Referral.objects.count() == 1


def test_an_affiliate_is_not_credited_with_their_own_account(affiliate, affiliate_user):
    from apps.clients.models import Client

    own = Client.objects.get(email=affiliate_user.email)
    assert services.attribute_signup(own, affiliate.code) is None and not Referral.objects.exists()


def test_a_customer_is_attributed_once_and_never_by_a_later_click(affiliate, referred, config, make_customer):
    second = services.enrol(make_customer("second@example.com")[0], accept_terms=True)
    assert services.attribute_signup(referred[1], second.code) is None
    assert Referral.objects.get(client=referred[1]).affiliate == affiliate


def test_staff_can_credit_an_existing_client_once(affiliate, make_customer, manager, agent, config):
    _, client = make_customer("late@example.com")
    with pytest.raises(ServiceError):
        services.attribute_client(agent, client, affiliate)
    referral = services.attribute_client(manager, client, affiliate)
    assert referral.source == "staff" and referral.attributed_by == manager
    with pytest.raises(ServiceError) as exc:
        services.attribute_client(manager, client, affiliate)
    assert exc.value.code == "already_referred"


def test_staff_cannot_credit_an_inactive_affiliate_or_the_affiliates_own_client(affiliate, affiliate_user, manager,
                                                                                make_customer):
    from apps.clients.models import Client

    own = Client.objects.get(email=affiliate_user.email)
    with pytest.raises(ServiceError) as exc:
        services.attribute_client(manager, own, affiliate)
    assert exc.value.code == "self_referral"
    services.suspend_affiliate(manager, affiliate)
    with pytest.raises(ServiceError) as exc:
        services.attribute_client(manager, make_customer("z@example.com")[1], reload(affiliate))
    assert exc.value.code == "affiliate_inactive"


# --- Earning a commission -------------------------------------------------------------------------------------------

def test_a_paid_invoice_earns_a_percentage_of_the_sale_excluding_tax(earned, affiliate):
    assert earned.status == CommissionStatus.PENDING and earned.affiliate == affiliate
    assert (earned.base_amount, earned.kind, earned.rate) == (D("1000.00"), "percentage", D("10.00"))
    assert earned.amount == earned.original_amount == D("100.00")  # 10% of 1000, not of 1100
    assert earned.invoice.total == D("1100.00") and earned.currency == "USD"
    assert earned.eligible_at > timezone.now() + timedelta(days=13)
    assert AuditEvent.objects.filter(action="commission.earned").exists()


def test_the_base_is_after_discount(manager, referred, affiliate, tax):
    invoice = pay_invoice(manager, referred[1], "1000.00", discount="200.00")
    commission = Commission.objects.get(invoice=invoice)
    assert commission.base_amount == D("800.00") and commission.amount == D("80.00")


def test_a_fixed_commission_is_capped_at_the_sale(manager, referred, affiliate, admin, tax):
    services.save_settings(admin, commission_kind="fixed", commission_value=D("25"))
    assert Commission.objects.get(invoice=pay_invoice(manager, referred[1], "1000.00")).amount == D("25.00")
    services.save_settings(admin, commission_value=D("500"), recurring_months=12)
    assert Commission.objects.get(invoice=pay_invoice(manager, referred[1], "40.00")).amount == D("40.00")


def test_an_affiliates_own_rule_beats_the_default(manager, referred, affiliate, tax):
    services.set_override(manager, affiliate, kind="percentage", value="25")
    commission = Commission.objects.get(invoice=pay_invoice(manager, referred[1], "200.00"))
    assert (commission.rate, commission.amount) == (D("25.00"), D("50.00"))


def test_the_rule_is_frozen_when_the_invoice_is_paid(manager, referred, affiliate, admin, earned):
    services.save_settings(admin, commission_value=D("50"))
    assert reload(earned).amount == D("100.00") and earned.rate == D("10.00")


def test_only_the_first_paid_invoice_earns_by_default(manager, referred, affiliate, tax):
    pay_invoice(manager, referred[1], "100.00")
    pay_invoice(manager, referred[1], "100.00")
    assert Commission.objects.count() == 1


def test_recurring_commission_lasts_for_the_configured_months(manager, referred, affiliate, admin, tax):
    services.save_settings(admin, recurring_months=3)
    pay_invoice(manager, referred[1], "100.00")
    pay_invoice(manager, referred[1], "100.00")
    assert Commission.objects.count() == 2
    Referral.objects.update(created_at=timezone.now() - timedelta(days=200))  # signed up long ago
    pay_invoice(manager, referred[1], "100.00")
    assert Commission.objects.count() == 2


def test_a_part_payment_earns_nothing_until_the_invoice_is_paid_in_full(manager, referred, affiliate, tax):
    invoice = pay_invoice(manager, referred[1], "100.00", amount="50.00")
    assert not Commission.objects.exists()
    payments.record_payment(manager, invoice, amount=str(reload(invoice).balance_due))
    assert Commission.objects.get().base_amount == D("100.00")


def test_who_does_not_earn(manager, referred, affiliate, admin, make_customer, tax):
    pay_invoice(manager, make_customer("plain@example.com")[1], "100.00")  # nobody referred them
    services.suspend_affiliate(manager, affiliate)
    pay_invoice(manager, referred[1], "100.00")  # the affiliate is suspended
    assert not Commission.objects.exists()
    assert AuditEvent.objects.filter(action="commission.skipped").exists()
    services.reactivate_affiliate(manager, affiliate)
    services.save_settings(admin, enabled=False)
    pay_invoice(manager, referred[1], "100.00")  # the programme is off
    assert not Commission.objects.exists()


def test_the_paid_signal_repeating_changes_nothing(earned):
    assert services.on_invoice_paid(earned.invoice) is None
    assert Commission.objects.count() == 1


def test_no_hold_means_approved_at_once(manager, referred, affiliate, admin, tax):
    services.save_settings(admin, hold_days=0)
    commission = Commission.objects.get(invoice=pay_invoice(manager, referred[1], "100.00"))
    assert commission.status == CommissionStatus.APPROVED and commission.decided_at


# --- Refunds ---------------------------------------------------------------------------------------------------------

def refund(manager, invoice, amount=None):
    tx = invoice.transactions.filter(type="payment").first()
    payments.refund_payment(manager, tx, amount=amount)
    return reload(invoice)


def test_a_full_refund_voids_an_unpaid_commission(manager, earned):
    refund(manager, earned.invoice)
    earned = reload(earned)
    assert earned.status == CommissionStatus.REJECTED and earned.amount == 0 and "refunded" in earned.note
    assert earned.original_amount == D("100.00")  # what it was is kept


def test_a_partial_refund_lowers_it_in_proportion(manager, earned):
    refund(manager, earned.invoice, "550.00")  # half of the 1100.00 paid
    earned = reload(earned)
    assert earned.status == CommissionStatus.PENDING and earned.amount == D("50.00")
    refund(manager, reload(earned.invoice), "550.00")
    assert reload(earned).status == CommissionStatus.REJECTED


def test_an_approved_commission_follows_the_refund_too(manager, earned):
    services.approve_commission(manager, earned)
    refund(manager, earned.invoice, "110.00")
    earned = reload(earned)
    assert earned.status == CommissionStatus.APPROVED and earned.amount == D("90.00")


def test_a_refund_after_the_payout_is_flagged_not_taken_back(manager, admin, earned):
    services.save_settings(admin, minimum_payout=D("10"))
    services.approve_commission(manager, earned)
    services.record_payout(manager, earned.affiliate, method="Bank")
    Notification.objects.all().delete()
    refund(manager, earned.invoice, "110.00")
    refund(manager, reload(earned.invoice), "110.00")  # a second refund does not raise a second alert
    earned = reload(earned)
    assert earned.status == CommissionStatus.PAID and earned.amount == D("100.00") and earned.needs_review
    assert {n.user for n in Notification.objects.filter(event="commission.refund_after_payout")} == {manager, admin}
    assert Notification.objects.filter(event="commission.refund_after_payout").count() == 2


def test_refunds_on_invoices_without_a_commission_are_harmless(manager, make_customer, tax):
    invoice = pay_invoice(manager, make_customer("plain@example.com")[1], "100.00")
    refund(manager, invoice)
    assert not Commission.objects.exists()


# --- Approving and rejecting commissions ------------------------------------------------------------------------------

def test_staff_approve_a_commission_early(manager, agent, earned, affiliate):
    with pytest.raises(ServiceError):
        services.approve_commission(agent, earned)
    mail.outbox.clear()
    approved = services.approve_commission(manager, earned)
    assert approved.status == CommissionStatus.APPROVED and approved.decided_by == manager
    assert "Commission approved" in mail.outbox[0].subject
    assert Notification.objects.filter(user=affiliate.user, event="commission.approved").exists()
    with pytest.raises(ServiceError) as exc:
        services.approve_commission(manager, approved)
    assert exc.value.code == "invalid_status"


def test_rejecting_a_commission_needs_a_reason_and_only_before_payment(manager, earned):
    with pytest.raises(ServiceError) as exc:
        services.reject_commission(manager, earned, note=" ")
    assert exc.value.code == "note_required"
    rejected = services.reject_commission(manager, earned, note="Self-referral")
    assert rejected.status == CommissionStatus.REJECTED and rejected.note == "Self-referral"
    with pytest.raises(ServiceError):
        services.reject_commission(manager, rejected, note="again")


def test_the_daily_job_approves_only_what_has_finished_its_hold(manager, earned, affiliate):
    assert services.run_approvals() == {"approved": 0, "errors": []}
    later = timezone.now() + timedelta(days=15)
    services.suspend_affiliate(manager, affiliate)
    assert services.run_approvals(now=later)["approved"] == 0  # a suspended affiliate's commissions wait
    services.reactivate_affiliate(manager, reload(affiliate))
    mail.outbox.clear()
    assert services.run_approvals(now=later) == {"approved": 1, "errors": []}
    assert reload(earned).status == CommissionStatus.APPROVED and len(mail.outbox) == 1
    assert services.run_approvals(now=later)["approved"] == 0  # not twice


def test_a_refunded_commission_is_not_approved_by_the_job(manager, earned):
    refund(manager, earned.invoice)
    assert services.run_approvals(now=timezone.now() + timedelta(days=30))["approved"] == 0


# --- Payouts ---------------------------------------------------------------------------------------------------------

def test_recording_a_payout_pays_the_approved_commissions(manager, admin, earned, affiliate):
    services.approve_commission(manager, earned)
    mail.outbox.clear()
    payout = services.record_payout(manager, affiliate, method="Bank transfer", reference="TXN-42", note="September")
    assert (payout.amount, payout.currency, payout.reference, payout.recorded_by) == (D("100.00"), "USD", "TXN-42", manager)
    assert payout.paid_on == timezone.localdate() and payout.code.startswith("P")
    earned = reload(earned)
    assert earned.status == CommissionStatus.PAID and earned.payout == payout
    assert "You were paid USD 100.00" in mail.outbox[0].subject and "TXN-42" in mail.outbox[0].body
    assert services.balances(affiliate)["paid"] == D("100.00") and services.balances(affiliate)["approved"] == 0
    with pytest.raises(ServiceError) as exc:  # a repeated submit finds nothing left to pay
        services.record_payout(manager, affiliate, method="Bank transfer")
    assert exc.value.code == "nothing_to_pay"
    assert Payout.objects.count() == 1


def test_payout_rules(manager, agent, admin, earned, affiliate):
    services.approve_commission(manager, earned)
    with pytest.raises(ServiceError):
        services.record_payout(agent, affiliate, method="Bank")
    with pytest.raises(ServiceError) as exc:
        services.record_payout(manager, affiliate, method=" ")
    assert exc.value.code == "method_required"
    with pytest.raises(ServiceError) as exc:
        services.record_payout(manager, affiliate, method="Bank", paid_on=timezone.localdate() + timedelta(days=1))
    assert exc.value.code == "payout_date_invalid"
    services.save_settings(admin, minimum_payout=D("500"))
    with pytest.raises(ServiceError) as exc:
        services.record_payout(manager, affiliate, method="Bank")
    assert exc.value.code == "below_minimum"
    services.save_settings(admin, minimum_payout=D("10"))
    services.suspend_affiliate(manager, affiliate)
    with pytest.raises(ServiceError) as exc:
        services.record_payout(manager, reload(affiliate), method="Bank")
    assert exc.value.code == "affiliate_inactive"
    assert not Payout.objects.exists() and reload(earned).status == CommissionStatus.APPROVED


def test_a_payout_can_cover_chosen_commissions_only(manager, admin, referred, affiliate, tax):
    services.save_settings(admin, recurring_months=6, minimum_payout=D("10"))
    first = Commission.objects.get(invoice=pay_invoice(manager, referred[1], "500.00"))
    second = Commission.objects.get(invoice=pay_invoice(manager, referred[1], "300.00"))
    for c in (first, second):
        services.approve_commission(manager, c)
    payout = services.record_payout(manager, affiliate, method="Bank", commission_ids=[first.pk])
    assert payout.amount == D("50.00") and reload(first).status == "paid" and reload(second).status == "approved"
    with pytest.raises(ServiceError) as exc:
        services.record_payout(manager, affiliate, method="Bank", commission_ids=[first.pk, second.pk])
    assert exc.value.code == "commission_invalid"  # one of them is already paid
    assert reload(second).status == "approved"


def test_another_affiliates_commission_cannot_be_included(manager, admin, earned, affiliate, config, make_customer):
    other = services.enrol(make_customer("o@example.com")[0], accept_terms=True)
    services.save_settings(admin, minimum_payout=D("1"))
    services.approve_commission(manager, earned)
    with pytest.raises(ServiceError):
        services.record_payout(manager, other, method="Bank", commission_ids=[earned.pk])
    assert reload(earned).status == CommissionStatus.APPROVED


# --- Visibility and reports -------------------------------------------------------------------------------------------

def test_who_sees_what(earned, affiliate, affiliate_user, referred, manager, agent, admin):
    assert list(services.visible_commissions(affiliate_user)) == [earned]
    assert list(services.visible_commissions(referred[0])) == []  # the customer who paid sees nothing
    assert list(services.visible_commissions(manager)) == [earned]
    assert list(services.visible_commissions(agent)) == []  # agents do not see affiliates at all
    assert list(services.visible_affiliates(manager)) == [affiliate] and list(services.visible_affiliates(agent)) == []
    assert not services.can_view(agent) and services.can_manage(manager) and services.can_manage(admin)


def test_the_report(manager, admin, earned, affiliate):
    services.approve_commission(manager, earned)
    services.save_settings(admin, minimum_payout=D("10"))
    services.record_payout(manager, affiliate, method="Bank")
    services.count_visit(affiliate)
    data = services.report()
    assert data["affiliates"]["active"] == 1 and data["referrals"] == 1 and data["visits"] == 1
    assert data["commissions"]["paid"] == {"count": 1, "amount": D("100.00")} and data["paid_out"] == D("100.00")
    assert data["top"][0]["code"] == affiliate.code and data["top"][0]["earned"] == D("100.00")
    assert services.report(days=7)["commissions"]["paid"]["count"] == 1
    Commission.objects.update(created_at=timezone.now() - timedelta(days=40))
    assert services.report(days=30)["commissions"]["paid"]["count"] == 0


def test_affiliate_stats(affiliate, referred, earned):
    services.count_visit(affiliate)
    services.count_visit(affiliate)
    stats = services.affiliate_stats(reload(affiliate))
    assert stats == {"visits": 2, "signups": 1, "customers_who_paid": 1, "conversion": 50.0}


def test_the_role_permissions(manager, admin, agent, customer):
    assert services.can_view(manager) and services.can_view(admin)
    for user in (agent, customer):
        assert not services.can_view(user) and not services.can_manage(user)
