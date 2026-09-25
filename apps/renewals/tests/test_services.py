"""Renewals, upgrades, proration, failure handling and the scheduled invoicing job."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.billing import integrity, invoicing, payments
from apps.billing.models import BillingSettings, Invoice, InvoiceStatus
from apps.core.exceptions import ServiceError
from apps.domains.models import Domain
from apps.hosting.adapters.base import HostingError
from apps.hosting.models import HostingAccount, HostingStatus
from apps.products import services as product_services
from apps.renewals import services
from apps.renewals.models import ChangeKind, ChangeStatus, ServiceChange

from .conftest import pay

pytestmark = pytest.mark.django_db

D = Decimal


def reload(obj):
    obj.refresh_from_db()
    return obj


# --- The paid term ---------------------------------------------------------------------------------

def test_only_billing_managers_set_a_term(staff, account):
    start = timezone.now()
    for actor in (staff(Role.SUPPORT_AGENT),):
        with pytest.raises(ServiceError) as exc:
            services.set_hosting_term(actor, account, billing_cycle="annual", term_start=start,
                                      expires_at=start + timedelta(days=365), term_paid="1")
        assert exc.value.code == "permission_denied"


@pytest.mark.parametrize("kwargs", [
    {"billing_cycle": "one_time"}, {"billing_cycle": "custom", "custom_months": 0}, {"billing_cycle": ""},
    {"billing_cycle": "annual", "term_paid": "-1"}, {"billing_cycle": "annual", "expires_at_offset": -5},
    {"billing_cycle": "annual", "term_paid": "abc"},
])
def test_a_bad_term_is_rejected_and_nothing_changes(manager, account, kwargs):
    kwargs = dict(kwargs)
    start = timezone.now()
    offset = kwargs.pop("expires_at_offset", 365)
    kwargs.setdefault("term_paid", "10")
    before = (account.billing_cycle, account.expires_at, account.term_paid)
    with pytest.raises((ValidationError, ServiceError)):
        services.set_hosting_term(manager, account, term_start=start, expires_at=start + timedelta(days=offset),
                                  **kwargs)
    account.refresh_from_db()
    assert (account.billing_cycle, account.expires_at, account.term_paid) == before


def test_setting_a_term_is_audited(manager, account):
    event = AuditEvent.objects.filter(action="hosting.term_set", target_id=str(account.pk)).get()
    assert event.metadata["billing_cycle"] == "annual" and event.metadata["term_paid"] == "100.00"


# --- Hosting renewal ---------------------------------------------------------------------------------

def test_renewal_invoice_uses_the_current_catalogue_price(manager, owner, account, starter, tax):
    product_services.set_price(manager, starter, billing_cycle="annual", price="120.00", setup_fee="25.00")
    mail.outbox.clear()
    change = services.create_hosting_renewal(owner, account)
    invoice = change.invoice
    assert change.kind == ChangeKind.RENEWAL and change.status == ChangeStatus.PENDING
    assert invoice.status == InvoiceStatus.UNPAID
    assert [(i.description[:24], i.amount) for i in invoice.items.all()] == [("Renewal: Starter (Annual", D("120.00"))]
    assert invoice.subtotal == D("120.00") and invoice.tax_total == D("12.00")  # no setup fee, tax on top
    assert change.paid_value == D("120.00") and change.period_months == 12
    assert "example.com" in invoice.items.get().description
    assert len(mail.outbox) == 1 and invoice.number in mail.outbox[0].subject
    assert integrity.verify_all() == []


def test_the_renewal_price_is_fixed_once_invoiced(manager, owner, account, starter):
    change = services.create_hosting_renewal(owner, account)
    product_services.set_price(manager, starter, billing_cycle="annual", price="999.00")
    assert reload(change.invoice).total == D("100.00")
    pay(manager, change.invoice)
    assert reload(change).status == ChangeStatus.APPLIED


def test_asking_twice_gives_the_same_invoice(owner, account):
    first = services.create_hosting_renewal(owner, account)
    second = services.create_hosting_renewal(owner, account)
    assert first.pk == second.pk and Invoice.objects.count() == 1


def test_paying_extends_the_paid_period_from_the_old_expiry(manager, owner, account):
    old_expiry, old_start = account.expires_at, account.term_start
    change = services.create_hosting_renewal(owner, account)
    pay(manager, change.invoice)
    reload(account)
    assert account.expires_at == services.add_months(old_expiry, 12)  # no time lost by renewing early
    assert account.term_start == old_start and account.term_paid == D("200.00")
    change.refresh_from_db()
    assert change.status == ChangeStatus.APPLIED and change.applied_at
    assert AuditEvent.objects.filter(action="service.renewed", target_id=str(account.pk)).exists()
    assert integrity.verify_all() == []


def test_renewing_an_expired_term_starts_afresh_from_payment(manager, owner, account):
    HostingAccount.objects.filter(pk=account.pk).update(expires_at=timezone.now() - timedelta(days=20))
    reload(account)
    change = services.create_hosting_renewal(owner, account)
    before = timezone.now()
    pay(manager, change.invoice)
    reload(account)
    assert account.term_start >= before and account.term_paid == D("100.00")  # no back-billing, no old value
    assert account.expires_at == services.add_months(account.term_start, 12)


def test_a_partial_payment_applies_nothing_until_it_is_paid_in_full(manager, owner, account):
    change = services.create_hosting_renewal(owner, account)
    expiry = account.expires_at
    pay(manager, change.invoice, "40.00")
    assert reload(change).status == ChangeStatus.PENDING and reload(account).expires_at == expiry
    pay(manager, change.invoice, "60.00")
    assert reload(change).status == ChangeStatus.APPLIED


def test_applying_twice_never_extends_twice(manager, owner, account):
    change = services.create_hosting_renewal(owner, account)
    pay(manager, change.invoice)
    once = reload(account).expires_at
    services.apply_paid_invoice(change.invoice)  # the paid signal delivered again
    assert reload(account).expires_at == once


def test_cancelling_the_invoice_voids_the_change_and_allows_a_new_one(manager, owner, account):
    change = services.create_hosting_renewal(owner, account)
    invoicing.cancel_invoice(manager, change.invoice)
    assert reload(change).status == ChangeStatus.VOID
    again = services.create_hosting_renewal(owner, account)
    assert again.pk != change.pk and again.status == ChangeStatus.PENDING


def test_who_may_renew(customer, owner, staff, account):
    with pytest.raises(ServiceError) as exc:
        services.create_hosting_renewal(customer, account)  # not a contact of the client
    assert exc.value.code == "permission_denied"
    assert services.create_hosting_renewal(owner, account)


def test_an_account_without_a_term_cannot_be_renewed_online(manager, owner, client_obj, starter):
    from apps.hosting import services as hosting

    bare = hosting.request_hosting(manager, client_obj, starter, "bare.com")
    hosting.complete_provisioning(manager, bare)
    with pytest.raises(ServiceError) as exc:
        services.create_hosting_renewal(owner, bare)
    assert exc.value.code == "term_not_set"


@pytest.mark.parametrize("status", [HostingStatus.TERMINATED, HostingStatus.CANCELLED, HostingStatus.PENDING])
def test_only_live_accounts_can_be_renewed(owner, account, status):
    HostingAccount.objects.filter(pk=account.pk).update(status=status)
    with pytest.raises(ServiceError) as exc:
        services.create_hosting_renewal(owner, reload(account))
    assert exc.value.code == "invalid_status"


def test_a_suspended_account_can_be_renewed(manager, owner, account):
    HostingAccount.objects.filter(pk=account.pk).update(status=HostingStatus.SUSPENDED)
    change = services.create_hosting_renewal(owner, reload(account))
    pay(manager, change.invoice)
    assert reload(change).status == ChangeStatus.APPLIED


def test_a_retired_price_blocks_the_renewal_cleanly(manager, owner, account, starter):
    price = starter.prices.get(billing_cycle="annual")
    product_services.set_price_active(manager, starter, price, False)
    with pytest.raises(ServiceError) as exc:
        services.create_hosting_renewal(owner, account)
    assert exc.value.code == "price_not_found" and not Invoice.objects.exists()


def test_a_free_renewal_is_settled_and_applied_at_once(manager, owner, account, starter):
    product_services.set_price(manager, starter, billing_cycle="annual", price="0.00")
    expiry = account.expires_at
    change = services.create_hosting_renewal(owner, account)
    assert reload(change.invoice).status == InvoiceStatus.PAID
    assert reload(change).status == ChangeStatus.APPLIED
    assert reload(account).expires_at == services.add_months(expiry, 12)


# --- Domain renewal ------------------------------------------------------------------------------------

def test_domain_renewal_invoice_and_payment(manager, owner, domain, tax):
    before = domain.expires_at
    change = services.create_domain_renewal(owner, domain, 2)
    invoice = change.invoice
    assert invoice.subtotal == D("28.00") and invoice.tax_total == D("2.80")
    assert "2 years" in invoice.items.get().description
    pay(manager, invoice)
    reload(domain)
    assert domain.expires_at > before + timedelta(days=700)
    assert reload(change).status == ChangeStatus.APPLIED
    assert integrity.verify_all() == []


@pytest.mark.parametrize("years", [0, -1, 11, 100, "2", None, 1.5])
def test_domain_renewal_term_is_validated(owner, domain, years):
    with pytest.raises(ServiceError) as exc:
        services.create_domain_renewal(owner, domain, years)
    assert exc.value.code == "invalid_term"


def test_domain_renewal_rules(manager, customer, owner, domain):
    with pytest.raises(ServiceError):
        services.create_domain_renewal(customer, domain, 1)
    first = services.create_domain_renewal(owner, domain, 1)
    assert services.create_domain_renewal(owner, domain, 1).pk == first.pk
    with pytest.raises(ServiceError) as exc:
        services.create_domain_renewal(owner, domain, 2)  # a different term while one is unpaid
    assert exc.value.code == "change_pending"
    Domain.objects.filter(pk=domain.pk).update(status="cancelled")
    invoicing.cancel_invoice(manager, first.invoice)
    with pytest.raises(ServiceError) as exc:
        services.create_domain_renewal(owner, reload(domain), 1)
    assert exc.value.code == "invalid_status"


def test_a_registrar_failure_leaves_a_paid_invoice_and_a_failed_change_that_can_be_retried(
        manager, owner, domain, monkeypatch):
    from apps.domains.adapters import manual
    from apps.domains.adapters.base import RegistrarError

    change = services.create_domain_renewal(owner, domain, 1)
    expiry = domain.expires_at
    original = manual.ManualAdapter.renew_domain

    def broken(self, *args, **kwargs):
        raise RegistrarError("registry offline")

    monkeypatch.setattr(manual.ManualAdapter, "renew_domain", broken)
    pay(manager, change.invoice)
    change.refresh_from_db()
    assert change.invoice.status == InvoiceStatus.PAID  # the money stands
    assert change.status == ChangeStatus.FAILED and "registry offline" in change.error
    assert reload(domain).expires_at == expiry
    assert AuditEvent.objects.filter(action="service.change_failed").exists()

    with pytest.raises(ServiceError):
        services.retry_change(manager, change)  # still broken
    monkeypatch.setattr(manual.ManualAdapter, "renew_domain", original)
    services.retry_change(manager, change)
    assert reload(change).status == ChangeStatus.APPLIED and reload(domain).expires_at > expiry
    with pytest.raises(ServiceError):
        services.retry_change(manager, change)  # nothing left to retry


# --- Upgrades ---------------------------------------------------------------------------------------------

def test_the_upgrade_calculation(account, pro):
    preview = services.preview_upgrade(account, pro)
    assert (preview.credit.term_days, preview.credit.remaining_days) == (365, 265)
    assert preview.credit.amount == D("72.60") and preview.applied_credit == D("72.60")
    assert preview.new_price == D("200.00") and preview.net_payable == D("127.40")
    assert preview.forfeited == 0 and preview.months == 12
    assert "265 days remaining" in preview.explanation and "127.40" in preview.explanation


def test_the_upgrade_invoice_shows_its_calculation(owner, account, pro, tax):
    change = services.create_upgrade(owner, account, pro)
    invoice = change.invoice
    assert invoice.subtotal == D("200.00") and invoice.discount_total == D("72.60")
    assert invoice.discount_label == "Credit for unused time on Starter (265 of 365 days)"
    assert invoice.tax_total == D("12.74") and invoice.total == D("140.14")  # 10% of the net 127.40
    assert "72.60" in invoice.notes and "127.40" in invoice.notes
    assert "Upgrade to Pro" in invoice.items.get().description
    assert change.paid_value == D("200.00") and change.calculation["credit"] == "72.60"
    assert integrity.verify_all() == []


def test_paying_an_upgrade_changes_the_plan_and_starts_a_new_term(manager, owner, account, pro):
    change = services.create_upgrade(owner, account, pro)
    before = timezone.now()
    pay(manager, change.invoice)
    reload(account)
    assert account.product == pro and account.package_name == "pro_pkg"
    assert account.term_start >= before and account.expires_at == services.add_months(account.term_start, 12)
    assert account.term_paid == D("200.00")  # the new term's valid paid value
    assert reload(change).status == ChangeStatus.APPLIED
    assert AuditEvent.objects.filter(action="service.upgraded").exists()
    assert integrity.verify_all() == []


def test_an_expired_plan_gets_no_credit(owner, account, pro):
    HostingAccount.objects.filter(pk=account.pk).update(expires_at=timezone.now() - timedelta(days=3))
    preview = services.preview_upgrade(reload(account), pro)
    assert preview.credit.amount == 0 and preview.net_payable == D("200.00")
    change = services.create_upgrade(owner, reload(account), pro)
    assert change.invoice.discount_total == 0 and change.invoice.total == D("200.00")


def test_credit_can_never_exceed_what_was_paid(manager, owner, account, pro):
    services.set_hosting_term(manager, account, billing_cycle="annual", term_start=account.term_start,
                              expires_at=account.expires_at, term_paid="5.00")  # a big discount was paid
    preview = services.preview_upgrade(reload(account), pro)
    assert preview.credit.amount <= D("5.00") and preview.net_payable >= D("195.00")


def test_credit_beyond_the_new_price_is_forfeited_and_says_so(manager, owner, account, pro):
    services.set_hosting_term(manager, account, billing_cycle="annual", term_start=account.term_start,
                              expires_at=account.expires_at, term_paid="500.00")
    preview = services.preview_upgrade(reload(account), pro)
    assert preview.credit.amount == D("363.01")  # 500.00 x 265/365, more than the new plan costs
    assert preview.applied_credit == D("200.00") and preview.net_payable == 0 and preview.forfeited > 0
    assert "not carried forward" in preview.explanation
    change = services.create_upgrade(owner, reload(account), pro)
    assert reload(change.invoice).status == InvoiceStatus.PAID  # nothing left to pay: applied straight away
    assert reload(change).status == ChangeStatus.APPLIED and reload(account).product == pro
    assert integrity.verify_all() == []


@pytest.mark.parametrize("case,code", [("same", "same_plan"), ("basic", "downgrade_not_allowed"),
                                       ("hidden", "product_unavailable"), ("no_package", "product_unavailable"),
                                       ("other_server", "server_mismatch"), ("no_cycle", "price_not_found")])
def test_upgrades_that_are_not_allowed(manager, owner, account, starter, pro, basic, server, case, code):
    target = {"same": starter, "basic": basic, "hidden": pro, "no_package": pro, "other_server": pro,
              "no_cycle": pro}[case]
    if case == "hidden":
        product_services.set_product_status(manager, pro, "hidden")
    elif case == "no_package":
        pro.whm_package_name = ""
        pro.save()
    elif case == "other_server":
        other = product_services.create_server(manager, {"name": "srv2", "hostname": "srv2.example.com"})
        product_services.set_product_servers(manager, pro, [other.pk])
    elif case == "no_cycle":
        product_services.set_price_active(manager, pro, pro.prices.get(billing_cycle="annual"), False)
    with pytest.raises(ServiceError) as exc:
        services.create_upgrade(owner, account, target)
    assert exc.value.code == code and not Invoice.objects.exists()


def test_only_an_active_account_can_be_upgraded(owner, account, pro):
    HostingAccount.objects.filter(pk=account.pk).update(status=HostingStatus.SUSPENDED)
    with pytest.raises(ServiceError) as exc:
        services.create_upgrade(owner, reload(account), pro)
    assert exc.value.code == "invalid_status"


def test_upgrade_permissions(customer, account, pro):
    with pytest.raises(ServiceError) as exc:
        services.create_upgrade(customer, account, pro)
    assert exc.value.code == "permission_denied"


def test_one_open_change_at_a_time(manager, owner, account, pro):
    renewal = services.create_hosting_renewal(owner, account)
    with pytest.raises(ServiceError) as exc:
        services.create_upgrade(owner, account, pro)
    assert exc.value.code == "change_pending" and renewal.invoice.number in exc.value.message
    invoicing.cancel_invoice(manager, renewal.invoice)
    upgrade = services.create_upgrade(owner, account, pro)
    assert services.create_upgrade(owner, account, pro).pk == upgrade.pk  # idempotent for the same plan
    with pytest.raises(ServiceError):
        services.create_hosting_renewal(owner, account)


def test_the_credit_is_frozen_at_invoicing_time(manager, owner, account, pro):
    change = services.create_upgrade(owner, account, pro)
    invoice = change.invoice
    HostingAccount.objects.filter(pk=account.pk).update(term_paid=D("9999.00"))  # later tampering with the term
    pay(manager, invoice)
    assert reload(change).status == ChangeStatus.APPLIED
    assert reload(account).term_paid == D("200.00")  # the frozen figure, not the tampered one
    assert reload(invoice).discount_total == D("72.60")  # the invoice never changes


def test_a_service_that_changed_after_invoicing_is_not_upgraded_and_needs_attention(manager, owner, account, pro):
    change = services.create_upgrade(owner, account, pro)
    HostingAccount.objects.filter(pk=account.pk).update(expires_at=account.expires_at + timedelta(days=30))
    pay(manager, change.invoice)
    change.refresh_from_db()
    assert change.status == ChangeStatus.FAILED and "changed after this invoice" in change.error
    assert reload(account).product.name == "Starter"  # nothing applied
    with pytest.raises(ServiceError):
        services.retry_change(manager, change)  # still stale
    services.dismiss_change(manager, change, note="Refunded by hand")
    assert reload(change).status == ChangeStatus.VOID
    with pytest.raises(ServiceError):
        services.dismiss_change(manager, change)


def test_a_whm_failure_leaves_the_payment_and_a_retryable_change(manager, owner, account, pro, monkeypatch):
    from apps.hosting.adapters import manual as manual_adapter

    change = services.create_upgrade(owner, account, pro)

    def broken(self, username, package):
        raise HostingError("WHM unreachable")

    original = manual_adapter.ManualAdapter.change_package
    monkeypatch.setattr(manual_adapter.ManualAdapter, "change_package", broken)
    pay(manager, change.invoice)
    change.refresh_from_db()
    assert change.invoice.status == InvoiceStatus.PAID and change.status == ChangeStatus.FAILED
    assert "WHM unreachable" in change.error and reload(account).product.name == "Starter"

    monkeypatch.setattr(manual_adapter.ManualAdapter, "change_package", original)
    services.retry_change(manager, change)
    assert reload(change).status == ChangeStatus.APPLIED and reload(account).product == pro
    assert reload(account).last_error == ""


def test_retrying_needs_billing_permission(staff, manager, owner, account, pro, monkeypatch):
    change = services.create_upgrade(owner, account, pro)
    ServiceChange.objects.filter(pk=change.pk).update(status=ChangeStatus.FAILED)
    with pytest.raises(ServiceError) as exc:
        services.retry_change(staff(Role.SUPPORT_AGENT), change)
    assert exc.value.code == "permission_denied"


# --- The scheduled job --------------------------------------------------------------------------------------

def test_the_job_invoices_only_what_is_due_and_only_once(manager, account, domain, client_obj, starter, tax):
    far = timezone.now() + timedelta(days=200)
    Domain.objects.filter(pk=domain.pk).update(expires_at=far)
    result = services.generate_renewal_invoices()
    assert result == {"hosting": 0, "domains": 0, "errors": []}  # nothing expires within 14 days

    HostingAccount.objects.filter(pk=account.pk).update(expires_at=timezone.now() + timedelta(days=10))
    Domain.objects.filter(pk=domain.pk).update(expires_at=timezone.now() + timedelta(days=5))
    mail.outbox.clear()
    result = services.generate_renewal_invoices()
    assert (result["hosting"], result["domains"], result["errors"]) == (1, 1, [])
    assert ServiceChange.objects.filter(status="pending").count() == 2 and len(mail.outbox) == 2
    again = services.generate_renewal_invoices()
    assert (again["hosting"], again["domains"]) == (0, 0)  # safe to run every day
    assert Invoice.objects.count() == 2
    assert integrity.verify_all() == []


def test_the_job_respects_the_lead_time_setting_and_auto_renew(manager, account, domain):
    HostingAccount.objects.filter(pk=account.pk).update(expires_at=timezone.now() + timedelta(days=30))
    Domain.objects.filter(pk=domain.pk).update(expires_at=timezone.now() + timedelta(days=30), auto_renew=False)
    assert services.generate_renewal_invoices()["hosting"] == 0
    row = BillingSettings.load()
    row.renewal_invoice_days = 45
    row.save()
    result = services.generate_renewal_invoices()
    assert (result["hosting"], result["domains"]) == (1, 0)  # auto-renew off: reminders only, no invoice


def test_the_job_skips_unsuitable_services_and_survives_errors(manager, account, domain, starter):
    HostingAccount.objects.filter(pk=account.pk).update(expires_at=timezone.now() + timedelta(days=3))
    product_services.set_price_active(manager, starter, starter.prices.get(billing_cycle="annual"), False)
    Domain.objects.filter(pk=domain.pk).update(expires_at=timezone.now() + timedelta(days=3))
    result = services.generate_renewal_invoices()
    assert result["hosting"] == 0 and result["domains"] == 1  # the hosting error did not stop the domain
    assert len(result["errors"]) == 1 and "hosting" in result["errors"][0]


def test_the_job_ignores_accounts_without_a_term_or_that_are_not_active(manager, account, client_obj, starter):
    from apps.hosting import services as hosting

    bare = hosting.request_hosting(manager, client_obj, starter, "bare.com")
    hosting.complete_provisioning(manager, bare)
    HostingAccount.objects.filter(pk=bare.pk).update(expires_at=timezone.now() + timedelta(days=1))
    HostingAccount.objects.filter(pk=account.pk).update(status=HostingStatus.SUSPENDED,
                                                         expires_at=timezone.now() + timedelta(days=1))
    assert services.generate_renewal_invoices()["hosting"] == 0


def test_the_command_and_celery_task(manager, account, capsys):
    from io import StringIO

    from django.core.management import call_command

    from apps.renewals import tasks

    HostingAccount.objects.filter(pk=account.pk).update(expires_at=timezone.now() + timedelta(days=2))
    out = StringIO()
    call_command("generate_renewal_invoices", stdout=out)
    assert "Hosting renewal invoices: 1" in out.getvalue()
    assert tasks.generate_renewal_invoices_task() == {"hosting": 0, "domains": 0, "errors": 0}


# --- Integrity ---------------------------------------------------------------------------------------------------

def test_verify_billing_rechecks_the_credit_and_catches_tampering(manager, owner, account, pro):
    change = services.create_upgrade(owner, account, pro)
    assert integrity.verify_all() == []
    calculation = dict(change.calculation, credit="90.00", applied_credit="90.00", net_payable="110.00",
                       forfeited="0.00")
    ServiceChange.objects.filter(pk=change.pk).update(calculation=calculation)
    problems = integrity.verify_all()
    assert any("does not follow from its inputs" in p for p in problems)
    ServiceChange.objects.filter(pk=change.pk).update(calculation=dict(change.calculation, term_paid="10.00"))
    assert any("exceeds the valid paid value" in p for p in integrity.verify_all())


def test_verify_flags_a_paid_invoice_whose_change_was_never_applied(manager, owner, account):
    change = services.create_hosting_renewal(owner, account)
    pay(manager, change.invoice)
    ServiceChange.objects.filter(pk=change.pk).update(status=ChangeStatus.PENDING)
    assert any("never applied" in p for p in integrity.verify_all())


def test_the_billing_settings_page_field_is_validated(manager):
    from django.core.exceptions import ValidationError as VE

    with pytest.raises(VE):
        invoicing.save_billing_settings(manager, {"renewal_invoice_days": 91})
