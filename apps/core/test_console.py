"""Phase D4a: the staff dashboard and its widgets, global search, and the audit log viewer."""
from datetime import timedelta
from decimal import Decimal
import re


import pytest
from django.test import Client as HttpClient
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.audit.services import record
from apps.billing.models import OPEN_STATUSES, Invoice
from apps.console import search as search_module
from apps.console import widgets
from apps.core import navigation

pytestmark = pytest.mark.django_db

ALL_KEYS = ["summary", "attention", "orders", "billing", "support", "failures", "domains", "renewals", "health", "activity"]
TOP = ["summary", "attention"]
EXPECTED = {  # who sees which widgets (the roles' permissions decide)
    "support agent": TOP + ["orders", "billing", "support", "failures", "domains", "renewals"],
    "technical staff": TOP + ["orders", "support", "failures", "domains", "renewals"],
    "manager": TOP + ["orders", "billing", "support", "failures", "domains", "renewals", "activity"],
    "admin": ALL_KEYS,
    "super admin": ALL_KEYS,
}


def browser(user=None):
    client = HttpClient(HTTP_HOST="localhost", raise_request_exception=False)
    if user is not None:
        client.force_login(user)
    return client


# --- Getting in -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("role", list(EXPECTED))
def test_staff_land_on_the_dashboard_from_the_front_door(world, role):
    assert browser(world.people[role]).get("/").headers["Location"] == reverse("console:dashboard")


def test_the_dashboard_is_for_staff_only(world):
    assert browser().get(reverse("console:dashboard")).headers["Location"].startswith("/account/login/?next=")
    for role in ("customer owner", "billing contact", "technical contact"):
        assert browser(world.people[role]).get(reverse("console:dashboard")).status_code == 403, role
        assert browser(world.people[role]).get(reverse("console:search"), {"q": "ada"}).status_code == 403, role
        assert browser(world.people[role]).get(reverse("console:widget", args=["billing"])).status_code == 403, role


@pytest.mark.parametrize("role", list(EXPECTED))
def test_each_role_sees_exactly_the_widgets_its_permissions_allow(world, role):
    user = world.people[role]
    assert [w.key for w in widgets.visible_widgets(user)] == EXPECTED[role]
    page = browser(user).get(reverse("console:dashboard")).content.decode()
    for key in ALL_KEYS:
        assert (reverse("console:widget", args=[key]) in page) == (key in EXPECTED[role]), (role, key)


def test_each_widget_is_loaded_on_its_own_with_a_no_script_fallback(world):
    page = browser(world.people["admin"]).get(reverse("console:dashboard")).content.decode()
    assert page.count('hx-trigger="load"') == 10 and page.count("<noscript>") == 10 and page.count("<h1") == 1
    assert page.count("data-widget-body") == 10 and page.count("skeleton") >= 10  # a loading state, not a blank


def test_the_dashboard_offers_shortcuts_only_for_what_the_person_may_do(world):
    def labels(role):
        return [label for label, _name in browser(world.people[role]).get(reverse("console:dashboard")).context["shortcuts"]]

    assert labels("manager") == ["New client", "New invoice", "New order", "Open a ticket"]
    assert labels("support agent") == ["Open a ticket"]


@pytest.mark.parametrize("role", list(EXPECTED))
def test_every_permitted_widget_opens_and_a_forbidden_or_unknown_one_does_not(world, role):
    client = browser(world.people[role])
    for key in ALL_KEYS:
        status = client.get(reverse("console:widget", args=[key])).status_code
        assert status == (200 if key in EXPECTED[role] else 403), (role, key, status)
    assert client.get(reverse("console:widget", args=["nonsense"])).status_code == 404


# --- The figures ------------------------------------------------------------------------------------------------------

def widget_context(world, key, role="admin"):
    return browser(world.people[role]).get(reverse("console:widget", args=[key])).context


def test_billing_figures_match_the_ledger(world):
    from apps.billing.models import Transaction

    context = widget_context(world, "billing")
    received = sum(t.amount for t in Transaction.objects.filter(status="succeeded", type="payment"))
    refunded = sum(t.amount for t in Transaction.objects.filter(status="succeeded", type="refund"))
    owed = sum(i.balance_due for i in Invoice.objects.filter(status__in=OPEN_STATUSES))
    assert context["today"] == context["month"] == context["year"] == received - refunded == Decimal("200.00")
    assert context["owed"] == owed and context["open"] == Invoice.objects.filter(status__in=OPEN_STATUSES).count()
    assert context["overdue"] == 1 and context["waiting"] == 1  # one reported offline payment awaits confirmation


def test_income_counts_only_the_period_and_only_money_that_arrived(world):
    from apps.billing.models import Transaction

    old = Transaction.objects.filter(status="succeeded", type="payment").order_by("id").first()
    Transaction.objects.filter(pk=old.pk).update(occurred_at=timezone.now() - timedelta(days=400))
    context = widget_context(world, "billing")
    assert context["today"] == Decimal("200.00") - old.amount == context["year"] == context["month"]
    Transaction.objects.filter(pk=old.pk).update(occurred_at=timezone.now())


def test_order_counts_equal_the_lists_they_link_to(world):
    from apps.orders import lifecycle
    from apps.orders.models import Order

    context = widget_context(world, "orders")
    for group, statuses in lifecycle.GROUPS.items():
        assert context["counts"][group] == Order.objects.filter(status__in=statuses).count()
        response = browser(world.people["admin"]).get(reverse("orders_staff:list"), {"group": group})
        assert response.context["page"].paginator.count == context["counts"][group]
    ids = [o.pk for o in context["recent"]]
    assert len(ids) == 5 and ids == sorted(ids, reverse=True)


def test_support_figures_equal_the_overview_and_the_filters_they_link_to(world):
    from apps.support import services as support

    context = widget_context(world, "support")
    overview = support.overview(world.people["admin"])["counts"]
    assert {k: v for k, v in context["counts"].items() if k != "customer_reply"} == overview
    client = browser(world.people["admin"])
    page = client.get(reverse("console:widget", args=["support"])).content.decode()
    tiles = {"active": {"status": "active"}, "customer_reply": {"status": "customer_reply"},
             "unassigned": {"status": "active", "assigned": "none"}, "urgent": {"status": "active", "priority": "urgent"}}
    for key, query in tiles.items():
        rows = client.get(reverse("support_staff:tickets"), query).context["page"].paginator.count
        assert rows == context["counts"][key], (key, rows, context["counts"][key])  # a tile equals the list it opens
    assert "status=active&amp;assigned=none" in page and "status=customer_reply" in page


def test_failures_lists_failed_orders_and_hosting_with_working_links(world):
    context = widget_context(world, "failures")
    assert context["order_count"] == 1 and context["hosting_count"] == 1
    client = browser(world.people["admin"])
    page = client.get(reverse("console:widget", args=["failures"])).content.decode()
    for order in context["orders"]:
        assert reverse("orders_staff:detail", args=[order.pk]) in page
        assert client.get(reverse("orders_staff:detail", args=[order.pk])).status_code == 200
    for account in context["hosting"]:
        assert client.get(reverse("hosting_staff:detail", args=[account.pk])).status_code == 200


def test_domains_expiring_soon_include_overdue_ones_and_leave_out_the_far_future(world):
    from apps.domains.models import Domain

    active = world.objects["domains"]["active"]
    now = timezone.now()
    Domain.objects.filter(pk=active.pk).update(expires_at=now + timedelta(days=10))
    assert [d.pk for d in widget_context(world, "domains")["domains"]] == [active.pk]
    Domain.objects.filter(pk=active.pk).update(expires_at=now + timedelta(days=90))
    assert widget_context(world, "domains")["domains"] == []
    Domain.objects.filter(pk=active.pk).update(expires_at=now - timedelta(days=3))
    page = browser(world.people["admin"]).get(reverse("console:widget", args=["domains"])).content.decode()
    assert "is-bad" in page and active.name in page  # already past its date: shown, in red


# --- The summary strip and the attention list (D7) -----------------------------------------------------------------

def tiles(world, role):
    return {t["label"]: t for t in widget_context(world, "summary", role)["tiles"]}


def test_each_summary_tile_is_offered_only_for_areas_the_role_may_open(world):
    assert set(tiles(world, "admin")) == {"Active clients", "Active services", "Pending orders", "Unpaid invoices",
                                          "Income this month", "Open tickets", "Failed orders"}
    assert set(tiles(world, "technical staff")) == {"Active clients", "Active services", "Pending orders", "Open tickets",
                                                    "Failed orders"}  # no money
    assert "Unpaid invoices" in tiles(world, "support agent") and "Income this month" in tiles(world, "manager")


def test_a_summary_tile_equals_the_list_it_opens(world):
    """The rule since D4a: a number on the dashboard is the number of rows behind its link."""
    client = browser(world.people["admin"])
    for label, t in tiles(world, "admin").items():
        if t.get("money"):
            continue  # money tiles are sums; their counts are the sub-line and are covered by the billing tests
        response = client.get(t["url"])
        assert response.status_code == 200, label
        assert response.context["page"].paginator.count == t["value"], (label, t["value"], t["url"])


def test_the_money_tiles_match_the_billing_widget(world):
    context = widget_context(world, "billing")
    t = tiles(world, "admin")
    assert t["Unpaid invoices"]["value"] == context["owed"] and t["Income this month"]["value"] == context["month"]
    assert f"{context['open']} open" in t["Unpaid invoices"]["sub"] and t["Unpaid invoices"]["bad"] is True  # one is overdue


def test_the_attention_list_names_what_is_wrong_worst_first_and_links_to_the_list(world):
    rows = widget_context(world, "attention")["rows"]
    titles = [r["title"] for r in rows]
    assert "Provisioning failed" in titles and "Overdue invoices" in titles and "Payments to confirm" in titles
    severities = [r["severity"] for r in rows]
    assert severities == sorted(severities, key=lambda s: {"critical": 0, "warning": 1}[s])  # critical first
    client = browser(world.people["admin"])
    for r in rows:
        assert client.get(r["url"]).status_code == 200, r["title"]


def test_the_attention_list_shows_a_person_only_what_they_may_open(world):
    titles = [r["title"] for r in widget_context(world, "attention", "technical staff")["rows"]]
    assert "Provisioning failed" in titles and "Overdue invoices" not in titles and "Payments to confirm" not in titles
    client = browser(world.people["technical staff"])
    for r in widget_context(world, "attention", "technical staff")["rows"]:
        assert client.get(r["url"]).status_code == 200, r["title"]  # never a row that leads to a 403


def test_an_empty_attention_list_says_all_clear():
    from django.template.loader import render_to_string

    html = render_to_string("console/widgets/attention.html", {"rows": []})
    assert "All clear" in html and 'class="attn-list"' not in html


def test_a_panel_that_has_nothing_to_show_explains_what_would_appear_there(world):
    from django.template.loader import render_to_string

    for key, needle in (("activity", "Nothing has been recorded yet"), ("domains", "No domain expires")):
        html = render_to_string(f"console/widgets/{key}.html", {"events": [], "domains": [], "count": 0})
        assert 'class="state-block"' in html and needle in html and "No data" not in html


def test_the_dashboard_greets_by_time_of_day_and_puts_the_main_action_first(world):
    response = browser(world.people["manager"]).get(reverse("console:dashboard"))
    assert response.context["greeting"] in ("Good morning", "Good afternoon", "Good evening")
    page = response.content.decode()
    assert 'class="btn btn-sm btn-primary" href="/staff/clients/new/"' in page and 'class="page-sub"' in page


def test_the_dashboard_script_can_show_an_error_with_a_retry():
    js = open("static/js/shell.js", encoding="utf-8").read()
    assert "htmx:responseError" in js and "Try again" in js and "This panel could not be loaded" in js


def test_audit_codes_are_shown_as_phrases_on_the_dashboard(world):
    from apps.core.templatetags.ui import action_label

    assert action_label("auth.login") == "Signed in" and action_label("invoice.paid") == "Invoice paid"
    assert action_label("account.role_changed") == "Account role changed" and action_label("") == ""


def test_health_reports_a_missing_email_provider_as_a_problem_not_an_error(world):
    checks = {c["name"]: c for c in widget_context(world, "health")["checks"]}
    assert checks["Database"]["ok"] and checks["Cache"]["ok"] and checks["Domain registrar"]["ok"]
    assert not checks["Email provider"]["ok"] and "none is active" in checks["Email provider"]["detail"]


def test_health_shows_a_broken_dependency_as_a_problem_instead_of_failing_the_page(world, monkeypatch):
    def broken():
        raise RuntimeError("secret connection string")

    monkeypatch.setattr(widgets, "_database", broken)
    page = browser(world.people["admin"]).get(reverse("console:widget", args=["health"]))
    assert page.status_code == 200 and b"Problem" in page.content and b"secret connection string" not in page.content


def test_activity_shows_the_ten_latest_events_newest_first(world):
    events = widget_context(world, "activity")["events"]
    assert len(events) == 10 and [e.pk for e in events] == sorted((e.pk for e in events), reverse=True)


def test_reading_the_dashboard_writes_nothing(world):
    client = browser(world.people["admin"])
    before = AuditEvent.objects.count()  # after signing in: signing in is itself recorded
    for key in ALL_KEYS:
        client.get(reverse("console:widget", args=[key]))
    client.get(reverse("console:dashboard"))
    client.get(reverse("console:search"), {"q": "ada"})
    assert AuditEvent.objects.count() == before


# --- Search -----------------------------------------------------------------------------------------------------------

def hits(world, term, role="admin"):
    response = browser(world.people[role]).get(reverse("console:search"), {"q": term})
    assert response.status_code == 200
    return {g.label: g for g in response.context["groups"]}


def test_a_client_is_found_by_name_email_and_reference(world):
    for term in ("Ada Ltd", "ada@harness", world.client.reference):
        assert "Clients" in hits(world, term), term
    group = hits(world, "Ada Ltd")["Clients"]
    assert group.hits[0].url == reverse("clients_staff:detail", args=[world.client.pk])


def test_an_invoice_is_found_by_number_and_a_domain_and_a_ticket_by_theirs(world):
    number = world.objects["invoices"]["paid"].number
    assert hits(world, number)["Invoices"].hits[0].url == reverse("billing_staff:invoice_detail", args=[world.objects["invoices"]["paid"].pk])
    assert "Domains" in hits(world, "name2.com")
    assert "Hosting" in hits(world, "site1.harness.test")
    ticket = world.objects["tickets"]["open"]
    assert "Tickets" in hits(world, ticket.reference)
    assert "Orders" in hits(world, next(iter(world.objects["orders"].values())).reference)


def test_every_hit_opens_for_the_person_who_searched(world):
    client = browser(world.people["support agent"])
    found = client.get(reverse("console:search"), {"q": "harness"}).context["groups"]
    assert found
    for group in found:
        for hit in group.hits:
            assert client.get(hit.url).status_code == 200, (group.label, hit.url)
        assert client.get(group.all_url).status_code == 200, group.all_url


def test_search_shows_only_the_areas_a_person_may_open(world):
    class Limited:
        is_authenticated = True

        def __init__(self, allowed):
            self.allowed = allowed

        def has_perm(self, codename):
            return codename in {f"accounts.{a}" for a in self.allowed}

    only_clients = search_module.search(Limited({"view_clients"}), "Ada")
    assert [g.label for g in only_clients] == ["Clients"]
    only_billing = search_module.search(Limited({"view_billing"}), "Ada")
    assert [g.label for g in only_billing] == ["Invoices"]
    assert search_module.search(Limited(set()), "Ada") == []


def test_a_short_or_empty_search_finds_nothing_and_says_why(world):
    assert b"at least two characters" in browser(world.people["admin"]).get(reverse("console:search"), {"q": "a"}).content
    assert browser(world.people["admin"]).get(reverse("console:search")).context["groups"] == []
    assert browser(world.people["admin"]).get(reverse("console:search"), {"q": "   "}).context["groups"] == []


@pytest.mark.parametrize("term", ["%", "_", "\\", "'; drop table clients_client; --", "<script>alert(1)</script>", "a" * 400,
                                  "\x00ada", "ada\x00", "É", "🙂", "ada  ltd", "C1", "INV-000001", "T000001"])
def test_hostile_or_odd_search_terms_never_crash_or_inject(world, term):
    response = browser(world.people["admin"]).get(reverse("console:search"), {"q": term})
    assert response.status_code == 200 and b"<script>alert(1)" not in response.content


def test_the_see_all_link_carries_the_term_and_lands_on_the_filtered_list(world):
    group = hits(world, "harness")["Clients"]
    assert group.all_url == f"{reverse('clients_staff:list')}?q=harness"
    assert group.total >= 1 and len(group.hits) <= search_module.LIMIT


def test_a_group_never_lists_more_than_the_limit_but_counts_them_all(world):
    group = hits(world, "harness.test")["Hosting"]
    assert len(group.hits) == search_module.LIMIT and group.total > search_module.LIMIT


def test_the_staff_shell_has_a_search_and_customers_do_not(world):
    page = browser(world.people["manager"]).get(reverse("console:dashboard")).content.decode()
    assert f'data-search-url="{reverse("console:search")}"' in page and f'href="{reverse("console:search")}"' in page
    assert "data-quick-open" in page and 'id="quick-q"' in page
    customer_page = browser(world.people["customer owner"]).get(reverse("dashboard")).content.decode()
    assert reverse("console:search") not in customer_page


# --- Audit log --------------------------------------------------------------------------------------------------------

def test_the_audit_log_needs_its_own_permission(world):
    assert browser().get(reverse("console:audit_log")).status_code == 302
    assert browser(world.people["support agent"]).get(reverse("console:audit_log")).status_code == 403
    assert browser(world.people["customer owner"]).get(reverse("console:audit_log")).status_code == 403
    for role in ("manager", "admin", "super admin"):
        assert browser(world.people[role]).get(reverse("console:audit_log")).status_code == 200, role


def test_the_log_filters_by_text_and_by_area_and_pages_by_fifty(world):
    client = browser(world.people["admin"])
    everything = client.get(reverse("console:audit_log")).context["page"]
    assert len(everything) == 50 and everything.paginator.count > 50
    invoice_events = client.get(reverse("console:audit_log"), {"action": "invoice"}).context["page"]
    assert invoice_events.paginator.count and all(e.action.startswith("invoice") for e in invoice_events)
    by_text = client.get(reverse("console:audit_log"), {"q": "manager@harness.test"}).context["page"]
    assert by_text.paginator.count and all("manager@harness.test" in e.actor_repr or "manager@harness.test" in e.target_repr
                                           or "manager@harness.test" in e.action for e in by_text)
    assert client.get(reverse("console:audit_log"), {"page": 2}).status_code == 200
    assert client.get(reverse("console:audit_log"), {"page": "junk"}).status_code == 200


def test_the_log_shows_secrets_as_redacted_and_the_areas_it_offers_are_real(world):
    record("test.login", actor=world.people["admin"], metadata={"password": "hunter2", "note": "kept"})
    page = browser(world.people["admin"]).get(reverse("console:audit_log"), {"q": "test.login"}).content.decode()
    assert "[redacted]" in page and "hunter2" not in page and "kept" in page
    kinds = browser(world.people["admin"]).get(reverse("console:audit_log")).context["kinds"]
    assert "invoice" in kinds and "test" in kinds and kinds == sorted(kinds)


@pytest.mark.parametrize("term", ["%", "_", "\x00", "a" * 300, "'; --"])
def test_odd_log_searches_are_safe(world, term):
    assert browser(world.people["admin"]).get(reverse("console:audit_log"), {"q": term}).status_code == 200


# --- Menus ------------------------------------------------------------------------------------------------------------

def test_the_staff_menu_starts_with_the_dashboard_and_offers_the_audit_log_by_permission(world):
    from django.test import RequestFactory

    def build(role):
        request = RequestFactory().get("/staff/")
        request.user = world.people[role]
        request.resolver_match = None
        return navigation.build(request, "staff")

    for role in EXPECTED:
        menus = build(role)
        assert menus["main"][0].label == "Overview" and menus["main"][0].url == reverse("console:dashboard")
        has_log = any(e.key == "staff.activity" and e.url == reverse("console:audit_log") for e in menus["main"])
        assert has_log == (role not in ("support agent", "technical staff")), role


def test_the_dashboard_link_is_marked_as_the_current_page(world):
    page = browser(world.people["manager"]).get(reverse("console:dashboard")).content.decode()
    assert re.search(r'class="rail-link is-current" href="/staff/" title="Overview" aria-current="page"', page)
