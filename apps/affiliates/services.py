"""
Affiliate management (roadmap Phase 13).

The life of a referral, and every rule that keeps it honest:

* **Attribution** happens once, when a customer *signs up* through an affiliate's link (or when staff fix a missed
  one). It is never changed by a later click, and only for an active affiliate and a brand-new client.
* **A commission is earned when an invoice is paid in full**, one per invoice, worked out on the invoice *excluding tax
  and after discount* and frozen at that moment. It starts **Pending** (a refund window), is **Approved** automatically
  when the hold ends (or by staff sooner), and **Paid** when staff record a payout that includes it. Staff can Reject.
* **Refunds follow the money:** a refund lowers (or voids) a commission that is not yet paid; one already paid out is
  flagged for staff, never silently taken back.
* Payouts are *recorded* by staff after they have paid the affiliate outside the portal (payments are manual).
* Affiliates see their own numbers and referred customers as anonymous labels, never names or emails.
"""
import logging
import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Sum
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from apps.accounts.roles import perm
from apps.audit import services as audit
from apps.billing import calculations as calc
from apps.billing.models import Invoice
from apps.core.exceptions import ServiceError
from apps.notifications import services as notifications
from apps.renewals.proration import add_months

from .models import (Affiliate, AffiliateSettings, AffiliateStatus, Commission, CommissionKind, CommissionStatus,
                     Payout, Referral, ReferralSource, validate_rule)

logger = logging.getLogger(__name__)

CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no look-alike characters
COOKIE_NAME = "wp_ref"


def _denied(message="You do not have permission to perform this action."):
    return ServiceError(message, code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)


def can_view(user):
    return bool(user and user.is_authenticated and user.has_perm(perm("view_affiliates")))


def can_manage(user):
    return bool(user and user.is_authenticated and user.has_perm(perm("manage_affiliates")))


def _require_manage(actor):
    if not can_manage(actor):
        raise _denied()


def _money(value, label="Amount"):
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise ValidationError(f"{label} must be a number.")
    if not amount.is_finite() or amount < 0 or calc.money(amount) != amount:
        raise ValidationError(f"{label} must be a valid amount with at most two decimal places.")
    return amount


# --- Settings ---------------------------------------------------------------------------------------------------

SETTING_FIELDS = ("enabled", "require_approval", "cookie_days", "commission_kind", "commission_value",
                  "recurring_months", "hold_days", "minimum_payout", "program_terms")


@transaction.atomic
def save_settings(actor, *, request=None, **values):
    if not actor.has_perm(perm("manage_settings")):
        raise _denied()
    row = AffiliateSettings.objects.select_for_update().get_or_create(pk=1)[0]
    before = {f: str(getattr(row, f)) for f in SETTING_FIELDS}
    for name in SETTING_FIELDS:
        if name in values:
            setattr(row, name, values[name])
    row.full_clean()
    row.save()
    changed = {f: [before[f], str(getattr(row, f))] for f in SETTING_FIELDS if before[f] != str(getattr(row, f))}
    if changed:
        audit.record("affiliate.settings_changed", actor=actor, target=row, metadata={"changes": changed},
                     request=request)
    return row


# --- Joining and managing affiliates --------------------------------------------------------------------------------

def _new_code():
    for _ in range(20):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        if not Affiliate.objects.filter(code=code).exists():
            return code
    raise ServiceError("Could not create a referral code. Please try again.", code="code_unavailable")


def clean_code(value):
    code = (value or "").strip().lower()
    if not (3 <= len(code) <= 32) or not all(c.isalnum() or c == "-" for c in code) or code.startswith("-") \
            or code.endswith("-"):
        raise ValidationError("A code is 3 to 32 letters, numbers or hyphens.")
    return code


def get_affiliate(user):
    return Affiliate.objects.filter(user=user).first() if user and user.is_authenticated else None


def enrol(user, *, accept_terms, payout_details="", request=None):
    """A signed-in person joins the programme. Active at once, or awaiting approval if the programme asks for it."""
    if not (user and user.is_authenticated):
        raise _denied()
    config = AffiliateSettings.load()
    if not config.enabled:
        raise ServiceError("The affiliate programme is not open right now.", code="program_disabled")
    if not accept_terms:
        raise ServiceError("Please accept the programme terms to join.", code="terms_required")
    payout_details = (payout_details or "").strip()
    if len(payout_details) > 1000:
        raise ValidationError("Payout details are too long.")
    with transaction.atomic():
        if Affiliate.objects.select_for_update().filter(user=user).exists():
            raise ServiceError("You are already part of the affiliate programme.", code="already_affiliate")
        try:
            with transaction.atomic():
                affiliate = Affiliate.objects.create(
                    user=user, code=_new_code(), payout_details=payout_details, terms_accepted_at=timezone.now(),
                    status=AffiliateStatus.PENDING if config.require_approval else AffiliateStatus.ACTIVE)
        except IntegrityError:  # two requests at once, or a code collision: the caller can simply retry
            raise ServiceError("You are already part of the affiliate programme.", code="already_affiliate")
        audit.record("affiliate.joined", actor=user, target=affiliate,
                     metadata={"status": affiliate.status, "code": affiliate.code}, request=request)
    if affiliate.status == AffiliateStatus.PENDING:
        link = reverse("affiliates_staff:affiliate", args=[affiliate.pk])
        notifications.notify_team("affiliate.applied", "manage_affiliates", title="New affiliate application",
                                  body=f"{user.email} wants to join the affiliate programme.", link=link)
    return affiliate


TRANSITIONS = {
    AffiliateStatus.PENDING: {AffiliateStatus.ACTIVE, AffiliateStatus.REJECTED},
    AffiliateStatus.ACTIVE: {AffiliateStatus.SUSPENDED},
    AffiliateStatus.SUSPENDED: {AffiliateStatus.ACTIVE},
    AffiliateStatus.REJECTED: {AffiliateStatus.ACTIVE},
}


def _set_status(actor, affiliate, to, *, note="", request=None):
    _require_manage(actor)
    note = (note or "").strip()[:500]
    with transaction.atomic():
        affiliate = Affiliate.objects.select_for_update().get(pk=affiliate.pk)
        if to not in TRANSITIONS.get(affiliate.status, set()):
            raise ServiceError(f"A {affiliate.get_status_display().lower()} affiliate cannot become "
                               f"{AffiliateStatus(to).label.lower()}.", code="invalid_status")
        previous, affiliate.status = affiliate.status, to
        affiliate.reviewed_by, affiliate.reviewed_at, affiliate.review_note = actor, timezone.now(), note
        affiliate.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note", "updated_at"])
        audit.record(f"affiliate.{to}", actor=actor, target=affiliate,
                     metadata={"from": previous, "to": to, "note": note}, request=request)
    return affiliate


def approve_affiliate(actor, affiliate, *, note="", request=None):
    result = _set_status(actor, affiliate, AffiliateStatus.ACTIVE, note=note, request=request)
    link = reverse("affiliates_customer:dashboard")
    notifications.dispatch("affiliate.approved", user=result.user, title="You are now an affiliate", link=link,
                           context={"affiliate": result, "link": link, "referral_url": referral_url(result)})
    return result


def reject_affiliate(actor, affiliate, *, note, request=None):
    if not (note or "").strip():
        raise ServiceError("Tell the applicant why they were not accepted.", code="note_required")
    result = _set_status(actor, affiliate, AffiliateStatus.REJECTED, note=note, request=request)
    notifications.dispatch("affiliate.rejected", user=result.user, title="Your affiliate application",
                           link=reverse("affiliates_customer:dashboard"),
                           context={"affiliate": result, "link": reverse("affiliates_customer:dashboard")})
    return result


def suspend_affiliate(actor, affiliate, *, note="", request=None):
    return _set_status(actor, affiliate, AffiliateStatus.SUSPENDED, note=note, request=request)


def reactivate_affiliate(actor, affiliate, *, note="", request=None):
    return _set_status(actor, affiliate, AffiliateStatus.ACTIVE, note=note, request=request)


@transaction.atomic
def set_override(actor, affiliate, *, kind="", value=None, request=None):
    """Staff: give this affiliate their own commission rule (blank kind = back to the programme's default)."""
    _require_manage(actor)
    affiliate = Affiliate.objects.select_for_update().get(pk=affiliate.pk)
    if kind:
        value = _money(value, "The commission")
        validate_rule(kind, value)
    else:
        kind, value = "", None
    before = [affiliate.commission_kind, str(affiliate.commission_value) if affiliate.commission_value is not None else None]
    affiliate.commission_kind, affiliate.commission_value = kind, value
    affiliate.save(update_fields=["commission_kind", "commission_value", "updated_at"])
    audit.record("affiliate.rule_changed", actor=actor, target=affiliate,
                 metadata={"from": before, "to": [kind, str(value) if value is not None else None]}, request=request)
    return affiliate


@transaction.atomic
def set_code(actor, affiliate, code, *, request=None):
    _require_manage(actor)
    code = clean_code(code)
    affiliate = Affiliate.objects.select_for_update().get(pk=affiliate.pk)
    if Affiliate.objects.filter(code=code).exclude(pk=affiliate.pk).exists():
        raise ServiceError("That code is already used by another affiliate.", code="code_taken")
    previous, affiliate.code = affiliate.code, code
    affiliate.save(update_fields=["code", "updated_at"])
    audit.record("affiliate.code_changed", actor=actor, target=affiliate, metadata={"from": previous, "to": code},
                 request=request)
    return affiliate


def update_payout_details(actor, affiliate, text, *, request=None):
    """The affiliate (or staff who manage affiliates) says where payouts should go."""
    if not (actor and actor.is_authenticated and (actor.pk == affiliate.user_id or can_manage(actor))):
        raise _denied()
    text = (text or "").strip()
    if len(text) > 1000:
        raise ValidationError("Payout details are too long.")
    affiliate.payout_details = text
    affiliate.save(update_fields=["payout_details", "updated_at"])
    audit.record("affiliate.payout_details_changed", actor=actor, target=affiliate, request=request)
    return affiliate


def referral_url(affiliate):
    return settings.SITE_URL.rstrip("/") + reverse("affiliates_public:referral", args=[affiliate.code])


# --- Links and attribution ----------------------------------------------------------------------------------------

def live_affiliate(code):
    """The active affiliate a link belongs to, if the programme is on (else None)."""
    if not AffiliateSettings.load().enabled:
        return None
    return Affiliate.objects.filter(code=(code or "").strip().lower(), status=AffiliateStatus.ACTIVE).first()


def count_visit(affiliate):
    """A visitor followed the affiliate's link. Only a count is kept: no address, no identity."""
    Affiliate.objects.filter(pk=affiliate.pk).update(visit_count=F("visit_count") + 1)


def _is_own_client(affiliate, client):
    """An affiliate is not paid for their own account (or one they are a contact of)."""
    return client.contacts.filter(user_id=affiliate.user_id).exists()


def attribute_signup(client, code, *, user=None, request=None):
    """
    A brand-new client came in with a referral code: remember the affiliate. Anything wrong with the code is simply
    ignored (a bad code must never stop someone signing up). Returns the Referral or None.
    """
    code = (code or "").strip().lower()
    if not code:
        return None
    config = AffiliateSettings.load()
    if not config.enabled:
        return None
    affiliate = Affiliate.objects.filter(code=code, status=AffiliateStatus.ACTIVE).first()
    if affiliate is None or _is_own_client(affiliate, client):
        return None
    try:
        with transaction.atomic():
            referral = Referral.objects.create(affiliate=affiliate, client=client, source=ReferralSource.LINK)
    except IntegrityError:  # already attributed
        return None
    audit.record("referral.attributed", actor=user, target=referral, metadata={"affiliate_id": affiliate.pk, "client_id": client.pk, "source": "link"},
                 request=request)
    return referral


@transaction.atomic
def attribute_client(actor, client, affiliate, *, request=None):
    """Staff: record that an existing client was referred (a missed attribution). One affiliate per client, ever."""
    _require_manage(actor)
    if not affiliate.is_active:
        raise ServiceError("Only an active affiliate can be credited with a client.", code="affiliate_inactive")
    if _is_own_client(affiliate, client):
        raise ServiceError("An affiliate cannot be credited with their own account.", code="self_referral")
    if Referral.objects.select_for_update().filter(client=client).exists():
        raise ServiceError("This client already has a referring affiliate.", code="already_referred")
    referral = Referral.objects.create(affiliate=affiliate, client=client, source=ReferralSource.STAFF,
                                       attributed_by=actor)
    audit.record("referral.attributed", actor=actor, target=referral,
                 metadata={"affiliate_id": affiliate.pk, "client_id": client.pk, "source": "staff"}, request=request)
    return referral


# --- Commission rule and the commission itself ---------------------------------------------------------------------

def rule_for(affiliate, config=None):
    """(kind, value): the affiliate's own rule if they have one, else the programme's."""
    config = config or AffiliateSettings.load()
    if affiliate.commission_kind and affiliate.commission_value is not None:
        return affiliate.commission_kind, affiliate.commission_value
    return config.commission_kind, config.commission_value


def commission_for(kind, value, base):
    """The commission on ``base``: a percentage of it, or a fixed amount (never more than the base itself)."""
    if kind == CommissionKind.PERCENTAGE:
        return calc.money(base * value / Decimal(100))
    return calc.money(min(value, base))


def _net_factor(invoice):
    """How much of what was paid is still kept (1 = nothing refunded, 0 = all refunded)."""
    if invoice.amount_paid <= 0:
        return Decimal(0)
    return max(Decimal(0), min(Decimal(1), (invoice.amount_paid - invoice.amount_refunded) / invoice.amount_paid))


def on_invoice_paid(invoice):
    """An invoice was paid in full: earn the referring affiliate their commission (once, and only if it qualifies)."""
    referral = Referral.objects.select_related("affiliate").filter(client_id=invoice.client_id).first()
    if referral is None:
        return None
    config = AffiliateSettings.load()
    affiliate = referral.affiliate
    if not config.enabled or not affiliate.is_active:
        audit.record("commission.skipped", target=invoice, metadata={"affiliate_id": affiliate.pk,
                                                                     "reason": "programme off or affiliate not active"})
        return None
    if Commission.objects.filter(invoice=invoice).exists():
        return None  # already done: a repeat of the signal changes nothing
    if config.recurring_months:
        if (invoice.paid_at or timezone.now()) > add_months(referral.created_at, config.recurring_months):
            return None
    elif Commission.objects.filter(referral=referral).exists():
        return None  # only the first paid invoice earns
    base = calc.money(invoice.subtotal - invoice.discount_total)
    if base <= 0:
        return None
    kind, value = rule_for(affiliate, config)
    amount = commission_for(kind, value, base)
    if amount <= 0:
        return None
    now = timezone.now()
    hold_over = config.hold_days == 0
    try:
        with transaction.atomic():
            commission = Commission.objects.create(
                affiliate=affiliate, referral=referral, invoice=invoice, currency=invoice.currency,
                base_amount=base, kind=kind, rate=value, original_amount=amount, amount=amount,
                eligible_at=now + timedelta(days=config.hold_days),
                status=CommissionStatus.APPROVED if hold_over else CommissionStatus.PENDING,
                decided_at=now if hold_over else None)
    except IntegrityError:
        return None
    audit.record("commission.earned", target=commission,
                 metadata={"affiliate_id": affiliate.pk, "invoice_id": invoice.pk, "amount": str(amount),
                           "kind": kind, "rate": str(value), "base": str(base)})
    return commission


def on_invoice_refunded(invoice):
    """Money was refunded on an invoice: lower or void an unpaid commission; flag one that was already paid out."""
    flagged = False
    with transaction.atomic():
        commission = Commission.objects.select_for_update().filter(invoice=invoice).first()
        if commission is None or commission.status == CommissionStatus.REJECTED:
            return None
        invoice = Invoice.objects.get(pk=invoice.pk)
        if commission.status == CommissionStatus.PAID:
            if not commission.needs_review:
                commission.needs_review = True
                commission.save(update_fields=["needs_review", "updated_at"])
                audit.record("commission.refund_after_payout", target=commission,
                             metadata={"invoice_id": invoice.pk, "refunded": str(invoice.amount_refunded)})
                flagged = True
        else:
            new_amount = calc.money(commission.original_amount * _net_factor(invoice))
            if new_amount != commission.amount:
                commission.amount = new_amount
                update = ["amount", "updated_at"]
                if new_amount <= 0:
                    commission.status, commission.note = CommissionStatus.REJECTED, "The invoice was refunded."
                    commission.decided_at = timezone.now()
                    update += ["status", "note", "decided_at"]
                commission.save(update_fields=update)
                audit.record("commission.adjusted", target=commission,
                             metadata={"invoice_id": invoice.pk, "amount": str(new_amount),
                                       "voided": new_amount <= 0})
    if flagged:
        link = reverse("affiliates_staff:commissions") + "?status=paid&review=1"
        notifications.notify_team("commission.refund_after_payout", "manage_affiliates",
                                  title=f"Refund on a commission already paid out ({commission.code})",
                                  body="The invoice was refunded after the affiliate was paid.", link=link)
    return commission


# --- Reviewing commissions ------------------------------------------------------------------------------------------

def _tell_commission_approved(commission):
    link = reverse("affiliates_customer:dashboard")
    notifications.dispatch("commission.approved", user=commission.affiliate.user, link=link,
                           title=f"Commission approved: {commission.currency} {commission.amount}",
                           context={"commission": commission, "link": link})


def approve_commission(actor, commission, *, request=None):
    """Staff: approve a pending commission now, without waiting for the hold to end."""
    _require_manage(actor)
    with transaction.atomic():
        commission = Commission.objects.select_for_update().get(pk=commission.pk)
        if commission.status != CommissionStatus.PENDING:
            raise ServiceError("Only a pending commission can be approved.", code="invalid_status")
        if commission.amount <= 0:
            raise ServiceError("Nothing is left to pay on this commission.", code="nothing_due")
        commission.status, commission.decided_by, commission.decided_at = CommissionStatus.APPROVED, actor, timezone.now()
        commission.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
        audit.record("commission.approved", actor=actor, target=commission,
                     metadata={"amount": str(commission.amount)}, request=request)
    _tell_commission_approved(commission)
    return commission


def reject_commission(actor, commission, *, note, request=None):
    """Staff: reject a pending or approved (not yet paid) commission, with a reason."""
    _require_manage(actor)
    note = (note or "").strip()
    if not note:
        raise ServiceError("Give a reason for rejecting the commission.", code="note_required")
    with transaction.atomic():
        commission = Commission.objects.select_for_update().get(pk=commission.pk)
        if commission.status not in (CommissionStatus.PENDING, CommissionStatus.APPROVED):
            raise ServiceError("Only a pending or approved commission can be rejected.", code="invalid_status")
        previous = commission.status
        commission.status, commission.note = CommissionStatus.REJECTED, note[:500]
        commission.decided_by, commission.decided_at = actor, timezone.now()
        commission.save(update_fields=["status", "note", "decided_by", "decided_at", "updated_at"])
        audit.record("commission.rejected", actor=actor, target=commission,
                     metadata={"from": previous, "note": note[:500]}, request=request)
    return commission


def run_approvals(*, now=None):
    """Daily: approve every pending commission whose hold has ended (and whose affiliate is in good standing)."""
    now = now or timezone.now()
    result = {"approved": 0, "errors": []}
    due = Commission.objects.filter(status=CommissionStatus.PENDING, eligible_at__lte=now,
                                    affiliate__status=AffiliateStatus.ACTIVE)
    for commission in due:
        try:
            with transaction.atomic():
                locked = Commission.objects.select_for_update().get(pk=commission.pk)
                if locked.status != CommissionStatus.PENDING or locked.amount <= 0:
                    continue
                locked.status, locked.decided_at = CommissionStatus.APPROVED, now
                locked.save(update_fields=["status", "decided_at", "updated_at"])
                audit.record("commission.approved", target=locked, metadata={"amount": str(locked.amount),
                                                                              "automatic": True})
            _tell_commission_approved(locked)
            result["approved"] += 1
        except Exception:  # noqa: BLE001 - one bad row must not stop the rest
            logger.exception("Approving commission %s failed", commission.pk)
            result["errors"].append(f"commission {commission.pk}")
    return result


# --- Balances and payouts ---------------------------------------------------------------------------------------------

def balances(affiliate):
    """What the affiliate has earned, by status, plus what is lifetime paid."""
    rows = {row["status"]: row for row in affiliate.commissions.values("status").annotate(
        total=Sum("amount"), count=Count("id"))}

    def total(status_):
        return calc.money(rows.get(status_, {}).get("total") or 0)

    return {"pending": total(CommissionStatus.PENDING), "approved": total(CommissionStatus.APPROVED),
            "paid": total(CommissionStatus.PAID), "rejected": total(CommissionStatus.REJECTED),
            "counts": {s: rows.get(s, {}).get("count", 0) for s in CommissionStatus.values}}


def payable_commissions(affiliate):
    return Commission.objects.filter(affiliate=affiliate, status=CommissionStatus.APPROVED, payout__isnull=True)


def record_payout(actor, affiliate, *, method, reference="", paid_on=None, note="", commission_ids=None,
                  request=None):
    """
    Staff: record that an affiliate was paid the approved commissions (all of them, or those listed). The money moved
    outside the portal; this is the record. Each commission can be in only one payout, so a repeated submit finds
    nothing left to pay.
    """
    _require_manage(actor)
    method = (method or "").strip()
    if not method:
        raise ServiceError("Say how the affiliate was paid (for example bank transfer).", code="method_required")
    paid_on = paid_on or timezone.localdate()
    if paid_on > timezone.localdate():
        raise ServiceError("The payment date cannot be in the future.", code="payout_date_invalid")
    config = AffiliateSettings.load()
    with transaction.atomic():
        affiliate = Affiliate.objects.select_for_update().get(pk=affiliate.pk)
        if affiliate.status != AffiliateStatus.ACTIVE:
            raise ServiceError("Only an active affiliate can be paid.", code="affiliate_inactive")
        queryset = Commission.objects.select_for_update().filter(
            affiliate=affiliate, status=CommissionStatus.APPROVED, payout__isnull=True)
        if commission_ids is not None:
            queryset = queryset.filter(pk__in=list(commission_ids))
        commissions = list(queryset.order_by("id"))
        if not commissions:
            raise ServiceError("There is nothing approved to pay.", code="nothing_to_pay")
        if commission_ids is not None and len(commissions) != len(set(commission_ids)):
            raise ServiceError("Some of those commissions cannot be paid out (already paid, or not approved).",
                               code="commission_invalid")
        total = calc.money(sum(c.amount for c in commissions))
        if total < config.minimum_payout:
            raise ServiceError(f"The minimum payout is {config.minimum_payout}; this comes to {total}.",
                               code="below_minimum")
        currencies = {c.currency for c in commissions}
        if len(currencies) != 1:
            raise ServiceError("These commissions are in different currencies; pay them separately.",
                               code="mixed_currency")
        payout = Payout.objects.create(affiliate=affiliate, amount=total, currency=currencies.pop(),
                                       method=method[:100], reference=(reference or "").strip()[:200],
                                       paid_on=paid_on, note=(note or "").strip()[:500], recorded_by=actor)
        for commission in commissions:
            commission.status, commission.payout = CommissionStatus.PAID, payout
            commission.save(update_fields=["status", "payout", "updated_at"])
        audit.record("payout.recorded", actor=actor, target=payout,
                     metadata={"affiliate_id": affiliate.pk, "amount": str(total), "commissions": len(commissions),
                               "reference": payout.reference}, request=request)
    link = reverse("affiliates_customer:dashboard")
    notifications.dispatch("payout.paid", user=affiliate.user, link=link,
                           title=f"You were paid {payout.currency} {payout.amount}",
                           context={"payout": payout, "link": link})
    return payout


# --- Visibility -------------------------------------------------------------------------------------------------------

def visible_affiliates(user):
    return Affiliate.objects.select_related("user") if can_view(user) else Affiliate.objects.none()


def visible_commissions(user):
    queryset = Commission.objects.select_related("affiliate__user", "invoice", "referral")
    if can_view(user):
        return queryset
    if user and user.is_authenticated:
        return queryset.filter(affiliate__user=user)
    return queryset.none()


def visible_payouts(user):
    queryset = Payout.objects.select_related("affiliate__user")
    if can_view(user):
        return queryset
    if user and user.is_authenticated:
        return queryset.filter(affiliate__user=user)
    return queryset.none()


# --- Reports --------------------------------------------------------------------------------------------------------

def affiliate_stats(affiliate):
    signups = affiliate.referrals.count()
    earning = affiliate.referrals.filter(commissions__isnull=False).distinct().count()
    return {"visits": affiliate.visit_count, "signups": signups, "customers_who_paid": earning,
            "conversion": round(100 * signups / affiliate.visit_count, 1) if affiliate.visit_count else None}


def report(*, days=None, now=None):
    """The programme at a glance (optionally only commissions earned in the last ``days`` days)."""
    now = now or timezone.now()
    commissions = Commission.objects.all()
    if days:
        commissions = commissions.filter(created_at__gte=now - timedelta(days=days))
    by_status = {row["status"]: row for row in commissions.values("status").annotate(total=Sum("amount"),
                                                                                    count=Count("id"))}
    top = (commissions.exclude(status=CommissionStatus.REJECTED).values("affiliate_id", "affiliate__code",
                                                                       "affiliate__user__email")
           .annotate(earned=Sum("amount"), sales=Count("id")).order_by("-earned")[:10])
    return {
        "affiliates": {s: Affiliate.objects.filter(status=s).count() for s in AffiliateStatus.values},
        "referrals": Referral.objects.count(),
        "visits": Affiliate.objects.aggregate(n=Sum("visit_count"))["n"] or 0,
        "commissions": {s: {"count": by_status.get(s, {}).get("count", 0),
                            "amount": calc.money(by_status.get(s, {}).get("total") or 0)}
                        for s in CommissionStatus.values},
        "paid_out": calc.money(Payout.objects.aggregate(t=Sum("amount"))["t"] or 0),
        "awaiting_review": Commission.objects.filter(needs_review=True).count(),
        "top": [{"affiliate_id": r["affiliate_id"], "code": r["affiliate__code"],
                 "email": r["affiliate__user__email"], "earned": calc.money(r["earned"]), "sales": r["sales"]}
                for r in top],
    }

