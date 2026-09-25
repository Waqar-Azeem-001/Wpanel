"""The menu registry (roadmap Rule 5.3 / Section 12.2): valid by construction, permission-aware, and the only source of menus."""
from pathlib import Path

import pytest
from django.conf import settings
from django.core import checks
from django.test import RequestFactory
from django.urls import resolve, reverse

from apps.accounts.models import User
from apps.accounts.roles import Role
from apps.clients import services as client_services
from apps.core import navigation
from apps.core.navigation import item

pytestmark = pytest.mark.django_db

TEMPLATES = Path(settings.BASE_DIR) / "templates"


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def admin(staff):
    return staff(Role.ADMIN)


@pytest.fixture
def agent(staff):
    return staff(Role.SUPPORT_AGENT)


@pytest.fixture
def owner(manager):
    return client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test", "country": "US"}
                                         ).contacts.get().user


@pytest.fixture
def loner(make_user):
    """A signed-in customer with no client account."""
    return make_user("loner@example.com")


def request_for(user, path="/account/profile/"):
    request = RequestFactory().get(path)
    request.user = user
    request.resolver_match = resolve(path)
    return request


def labels(menus, position):
    return [e.label for e in menus[position]]


# --- The registry is sound --------------------------------------------------------------------------------------------

def test_the_registry_has_no_faults():
    assert navigation.problems() == []
    assert len({e.key for e in navigation.ITEMS}) == len(navigation.ITEMS)


def test_the_startup_check_reports_no_errors():
    assert [e for e in checks.run_checks() if e.id == "core.E001"] == []


def bad_registry(monkeypatch, *extra):
    monkeypatch.setattr(navigation, "ITEMS", navigation.ITEMS + list(extra))
    monkeypatch.setattr(navigation, "BY_KEY", {e.key: e for e in navigation.ITEMS})


@pytest.mark.parametrize("entry,fragment", [
    (item("x.bad", "Bad", "no_such:route", areas=("client",)), "does not resolve"),
    (item("client.home", "Second home", "accounts:profile", areas=("client",)), "used twice"),
    (item("x.orphan", "Orphan", "accounts:login", areas=("client",), parent="nope"), "does not exist"),
    (item("x.perm", "Perm", "accounts:login", areas=("staff",), permission="view_nothing"), "unknown permission"),
    (item("x.pos", "Pos", "accounts:login", areas=("staff",), position="middle"), "unknown position"),
    (item("x.req", "Req", "accounts:login", areas=("client",), requires="wizard"), "unknown requirement"),
    (item("x.badge", "Badge", "accounts:login", areas=("client",), badge="lucky"), "unknown badge"),
    (item("x.area", "Area", "accounts:login", areas=("moon",)), "unknown area"),
    (item("x.group", "Empty group", areas=("client",)), "nothing in it"),
    (item("x.label", "  ", "accounts:login", areas=("client",)), "no label"),
    (item("x.child", "Child", "accounts:login", areas=("client",), parent="client.home"), "is a link, not a group"),
    (item("x.scope", "Scope", "accounts:login", areas=("staff",), parent="client.services"), "area its group is not"),
])
def test_every_kind_of_fault_is_found(monkeypatch, entry, fragment):
    bad_registry(monkeypatch, entry)
    assert any(fragment in p for p in navigation.problems()), navigation.problems()


def test_a_broken_registry_fails_manage_py_check(monkeypatch):
    bad_registry(monkeypatch, item("x.bad", "Bad", "no_such:route", areas=("client",)))
    errors = [e for e in checks.run_checks() if e.id == "core.E001"]
    assert errors and "does not resolve" in errors[0].msg


def test_every_menu_link_reverses_to_an_internal_path():
    for entry in navigation.ITEMS:
        if entry.url_name:
            assert reverse(entry.url_name).startswith("/"), entry.key


# --- Who sees what ----------------------------------------------------------------------------------------------------

def test_a_customer_with_an_account_sees_the_full_customer_menu(owner):
    menus = navigation.build(request_for(owner), "client")
    assert labels(menus, "main") == ["Home", "Services", "Domains", "Billing", "Support", "Open Ticket", "Affiliates"]
    services = next(e for e in menus["main"] if e.key == "client.services")
    assert [c.label for c in services.children] == ["My Hosting", "My Orders", "Order New Services"]
    assert labels(menus, "right") == ["Cart", "Notifications"]
    account = menus["account"][0]
    assert [c.label for c in account.children] == ["Your profile", "Your account", "Cancellation requests",
                                                   "Change password", "Notification preferences"]
    assert account.label == owner.get_short_name()


def test_a_customer_without_an_account_sees_the_fallback_not_the_account_menus(loner):
    menus = navigation.build(request_for(loner), "client")
    assert labels(menus, "main") == ["Home", "Plans", "Help"]
    assert labels(menus, "right") == ["Notifications"]  # no cart without a client account


def test_an_anonymous_visitor_sees_the_public_menu():
    from django.contrib.auth.models import AnonymousUser

    menus = navigation.build(request_for(AnonymousUser(), reverse("accounts:login")), "public")
    assert labels(menus, "main") == ["Plans", "Domains", "Help"] and labels(menus, "right") == ["Sign in", "Create account"]


def test_the_public_sign_in_entries_are_hidden_from_signed_in_people(owner):
    assert navigation.build(request_for(owner), "public")["right"] == []


def test_staff_menus_follow_their_permissions(agent, manager, admin):
    def top(user):
        return labels(navigation.build(request_for(user), "staff"), "main")

    assert top(agent) == ["Clients", "Orders", "Billing", "Support"]  # no Reports, no Utilities
    assert top(manager) == ["Clients", "Orders", "Billing", "Support", "Reports"]
    assert top(admin) == ["Clients", "Orders", "Billing", "Support", "Reports", "Utilities"]


def test_a_group_appears_only_when_something_in_it_can_be_opened(agent, admin):
    agent_clients = next(e for e in navigation.build(request_for(agent), "staff")["main"] if e.key == "staff.clients")
    assert [c.label for c in agent_clients.children] == [
        "View / Search Clients", "Products / Services", "Domain Registrations", "Cancellation Requests", "Service Lifecycle"]
    setup = navigation.build(request_for(admin), "staff")["setup"][0]
    assert "Brand" in [c.label for c in setup.children] and setup.children[9].divider_before  # the divider before settings
    agent_setup = navigation.build(request_for(agent), "staff")["setup"][0]
    assert [c.label for c in agent_setup.children] == ["Domain Pricing", "Servers", "Payment Methods", "Tax Rules",
                                                        "Coupons", "Billing Settings", "Support Departments"]


def test_the_django_admin_link_is_for_superusers_only(owner, admin):
    superuser = User.objects.create_superuser(email="root@example.com", password="x")
    assert "Django admin" not in [c.label for c in navigation.build(request_for(admin), "staff")["account"][0].children]
    assert "Django admin" in [c.label for c in navigation.build(request_for(superuser), "staff")["account"][0].children]
    assert "Django admin" not in [c.label for c in navigation.build(request_for(owner), "client")["account"][0].children]


def test_a_feature_flag_hides_an_entry_until_it_is_switched_on(monkeypatch, owner, settings):
    bad_registry(monkeypatch, item("x.beta", "Beta feature", "accounts:profile", areas=("client",), feature_flag="beta"))
    settings.FEATURE_FLAGS = {}
    assert "Beta feature" not in labels(navigation.build(request_for(owner), "client"), "main")
    settings.FEATURE_FLAGS = {"beta": True}
    assert "Beta feature" in labels(navigation.build(request_for(owner), "client"), "main")


def test_the_bell_shows_the_unread_count(owner):
    from apps.notifications.models import Notification

    assert navigation.build(request_for(owner), "client")["right"][-1].badge == 0
    Notification.objects.create(user=owner, event="order.active", title="x")
    Notification.objects.create(user=owner, event="order.active", title="y")
    assert navigation.build(request_for(owner), "client")["right"][-1].badge == 2


# --- Rule 5.6, in both directions -------------------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["agent", "manager", "admin"])
def test_a_staff_page_is_hidden_exactly_from_people_who_cannot_open_it(client, role, agent, manager, admin):
    """If you can see a link you can open it (tested for every menu in test_ui); and if you cannot open it, you do not see it."""
    user = {"agent": agent, "manager": manager, "admin": admin}[role]
    client.force_login(user)
    seen = {e.key for menu in navigation.build(request_for(user), "staff").values() for top in menu
            for e in (top.children or [top])}
    for entry in navigation.ITEMS:
        if "staff" not in entry.areas or not entry.url_name or entry.parent == "account.menu":
            continue
        if entry.key not in seen:
            status = client.get(reverse(entry.url_name), HTTP_HOST="localhost").status_code
            assert status in (302, 403), (role, entry.key, status)  # never a page it would show if it were linked


# --- Active state and breadcrumbs -------------------------------------------------------------------------------------

def test_the_current_page_and_its_group_are_marked(manager):
    menus = navigation.build(request_for(manager, "/staff/billing/invoices/"), "staff")
    billing = next(e for e in menus["main"] if e.key == "staff.billing")
    assert billing.active and [c.label for c in billing.children if c.active] == ["Invoices"]
    clients = next(e for e in menus["main"] if e.key == "staff.clients")
    assert not clients.active


def test_a_detail_page_keeps_its_group_lit(manager):
    menus = navigation.build(request_for(manager, "/staff/billing/payments/"), "staff")
    assert next(e for e in menus["main"] if e.key == "staff.billing").active


def test_breadcrumbs_come_from_the_registry(manager):
    crumbs = navigation.breadcrumbs_for(request_for(manager, "/staff/billing/invoices/"))
    assert [c[0] for c in crumbs] == ["Home", "Billing", "Invoices"]
    assert crumbs[0][1] == reverse("home") and crumbs[1][1] == "" and crumbs[2][1] == reverse("billing_staff:invoice_list")
    assert [c[0] for c in navigation.breadcrumbs_for(request_for(manager, "/staff/reports/"))] == ["Home", "Reports"]
    assert navigation.breadcrumbs_for(request_for(manager, "/account/profile/")) == []  # the home page has none
    assert navigation.breadcrumbs_for(request_for(manager, "/staff/billing/invoices/1/")) == []  # a page that is not an entry


def test_the_breadcrumb_bar_is_drawn_on_registry_pages_only(client, manager):
    client.force_login(manager)
    page = client.get("/staff/billing/invoices/").content.decode()
    assert 'aria-label="Breadcrumb"' in page and 'aria-current="page">Invoices<' in page
    assert 'aria-label="Breadcrumb"' not in client.get("/account/profile/").content.decode()


# --- No menu is written by hand ---------------------------------------------------------------------------------------

def test_no_navigation_template_contains_a_link_of_its_own():
    for name in ("navbar_public", "navbar_client", "navbar_staff", "nav_items"):
        text = (TEMPLATES / "components" / f"{name}.html").read_text(encoding="utf-8")
        assert "<a class=\"nav-link\" href=\"/" not in text and 'href="/' not in text, name
        urls = [u for u in text.replace("\n", " ").split("{% url ")[1:]]
        assert all(u.startswith("'accounts:logout'") for u in urls), (name, urls)  # only the sign-out form


def test_the_old_hand_written_account_partial_is_gone():
    assert not (TEMPLATES / "components" / "navbar_account.html").exists()
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "perms.accounts" not in base and "client_contacts" not in base
    for name in ("navbar_public", "navbar_client", "navbar_staff"):
        assert "perms.accounts" not in (TEMPLATES / "components" / f"{name}.html").read_text(encoding="utf-8")
