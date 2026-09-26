"""The design system (roadmap Phase D1): shells, components, shared tags, error pages and assets."""
import re
from datetime import date, datetime
from datetime import timezone as dt_timezone
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django import forms
from django.conf import settings
from django.contrib.staticfiles import finders
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import Role
from apps.billing import invoicing
from apps.branding import services as branding
from apps.clients import services as client_services
from apps.core.templatetags import ui
from apps.products import services as product_services
from apps.products.models import ProductType

pytestmark = pytest.mark.django_db

TEMPLATES = Path(settings.BASE_DIR) / "templates"
STATIC = Path(settings.BASE_DIR) / "static"


def render(source, **context):
    return Template("{% load ui %}" + source).render(Context(context))


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
    client = client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test", "country": "US"})
    return client.contacts.get().user


# --- Status badges (v2 Section 9.3) -----------------------------------------------------------------------------------

@pytest.mark.parametrize("tone,statuses", [
    ("success", ["active", "paid", "resolved", "approved", "completed", "registered"]),
    ("warning", ["pending", "pending_payment", "processing", "provisioning", "partially_paid", "customer_reply",
                 "renewal_due", "grace"]),
    ("danger", ["unpaid", "overdue", "fraud", "failed", "suspended", "rejected", "expired"]),
    ("info", ["open", "agent_reply", "in_progress", "pending_transfer_in", "draft"]),
    ("neutral", ["cancelled", "terminated", "closed", "refunded", "collections"])])
def test_every_status_in_the_roadmap_map_has_its_colour(tone, statuses):
    for status in statuses:
        assert f'class="status-badge is-{tone} status-{status}"' in render("{% status_badge s %}", s=status), status


def test_a_badge_always_shows_a_text_label_and_never_trusts_it():
    assert ">Pending payment<" in render("{% status_badge s %}", s="pending_payment")  # readable label from the key
    assert ">Paid in full<" in render("{% status_badge s l %}", s="paid", l="Paid in full")
    html = render("{% status_badge s l %}", s="active", l="<script>alert(1)</script>")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "is-neutral" in render("{% status_badge s %}", s="something-new")  # unknown = neutral, never uncoloured
    assert "is-warning" in render("{% status_badge s %}", s="Pending Payment")  # case and spaces do not matter
    assert ">-<" in render("{% status_badge s %}", s=None)


def test_no_template_hand_colours_a_status_any_more():
    offenders = [str(p.relative_to(TEMPLATES)) for p in TEMPLATES.rglob("*.html")
                 if re.search(r'class="badge status-', p.read_text(encoding="utf-8"))]
    assert offenders == []


# --- Money and dates --------------------------------------------------------------------------------------------------

def test_money_is_written_as_the_brand_says(admin):
    assert render("{{ v|money:c }}", v=Decimal("1234.5"), c="USD") == "USD 1,234.50"
    assert render("{{ v|money }}", v="99") == "99.00"
    assert render("{{ v|money:c }}", v="oops", c="USD") == "oops" and render("{{ v|money }}", v=None) == "-"
    branding.save_settings(admin, values={"money_format": "code_after"})
    assert render("{{ v|money:c }}", v=Decimal("1234.5"), c="USD") == "1,234.50 USD"
    branding.save_settings(admin, values={"money_format": "plain"})
    assert render("{{ v|money:c }}", v=Decimal("1234.5"), c="USD") == "1,234.50"


def test_dates_are_written_as_the_brand_says_in_the_site_time_zone(admin):
    moment = datetime(2026, 9, 25, 22, 30, tzinfo=dt_timezone.utc)
    assert render("{{ v|fdate }}", v=date(2026, 9, 5)) == "05 Sep 2026" and render("{{ v|fdate }}", v=None) == "-"
    with timezone.override("Asia/Karachi"):  # 22:30 UTC is already the next morning there
        assert render("{{ v|fdate }}", v=moment) == "26 Sep 2026"
        assert render("{{ v|fdatetime }}", v=moment) == "26 Sep 2026 03:30"
    branding.save_settings(admin, values={"date_format": "%Y-%m-%d"})
    assert render("{{ v|fdate }}", v=date(2026, 9, 5)) == "2026-09-05"
    assert render("{{ v|fdatetime }}", v=datetime(2026, 9, 5, 8, 5)) == "2026-09-05 08:05"


# --- Forms ------------------------------------------------------------------------------------------------------------

class SampleForm(forms.Form):
    name = forms.CharField(label="Full name", help_text="As on your ID")
    plan = forms.ChoiceField(label="Plan", choices=[("a", "A"), ("b", "B")])
    agree = forms.BooleanField(label="I agree")
    note = forms.CharField(label="Note", required=False, widget=forms.Textarea)
    token = forms.CharField(widget=forms.HiddenInput, required=False)


def test_form_fields_get_bootstrap_classes_labels_and_required_markers():
    html = render_to_string("partials/form.html", {"form": SampleForm()})
    assert 'class="form-control"' in html and 'class="form-select"' in html and 'class="form-check-input"' in html
    assert '<label for="id_name">Full name' in html and "(required)" in html and "As on your ID" in html
    assert html.count("(required)") == 3  # name, plan, agree - not the optional note
    assert 'type="hidden"' in html and 'for="id_agree"' in html


def test_a_field_with_an_error_is_marked_invalid_and_linked_to_its_message():
    form = SampleForm({"name": "", "plan": "a"})
    html = render_to_string("partials/form.html", {"form": form})
    assert "is-invalid" in html and 'aria-invalid="true"' in html and 'aria-describedby="id_name_errors"' in html
    assert 'id="id_name_errors"' in html and "This field is required." in html


def test_the_field_filter_keeps_widget_classes_and_skips_hidden_fields():
    class F(forms.Form):
        x = forms.CharField(widget=forms.TextInput(attrs={"class": "short"}))
        h = forms.CharField(widget=forms.HiddenInput)

    form = F()
    assert 'class="short form-control"' in str(ui.bs_field(form["x"]))
    assert 'class="' not in str(ui.bs_field(form["h"]))


# --- The shells and the navigation ------------------------------------------------------------------------------------

def hrefs(html, container_class):
    """The internal links inside the first element whose class list contains ``container_class``."""
    class Finder(HTMLParser):
        def __init__(self):
            super().__init__()
            self.depth, self.links = 0, []

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if self.depth == 0 and tag in ("nav", "header") and container_class in (a.get("class") or "").split():
                self.depth = 1
            elif self.depth:
                self.depth += 1 if tag not in ("br", "img", "hr", "input", "meta", "link") else 0
                if tag == "a" and a.get("href", "").startswith("/"):
                    self.links.append(a["href"])

        def handle_endtag(self, tag):
            if self.depth:
                self.depth -= 1

    finder = Finder()
    finder.feed(html)
    return finder.links


def test_each_kind_of_visitor_gets_their_shell(client, owner, manager):
    page = client.get("/").content.decode()
    assert "navbar-public" in page and "shell-client" not in page and "shell-staff" not in page
    client.force_login(owner)
    page = client.get(reverse("accounts:profile")).content.decode()
    assert "shell-client" in page and "shell-staff" not in page and "navbar-public" not in page
    client.force_login(manager)
    page = client.get(reverse("accounts:profile")).content.decode()
    assert "shell-staff" in page and "shell-client" not in page


def test_every_shell_has_the_shared_furniture(client, manager):
    client.force_login(manager)
    page = client.get(reverse("reports_staff:index")).content.decode()
    for needle in ('class="skip-link"', 'id="main"', 'id="confirm-modal"', "app-footer", 'href="/brand.css?v=',
                   "vendor/bootstrap/css/bootstrap.min.css", "vendor/htmx/htmx.min.js", "js/app.js", 'lang="en"',
                   'data-bs-theme="light"'):
        assert needle in page, needle


@pytest.mark.parametrize("who", ["anonymous", "customer", "agent", "manager", "admin", "superuser"])
def test_every_link_in_the_navigation_opens_for_the_person_who_sees_it(client, who, owner, agent, manager, admin):
    """Rule 5.6: if you can see a link you can open it. (A menu entry that led to a 403 would fail here.)"""
    users = {"customer": owner, "agent": agent, "manager": manager, "admin": admin,
             "superuser": User.objects.create_superuser(email="root@example.com", password="x")}
    if who != "anonymous":
        client.force_login(users[who])
    start = "/" if who == "anonymous" else reverse("accounts:profile")
    page = client.get(start, follow=True).content.decode()
    links = set()
    for part in ("app-navbar", "chrome", "tabbar"):  # the store bar, the header (menus, actions, group strip), the phone bar
        links |= set(hrefs(page, part))
    assert len(links) >= 5, links
    broken = {}
    for link in sorted(links):
        if link == reverse("accounts:logout"):
            continue
        status = client.get(link, HTTP_HOST="localhost").status_code
        if status >= 400:
            broken[link] = status
    assert broken == {}, (who, broken)


def menu_labels(client, user):
    """Every page label the person is offered: the rail, the group strip and the jump-to list all come from one list."""
    import json

    client.force_login(user)
    page = client.get(reverse("accounts:profile")).content.decode()
    data = page.split('id="quick-pages" type="application/json">')[1].split("</script>")[0]
    return {p["label"] for p in json.loads(data)} | {p["group"] for p in json.loads(data)}


def test_the_menus_show_only_what_a_role_may_open(client, agent, manager, admin, owner):
    agent_labels = menu_labels(client, agent)
    for hidden in ("Reports", "Affiliate programme", "Brand", "Add a client", "New order", "Activity log", "Users"):
        assert hidden not in agent_labels, hidden
    assert {"Tickets", "Registrations"} <= agent_labels
    manager_labels = menu_labels(client, manager)
    assert {"Reports", "Add a client", "Users"} <= manager_labels and "Lifecycle timings" not in manager_labels
    admin_labels = menu_labels(client, admin)
    assert {"Lifecycle timings", "Brand", "Email log"} <= admin_labels
    customer_labels = menu_labels(client, owner)
    assert {"Cancellation requests"} & customer_labels == set()  # that one lives in the profile menu, not the rail
    assert {"Invoices", "Tickets", "My hosting"} <= customer_labels and "Settings" not in customer_labels


def test_the_account_menu_keeps_working_forms_and_the_bell(client, owner):
    client.force_login(owner)
    page = client.get(reverse("accounts:profile")).content.decode()
    assert f'action="{reverse("accounts:logout")}"' in page and "Sign out" in page
    from apps.notifications.models import Notification

    Notification.objects.create(user=owner, event="order.active", title="x")
    assert 'class="icon-badge">1<' in client.get(reverse("accounts:profile")).content.decode()


def test_the_layout_choice_is_a_context_processor_not_a_per_page_edit():
    request = RequestFactory().get("/")
    request.user = type("Anon", (), {"is_authenticated": False, "is_staff": False})()
    from apps.core.context_processors import layout

    assert layout(request) == {"layout_template": "layouts/public.html", "ui_area": "public"}
    assert TEMPLATES.joinpath("base.html").read_text().count("{% extends layout_template %}") == 1


# --- Error pages and maintenance --------------------------------------------------------------------------------------

def test_the_404_page_is_branded_and_offers_a_way_back(client, admin):
    branding.save_settings(admin, values={"site_name": "Lost Hosting"})
    response = client.get("/no/such/page/")
    page = response.content.decode()
    assert response.status_code == 404 and "We could not find that page" in page and "Lost Hosting" in page
    assert "navbar-public" in page and 'role="search"' in page and reverse("help:index") in page
    assert f'href="{reverse("home")}">Go to the home page</a>' in page


def test_the_403_page_explains_and_links_back(client, owner):
    client.force_login(owner)
    response = client.get(reverse("clients_staff:list"))
    page = response.content.decode()
    assert response.status_code == 403 and "do not have access" in page and "shell-client" in page
    assert f'href="{reverse("home")}">Back to the dashboard</a>' in page  # the page's own way back, not just the brand link
    client.logout()
    assert b"Sign in" in client.get(reverse("clients_staff:list"), follow=True).content  # anonymous users go to sign in


def test_the_403_page_names_the_support_address_when_there_is_one(client, owner, admin):
    branding.save_settings(admin, values={"support_email": "help@acme.test"})
    client.force_login(owner)
    assert b"help@acme.test" in client.get(reverse("clients_staff:list")).content


def test_a_broken_brand_never_breaks_the_error_pages(client, monkeypatch):
    from apps.branding.models import BrandSettings

    monkeypatch.setattr(BrandSettings, "load", classmethod(lambda cls: (_ for _ in ()).throw(RuntimeError("db down"))))
    response = client.get("/no/such/page/")
    assert response.status_code == 404 and b"We could not find that page" in response.content


def test_the_500_page_stands_alone(django_assert_num_queries):
    """It is shown when something is already broken: no request, no context processors, no database."""
    from django.views.defaults import server_error

    request = RequestFactory().get("/boom/")
    with django_assert_num_queries(0):
        response = server_error(request)
    page = response.content.decode()
    assert response.status_code == 500 and "Something went wrong on our side" in page
    assert f'href="{reverse("home")}">Go to the home page</a>' in page and reverse("help:index") in page
    assert "<nav" not in page and "brand.css" not in page and "csrf" not in page.lower()


def test_the_maintenance_page_depends_on_nothing():
    page = (TEMPLATES / "maintenance.html").read_text(encoding="utf-8")
    assert "{%" not in page and "{{" not in page and "href=" not in page and "src=" not in page
    assert "We will be right back" in page and "<style>" in page


# --- Assets and links -------------------------------------------------------------------------------------------------

def test_no_page_loads_anything_from_the_internet():
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        if "emails" in path.parts or path.name == "email.html":
            continue
        for match in re.finditer(r'<(?:script|link|img)\b[^>]*\b(?:src|href)="(https?:)?//[^"]+"', path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(TEMPLATES)}: {match.group(0)[:80]}")
    assert offenders == []


def test_every_static_file_a_template_names_exists():
    missing = []
    for path in TEMPLATES.rglob("*.html"):
        for name in re.findall(r"""\{%\s*static\s+['"]([^'"]+)['"]\s*%\}""", path.read_text(encoding="utf-8")):
            if not finders.find(name):
                missing.append(f"{path.relative_to(TEMPLATES)}: {name}")
    assert missing == []


def test_the_vendored_assets_are_present_with_their_licences():
    for name in ("vendor/bootstrap/css/bootstrap.min.css", "vendor/bootstrap/js/bootstrap.bundle.min.js",
                 "vendor/bootstrap-icons/font/bootstrap-icons.min.css", "vendor/bootstrap-icons/font/fonts/bootstrap-icons.woff2",
                 "vendor/htmx/htmx.min.js", "vendor/bootstrap/LICENSE", "vendor/bootstrap-icons/LICENSE",
                 "vendor/htmx/LICENSE", "css/theme.css", "js/theme.js", "js/app.js", "img/favicon.svg"):
        assert (STATIC / name).is_file(), name
    css = (STATIC / "css/theme.css").read_text(encoding="utf-8")
    assert ":focus-visible" in css and '[data-bs-theme="dark"]' in css and "--brand-primary" in css


def test_the_old_stylesheet_is_gone():
    assert not (STATIC / "css" / "app.css").exists()
    assert not [p for p in TEMPLATES.rglob("*.html") if "css/app.css" in p.read_text(encoding="utf-8")]


def test_the_document_and_email_shells_render_without_navigation():
    document = render_to_string("layouts/document.html", {"brand": branding.get(), "SITE_NAME": "Doc Co"})
    assert "Doc Co" in document and "<nav" not in document and "navbar" not in document
    email = render_to_string("layouts/email.html", {"brand": branding.get(), "body": "<p>Hello</p>", "logo_url": ""})
    assert "<p>Hello</p>" in email and "background:#2459d6" in email and "<script" not in email


# --- Accessibility: every control has a label -------------------------------------------------------------------------

def unlabelled(html):
    """The name of every form control that has no label (a <label for>, a wrapping <label> or an aria-label)."""
    class Scan(HTMLParser):
        def __init__(self):
            super().__init__()
            self.labelled_ids, self.controls, self.in_label = set(), [], False

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "label":
                self.in_label = True
                if a.get("for"):
                    self.labelled_ids.add(a["for"])
            elif tag in ("input", "select", "textarea") and a.get("type") not in ("hidden", "submit", "button"):
                if not (a.get("aria-label") or a.get("aria-labelledby") or self.in_label):
                    self.controls.append((a.get("id") or "", a.get("name")))

        def handle_endtag(self, tag):
            if tag == "label":
                self.in_label = False

    scan = Scan()
    scan.feed(html)
    return [name for ident, name in scan.controls if ident not in scan.labelled_ids]


def test_the_forms_the_audit_found_unlabelled_now_have_labels(client, manager):
    client.force_login(manager)
    ada = client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test", "country": "US"})
    product = product_services.create_product(manager, {"name": "Starter", "type": ProductType.SHARED_HOSTING,
                                                        "whm_package_name": "starter_pkg"})
    server = product_services.create_server(manager, {"name": "srv1", "hostname": "srv1.example.com"})
    invoice = invoicing.issue_invoice(manager, invoicing.create_invoice(
        manager, ada, lines=[{"description": "d", "quantity": 1, "unit_price": "10"}]))
    pages = [reverse("catalog_staff:product_detail", args=[product.slug]),
             reverse("catalog_staff:server_edit", args=[server.pk]),
             reverse("billing_staff:invoice_create") + f"?client={ada.pk}",
             reverse("billing_staff:quote_create") + f"?client={ada.pk}",
             reverse("billing_staff:invoice_detail", args=[invoice.pk])]
    for path in pages:
        page = client.get(path)
        assert page.status_code == 200, path
        assert unlabelled(page.content.decode()) == [], path
