"""The report parameters, formatting and every report's numbers."""
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.affiliates import services as affiliates
from apps.billing import payments
from apps.accounts.services import register_user
from apps.billing import invoicing
from apps.billing.models import Invoice, Transaction
from apps.renewals import services as renewals
from apps.core.exceptions import ServiceError
from apps.hosting import services as hosting_services
from apps.hosting.models import HostingAccount
from apps.lifecycle import services as lifecycle
from apps.orders.models import OrderStatus
from apps.reports import builders, services
from apps.reports.definitions import ReportDef, parse_params
from apps.reports.exports import display, raw, safe_text
from apps.support import services as support
from apps.support.models import Department, Ticket

from .conftest import ago, invoice_for, make_order, new_client, pay_on

pytestmark = pytest.mark.django_db

D = Decimal
TODAY = timezone.localdate()


def run(user, slug, **params):
    return services.run(user, slug, {k: str(v) for k, v in params.items()})[2]


def by_period(result):
    return {row["period"]: row for row in result.rows}


def figure(result, label):
    return next(value for text, _, value in result.summary if text == label)


# --- Parameters ------------------------------------------------------------------------------------------------------

def definition(*uses):
    return ReportDef(slug="x", title="X", group="sales", description="", permissions=(), uses=uses, build=None)


def test_the_default_range_is_the_last_thirty_days():
    params = parse_params(definition("range", "group"), {}, today=date(2026, 9, 25))
    assert (params.start, params.end, params.group) == (date(2026, 8, 27), date(2026, 9, 25), "day")


def test_a_range_can_be_given_and_covers_the_whole_last_day():
    params = parse_params(definition("range"), {"from": "2026-09-01", "to": "2026-09-05"}, today=date(2026, 9, 25))
    assert (params.start, params.end) == (date(2026, 9, 1), date(2026, 9, 5))
    assert (params.end_at - params.start_at) == timedelta(days=5)  # both ends included


@pytest.mark.parametrize("data,message", [
    ({"from": "yesterday"}, "start date must be a date"), ({"to": "2026-13-45"}, "end date must be a date"),
    ({"from": "2026-09-10", "to": "2026-09-01"}, "must not be after"),
    ({"from": "2000-01-01", "to": "2026-09-01"}, "at most 10 years")])
def test_bad_ranges_are_refused(data, message):
    with pytest.raises(ValidationError) as exc:
        parse_params(definition("range"), data, today=date(2026, 9, 25))
    assert message in str(exc.value)


def test_grouping_and_other_parameters_are_checked():
    d = definition("range", "group", "days", "scope")
    assert parse_params(d, {"group": "MONTH", "days": "60", "scope": "hosting"}).group == "month"
    for data in ({"group": "hour"}, {"days": "0"}, {"days": "400"}, {"days": "x"}, {"scope": "everything"}):
        with pytest.raises(ValidationError):
            parse_params(d, data)
    with pytest.raises(ValidationError) as exc:  # a day-by-day report for years would be thousands of rows
        parse_params(d, {"from": "2020-01-01", "to": "2026-01-01", "group": "day"})
    assert "group by month" in str(exc.value)
    assert parse_params(d, {"from": "2020-01-01", "to": "2026-01-01", "group": "month"}).group == "month"


def test_parameters_a_report_does_not_use_are_ignored():
    params = parse_params(definition(), {"from": "garbage", "group": "nonsense", "days": "x"})
    assert params.group == "day" and params.days == 30


# --- Formatting ------------------------------------------------------------------------------------------------------

def test_values_read_well_on_screen_and_stay_numbers_in_files():
    aware = timezone.make_aware(datetime(2026, 9, 25, 14, 5))
    assert display("money", D("1234.5")) == "1,234.50" and display("int", 12345) == "12,345"
    assert display("hours", 3.5) == "3.5 h"
    assert display("date", date(2026, 9, 5)) == "05 Sep 2026" and display("datetime", aware) == "25 Sep 2026 14:05"
    assert display("money", None) == "-" and display("text", "") == "-" and display("percent", 12.34) == "12.3%"
    assert raw("money", D("1234.5")) == "1234.50" and raw("date", date(2026, 9, 5)) == "2026-09-05"
    assert raw("datetime", aware) == "2026-09-25 14:05" and raw("int", 5) == "5" and raw("money", None) == ""


@pytest.mark.parametrize("value", ["=1+1", "+cmd", "-2+3", "@SUM(A1)", "\tTAB", "\rCR"])
def test_anything_that_could_run_as_a_formula_is_neutralised(value):
    assert safe_text(value) == "'" + value
    assert raw("text", value) == "'" + value


def test_ordinary_text_is_left_alone():
    assert safe_text("Ada Lovelace") == "Ada Lovelace" and safe_text(None) == "" and safe_text("a=b") == "a=b"
    assert raw("money", D("-5")) == "-5.00"  # a negative number is a number, not text


# --- Sales -----------------------------------------------------------------------------------------------------------

def test_sales_performance_counts_orders_value_and_new_customers_per_day(manager):
    a = new_client(manager, "Ada", "ada@example.com", days_ago=2)
    b = new_client(manager, "Bob", "bob@example.com", days_ago=0)
    make_order(a, days_ago=2, total="100.00")
    make_order(a, days_ago=2, total="50.00", status=OrderStatus.CANCELLED)
    make_order(b, days_ago=0, total="200.00")
    make_order(b, days_ago=0, total="999.00", status=OrderStatus.DRAFT)  # a draft is not a sale
    make_order(b, days_ago=40, total="500.00")  # outside the period
    result = run(manager, "sales-performance", **{"from": TODAY - timedelta(days=3), "to": TODAY})
    rows = by_period(result)
    assert len(result.rows) == 4  # every day appears, quiet or not
    two_days = rows[(TODAY - timedelta(days=2)).isoformat()]
    assert (two_days["orders"], two_days["value"], two_days["lost"], two_days["customers"]) == (2, D("150.00"), 1, 1)
    today = rows[TODAY.isoformat()]
    assert (today["orders"], today["value"], today["customers"]) == (1, D("200.00"), 1)
    assert rows[(TODAY - timedelta(days=1)).isoformat()]["orders"] == 0
    assert (figure(result, "Orders placed"), figure(result, "Order value"), figure(result, "New customers")) == (
        3, D("350.00"), 2)


def test_sales_performance_groups_by_month_and_year(manager):
    client = new_client(manager, "Ada", "ada@example.com")
    make_order(client, days_ago=0, total="10.00")
    make_order(client, days_ago=40, total="20.00")
    make_order(client, days_ago=400, total="30.00")
    months = run(manager, "sales-performance", **{"from": TODAY - timedelta(days=100), "to": TODAY, "group": "month"})
    assert sum(r["orders"] for r in months.rows) == 2 and all(len(r["period"]) == 7 for r in months.rows)
    years = run(manager, "sales-performance", **{"from": TODAY - timedelta(days=500), "to": TODAY, "group": "year"})
    assert sum(r["orders"] for r in years.rows) == 3 and all(len(r["period"]) == 4 for r in years.rows)


def test_days_are_the_sites_days_not_utc_days(manager):
    client = new_client(manager, "Ada", "ada@example.com", days_ago=30)
    order = make_order(client)
    moment = datetime(2026, 9, 20, 21, 30, tzinfo=dt_timezone.utc)
    type(order).objects.filter(pk=order.pk).update(created_at=moment)  # 21:30 UTC is 02:30 next day in Karachi
    with timezone.override("Asia/Karachi"):
        result = run(manager, "sales-performance", **{"from": "2026-09-19", "to": "2026-09-22"})
    assert by_period(result)["2026-09-21"]["orders"] == 1 and by_period(result)["2026-09-20"]["orders"] == 0


def test_orders_in_another_currency_are_flagged_not_mixed_in(manager):
    client = new_client(manager, "Ada", "ada@example.com")
    make_order(client, total="100.00")
    make_order(client, total="900.00", currency="EUR")
    result = run(manager, "sales-performance")
    assert figure(result, "Order value") == D("100.00") and any("another currency" in n for n in result.notes)


def test_the_orders_report_lists_orders_with_status_totals(manager):
    client = new_client(manager, "Ada", "ada@example.com")
    make_order(client, days_ago=1, total="100.00")
    make_order(client, days_ago=1, total="40.00", status=OrderStatus.CANCELLED)
    make_order(client, days_ago=1, total="9.00", status=OrderStatus.DRAFT)
    result = run(manager, "orders")
    assert len(result.rows) == 2 and figure(result, "Orders") == 2 and figure(result, "Value") == D("140.00")
    assert figure(result, "Active") == 1 and figure(result, "Cancelled") == 1
    assert {r["client"] for r in result.rows} == {"Ada"} and result.rows[0]["date"] >= result.rows[1]["date"]


def test_the_end_date_is_included(manager):
    client = new_client(manager, "Ada", "ada@example.com")
    make_order(client, days_ago=0)
    assert len(run(manager, "orders", **{"from": TODAY, "to": TODAY}).rows) == 1
    assert len(run(manager, "orders", **{"from": TODAY - timedelta(days=5), "to": TODAY - timedelta(days=1)}).rows) == 0


def test_new_customers_show_where_they_came_from(manager, admin):
    affiliates.save_settings(admin, require_approval=False)
    owner_user = register_user(email="aff@example.com", password="Str0ng-Passw0rd!x", first_name="Aff")
    code = affiliates.enrol(owner_user, accept_terms=True).code
    register_user(email="via@example.com", password="Str0ng-Passw0rd!x", first_name="Via", referral_code=code)
    new_client(manager, "Direct", "direct@example.com")
    result = run(manager, "new-customers")
    sources = {r["email"]: r["source"] for r in result.rows}
    assert sources["via@example.com"] == f"Affiliate {code}" and sources["direct@example.com"] == "Direct"
    assert figure(result, "New customers") == 3 and figure(result, "Through an affiliate") == 1


# --- Financial -------------------------------------------------------------------------------------------------------

def test_income_by_day_uses_successful_payments_and_refunds(manager, bank):
    a = new_client(manager, "Ada", "ada@example.com")
    b = new_client(manager, "Bob", "bob@example.com")
    first = invoice_for(manager, a, "100.00")
    second = invoice_for(manager, b, "200.00")
    pay_on(manager, first, 3)
    paid = pay_on(manager, second, 1)
    payments.refund_payment(manager, paid, amount="50.00")  # a refund is dated today
    pending = invoice_for(manager, a, "77.00")
    payments.report_payment(a.contacts.get().user, pending, method=bank)  # reported, not confirmed: not income
    result = run(manager, "income", **{"from": TODAY - timedelta(days=4), "to": TODAY})
    rows = by_period(result)
    assert len(result.rows) == 5
    assert rows[(TODAY - timedelta(days=3)).isoformat()]["paid"] == D("100.00")
    assert rows[(TODAY - timedelta(days=1)).isoformat()]["paid"] == D("200.00")
    assert (rows[TODAY.isoformat()]["refunded"], rows[TODAY.isoformat()]["net"]) == (D("50.00"), D("-50.00"))
    assert (figure(result, "Received"), figure(result, "Refunded"), figure(result, "Net income")) == (
        D("300.00"), D("50.00"), D("250.00"))
    assert sum(r["payments"] for r in result.rows) == 2


def test_income_by_month_and_year_add_up_to_the_same_total(manager):
    client = new_client(manager, "Ada", "ada@example.com")
    for days, price in ((1, "10.00"), (45, "20.00"), (400, "30.00")):
        pay_on(manager, invoice_for(manager, client, price), days)
    span = {"from": TODAY - timedelta(days=500), "to": TODAY}
    totals = {g: figure(run(manager, "income", **span, group=g), "Net income") for g in ("month", "year")}
    assert totals == {"month": D("60.00"), "year": D("60.00")}
    ledger = sum(t.amount for t in Transaction.objects.all())
    assert ledger == D("60.00")  # the report matches the permanent records


def test_income_keeps_currencies_apart(manager):
    client = new_client(manager, "Ada", "ada@example.com")
    pay_on(manager, invoice_for(manager, client, "10.00"), 1)
    euro = invoice_for(manager, client, "99.00")
    Invoice.objects.filter(pk=euro.pk).update(currency="EUR")
    payments.record_payment(manager, Invoice.objects.get(pk=euro.pk), amount="99.00", occurred_at=ago(1))
    Transaction.objects.filter(invoice_id=euro.pk).update(currency="EUR")
    result = run(manager, "income", **{"from": TODAY - timedelta(days=2), "to": TODAY})
    assert figure(result, "Net income (USD)") == D("10.00") and figure(result, "Net income (EUR)") == D("99.00")
    assert {r["currency"] for r in result.rows} == {"USD", "EUR"}


def test_unpaid_invoices_are_aged(manager):
    client = new_client(manager, "Ada", "ada@example.com")
    due_dates = {"current": 5, "late10": -10, "late45": -45, "late70": -70, "late120": -120}
    invoices = {}
    for name, offset in due_dates.items():
        invoice = invoice_for(manager, client, "100.00")
        Invoice.objects.filter(pk=invoice.pk).update(due_date=TODAY + timedelta(days=offset))
        invoices[name] = invoice
    part = invoice_for(manager, client, "100.00")
    Invoice.objects.filter(pk=part.pk).update(due_date=TODAY - timedelta(days=2))
    pay_on(manager, part, 1, amount="30.00")
    pay_on(manager, invoice_for(manager, client, "55.00"), 1)  # paid: not listed
    invoicing.cancel_invoice(manager, invoice_for(manager, client, "66.00"))  # cancelled: not listed
    invoicing.create_invoice(manager, client, lines=[{"description": "d", "quantity": 1, "unit_price": "88"}])  # draft
    result = run(manager, "unpaid-invoices")
    assert len(result.rows) == 6
    assert figure(result, "Outstanding") == D("570.00") and figure(result, "Overdue") == D("470.00")
    assert figure(result, "Not yet due") == D("100.00") and figure(result, "1-30 days overdue") == D("170.00")
    assert figure(result, "31-60 days overdue") == D("100.00") and figure(result, "61-90 days overdue") == D("100.00")
    assert figure(result, "Over 90 days overdue") == D("100.00")
    partial = next(r for r in result.rows if r["status"] == "Partially paid")
    assert (partial["balance"], partial["paid"], partial["late"]) == (D("70.00"), D("30.00"), 2)
    assert [r["due"] for r in result.rows] == sorted(r["due"] for r in result.rows)  # oldest first


def test_the_transactions_report_lists_and_counts_everything(manager, bank):
    client = new_client(manager, "Ada", "ada@example.com")
    inv = invoice_for(manager, client, "100.00")
    paid = pay_on(manager, inv, 2, reference="TT-1", method=bank)
    payments.refund_payment(manager, paid, amount="10.00")
    pending = invoice_for(manager, client, "40.00")
    payments.report_payment(client.contacts.get().user, pending, method=bank)
    result = run(manager, "transactions", **{"from": TODAY - timedelta(days=3), "to": TODAY})
    assert len(result.rows) == 3 and figure(result, "Transactions") == 3
    assert (figure(result, "Payments received"), figure(result, "Refunded")) == (D("100.00"), D("10.00"))
    assert figure(result, "Awaiting confirmation") == 1
    row = next(r for r in result.rows if r["reference"] == "TT-1")
    assert (row["method"], row["type"], row["status"], row["amount"]) == (bank.name, "Payment", "Succeeded", D("100.00"))


def test_refunds_and_failed_payments(manager, bank):
    client = new_client(manager, "Ada", "ada@example.com")
    paid = pay_on(manager, invoice_for(manager, client, "100.00"), 2)
    payments.refund_payment(manager, paid, amount="25.00", reason="Goodwill")
    other = invoice_for(manager, client, "60.00")
    reported = payments.report_payment(client.contacts.get().user, other, method=bank, amount="60.00")
    payments.reject_payment(manager, reported, reason="Not received")
    result = run(manager, "refunds-disputes")
    kinds = {r["kind"]: r for r in result.rows}
    assert kinds["Refund"]["reason"] == "Goodwill" and kinds["Refund"]["amount"] == D("25.00")
    assert kinds["Failed or rejected payment"]["reason"] == "Not received"
    assert (figure(result, "Refunds"), figure(result, "Refunded"), figure(result, "Failed or rejected payments")) == (
        1, D("25.00"), 1)
    assert any("no chargeback" in n for n in result.notes)


# --- Services --------------------------------------------------------------------------------------------------------

def test_active_services_by_scope(manager, account, domain):
    both = run(manager, "active-services")
    assert (figure(both, "Active hosting"), figure(both, "Active domains")) == (1, 1) and len(both.rows) == 2
    assert [r["type"] for r in run(manager, "active-services", scope="hosting").rows] == ["Hosting"]
    only = run(manager, "active-services", scope="domain")
    assert [r["type"] for r in only.rows] == ["Domain"] and only.rows[0]["detail"] == "Auto-renews"
    hosting_services.suspend_account(manager, account, reason="Abuse")
    assert figure(run(manager, "active-services"), "Active hosting") == 0


def test_expiring_services_include_lapsed_ones_and_show_renewal_invoicing(manager, account, domain, client_obj):
    HostingAccount.objects.filter(pk=account.pk).update(expires_at=timezone.now() - timedelta(days=3))
    type(domain).objects.filter(pk=domain.pk).update(expires_at=timezone.now() + timedelta(days=20))
    result = run(manager, "expiring-services", days=30)
    assert [r["name"] for r in result.rows] == [account.domain, domain.name]  # the soonest (already past) first
    assert result.rows[0]["left"] < 0 and 19 <= result.rows[1]["left"] <= 20
    assert figure(result, "Already past their date") == 1 and figure(result, "Expiring within the period") == 1
    assert figure(result, "Without a renewal invoice") == 2
    renewals.create_hosting_renewal(manager, HostingAccount.objects.get(pk=account.pk))
    rows = {r["type"]: r for r in run(manager, "expiring-services", days=30).rows}
    assert rows["Hosting"]["invoiced"] == "Yes" and rows["Domain"]["invoiced"] == "No"
    assert [r["type"] for r in run(manager, "expiring-services", days=10).rows] == ["Hosting"]
    assert [r["type"] for r in run(manager, "expiring-services", days=30, scope="domain").rows] == ["Domain"]
    far = hosting_services.request_hosting(manager, client_obj, account.product, "far.com")
    hosting_services.complete_provisioning(manager, far)
    HostingAccount.objects.filter(pk=far.pk).update(billing_cycle="annual",
                                                    expires_at=timezone.now() + timedelta(days=100))
    assert "far.com" not in [r["name"] for r in run(manager, "expiring-services", days=30).rows]
    assert "far.com" in [r["name"] for r in run(manager, "expiring-services", days=120).rows]


def test_suspended_services_and_their_cause(manager, account, client_obj, starter):
    hosting_services.suspend_account(manager, account, reason="Abuse report")
    other = hosting_services.request_hosting(manager, client_obj, starter, "late.com")
    hosting_services.complete_provisioning(manager, other)
    HostingAccount.objects.filter(pk=other.pk).update(billing_cycle="annual",
                                                      expires_at=timezone.now() - timedelta(days=20))
    lifecycle.run_lifecycle()
    result = run(manager, "suspended-services")
    causes = {r["name"]: r["cause"] for r in result.rows}
    assert causes == {account.domain: "Suspended by staff", other.domain: "Non-payment (automatic)"}
    assert figure(result, "Suspended") == 2 and figure(result, "For non-payment") == 1


def test_cancelled_services_come_from_completed_requests(manager, account, owner):
    cr = lifecycle.approve(manager, lifecycle.request_cancellation(owner, account, reason_code="too_expensive",
                                                                    timing="immediate"))
    lifecycle.request_cancellation(owner, HostingAccount.objects.create(
        client=account.client, product=account.product, domain="pending.com", username="pend",
        status="active"), reason_code="moving", timing="end_of_term")  # still open: not a cancellation yet
    result = run(manager, "cancelled-services")
    assert [r["code"] for r in result.rows] == [cr.code] and result.rows[0]["reason"] == "Too expensive"
    assert figure(result, "Cancellations") == 1 and figure(result, "Reason: Too expensive") == 1
    assert run(manager, "cancelled-services", **{"from": TODAY - timedelta(days=90),
                                                 "to": TODAY - timedelta(days=60)}).rows == []


# --- Support ---------------------------------------------------------------------------------------------------------

def open_ticket(owner, client, subject, department="technical"):
    return support.open_ticket(owner, client, department=Department.objects.get(slug=department), subject=subject,
                               body="Help")


def test_open_tickets_summary_and_ordering(manager, agent, owner, client_obj):
    first = open_ticket(owner, client_obj, "First")
    second = open_ticket(owner, client_obj, "Second", "billing")
    resolved = open_ticket(owner, client_obj, "Done")
    support.set_status(manager, resolved, "resolved")
    support.assign(manager, first, agent)
    Ticket.objects.filter(pk=first.pk).update(created_at=ago(5))
    support.set_priority(manager, second, "urgent")
    result = run(manager, "open-tickets")
    assert [r["subject"] for r in result.rows] == ["First", "Second"]  # oldest first; resolved is not open
    assert result.rows[0]["age"] == 5 and result.rows[0]["assignee"] == agent.email
    assert result.rows[1]["assignee"] == "Unassigned"
    assert (figure(result, "Open tickets"), figure(result, "Unassigned"), figure(result, "Urgent")) == (2, 1, 1)
    assert figure(result, "Waiting for a reply from us") == 2


def test_resolved_tickets_and_time_to_resolve(manager, owner, client_obj):
    fast, slow = open_ticket(owner, client_obj, "Fast"), open_ticket(owner, client_obj, "Slow")
    for ticket, hours in ((fast, 2), (slow, 6)):
        support.set_status(manager, ticket, "resolved")
        Ticket.objects.filter(pk=ticket.pk).update(created_at=ago(hours=hours), resolved_at=timezone.now())
    result = run(manager, "resolved-tickets")
    assert sorted(r["hours"] for r in result.rows) == [2.0, 6.0]
    assert figure(result, "Resolved") == 2 and figure(result, "Average time to resolve") == 4.0
    assert run(manager, "resolved-tickets", **{"from": TODAY - timedelta(days=9), "to": TODAY - timedelta(days=5)}).rows == []


def test_department_statistics(manager, owner, client_obj):
    open_ticket(owner, client_obj, "T1")
    done = open_ticket(owner, client_obj, "T2")
    open_ticket(owner, client_obj, "B1", "billing")
    support.set_status(manager, done, "resolved")
    Ticket.objects.filter(pk=done.pk).update(created_at=ago(hours=3), resolved_at=timezone.now())
    rows = {r["department"]: r for r in run(manager, "department-stats").rows}
    technical = next(r for name, r in rows.items() if name.startswith("Technical"))
    assert (technical["opened"], technical["resolved"], technical["open"], technical["hours"]) == (2, 1, 1, 3.0)
    billing_row = next(r for name, r in rows.items() if name.startswith("Billing"))
    assert (billing_row["opened"], billing_row["resolved"], billing_row["open"], billing_row["hours"]) == (1, 0, 1, None)
    assert figure(run(manager, "department-stats"), "Open now") == 2


# --- Limits, access and the headline figures ------------------------------------------------------------------------

def test_a_long_report_is_cut_and_says_so(manager, monkeypatch):
    client = new_client(manager, "Ada", "ada@example.com")
    for _ in range(5):
        make_order(client)
    monkeypatch.setattr(builders, "MAX_ROWS", 3)
    result = run(manager, "orders")
    assert len(result.rows) == 3 and result.truncated and any("first 3" in n for n in result.notes)
    assert figure(result, "Orders") == 5  # the headline figures still count everything


def test_who_may_run_what(manager, admin, agent, customer, support_reporter):
    assert len(services.available(manager)) == len(builders.all_reports()) == 14
    assert len(services.available(admin)) == 14
    assert services.available(agent) == [] and services.available(customer) == []
    assert {d.slug for d in services.available(support_reporter)} == {"open-tickets", "resolved-tickets", "department-stats"}
    for user, slug in ((agent, "orders"), (customer, "income"), (support_reporter, "income"),
                       (support_reporter, "active-services")):
        with pytest.raises(ServiceError) as exc:
            services.run(user, slug, {})
        assert exc.value.code == "permission_denied" and exc.value.status_code == 403, (user.email, slug)
    assert services.run(support_reporter, "open-tickets", {})[2].rows == []
    with pytest.raises(ServiceError) as exc:
        services.run(manager, "no-such-report", {})
    assert exc.value.code == "report_not_found" and exc.value.status_code == 404


def test_the_headline_figures_follow_permissions(manager, agent, support_reporter, owner, client_obj, bank):
    pay_on(manager, invoice_for(manager, client_obj, "120.00"), 0)
    make_order(client_obj)
    open_ticket(owner, client_obj, "Help")
    figures = {label: value for label, _, value, _ in services.headline(manager)}
    assert figures["Income today (USD)"] == D("120.00") and figures["Income this month"] == D("120.00")
    assert figures["Orders today"] == 1 and figures["Open tickets"] == 1 and figures["Invoices overdue"] == 0
    assert figures["New customers today"] >= 1 and "Services expiring in 30 days" in figures
    assert {label for label, *_ in services.headline(support_reporter)} == {"Open tickets"}
    assert services.headline(agent) == []
