"""
Link and URL robustness found by the D0 audit (roadmap Rule 5). The first pieces of the link-integrity harness that
Phase D2 will grow: a template lint, a hostile-query sweep of every page, and links that must be built by ``reverse``.
"""
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.test import RequestFactory
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from apps.accounts.roles import Role
from apps.clients import services as client_services
from apps.core.web import query_id

pytestmark = pytest.mark.django_db

TEMPLATES = Path(settings.BASE_DIR) / "templates"


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def some_client(manager):
    return client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test", "country": "US"})


# --- query_id --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [("5", 5), (" 12 ", 12), ("", None), ("abc", None), ("-1", None), ("1.5", None),
                                          ("0x10", None), ("9" * 30, None), ("12abc", None)])
def test_a_query_id_is_a_sane_number_or_not_given(raw, expected):
    request = RequestFactory().get("/", {"client": raw})
    assert query_id(request, "client") == expected
    assert query_id(RequestFactory().get("/"), "client") is None


# --- The four pages that took a client id from the address ------------------------------------------------------------

CLIENT_PAGES = [("/staff/billing/invoices/create/", "billing_staff:invoice_new"),
                ("/staff/billing/quotes/create/", "billing_staff:quote_new")]
CHOOSER_PAGES = ["/staff/orders/new/", "/staff/support/tickets/new/"]
JUNK = ["", "abc", "-1", "1e9", "9" * 30, "%00", "1;DROP TABLE"]


@pytest.mark.parametrize("path,chooser", CLIENT_PAGES)
def test_create_pages_without_a_usable_client_send_you_to_the_chooser(client, manager, some_client, path, chooser):
    client.force_login(manager)
    for junk in JUNK:
        response = client.get(path, {"client": junk} if junk else {})
        assert response.status_code == 302 and response["Location"] == reverse(chooser), (path, junk)
    assert client.get(path, {"client": some_client.pk}).status_code == 200  # a real client still opens the form
    assert client.get(path, {"client": 999999}).status_code == 404  # a well-formed but unknown one is a plain 404


@pytest.mark.parametrize("path", CHOOSER_PAGES)
def test_pages_that_choose_a_client_show_the_chooser_for_junk(client, manager, some_client, path):
    client.force_login(manager)
    for junk in JUNK:
        response = client.get(path, {"client": junk})
        assert response.status_code == 200 and b'name="q"' in response.content, (path, junk)  # the client search box
    assert client.get(path, {"client": some_client.pk}).status_code == 200
    assert client.get(path, {"client": 999999}).status_code == 404


# --- Every page survives hostile query strings ------------------------------------------------------------------------

def page_paths():
    """Every argument-free page (not the API, not Django admin, not POST-only actions) as a path."""
    found = []

    def walk(patterns, prefix=""):
        for p in patterns:
            if isinstance(p, URLResolver):
                walk(p.url_patterns, prefix + str(p.pattern))
            elif isinstance(p, URLPattern):
                path = prefix + str(p.pattern)
                if "<" in path or path.startswith(("api/", "admin/")) or "(?P" in path:
                    continue
                found.append("/" + path)

    walk(get_resolver().url_patterns)
    return sorted(set(found))


HOSTILE = ["client=abc", "page=zzz", "page=-3", "page=999999", "q=%00", "q=%27%22%3C", "status=nope", "days=x",
           "from=x&to=y", "group=zzz", "scope=zzz", "next=//evil.example", "department=abc", "ordering=zzz"]


def test_no_page_is_a_server_error_whatever_the_query_string(client, manager):
    client.force_login(manager)
    paths = [p for p in page_paths() if not p.endswith(("/logout/", ".gif"))]
    assert len(paths) > 50  # the sweep really covers the site
    broken = []
    for path in paths:
        for query in HOSTILE:
            status = client.get(f"{path}?{query}", HTTP_HOST="localhost").status_code
            if status >= 500:
                broken.append((path, query, status))
    assert broken == []


# --- Rule 5: links are generated, never typed -------------------------------------------------------------------------

def template_files():
    return [p for p in TEMPLATES.rglob("*.html")]


def test_templates_have_no_placeholder_or_hand_written_internal_links():
    bad = []
    for path in template_files():
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r'''(?:href|action|src|hx-get|hx-post)\s*=\s*(["'])(.*?)\1''', text):
            value = match.group(2).strip()
            if value in ("", "#") or value.lower().startswith("javascript:") or re.match(r"^/(?!/)", value):
                bad.append(f"{path.relative_to(TEMPLATES)}: {match.group(0)}")
    assert bad == []


def test_the_home_link_has_a_name_and_the_brand_uses_it(client):
    assert reverse("home") == "/"
    assert f'href="{reverse("home")}"'.encode() in client.get(reverse("accounts:login")).content


def test_links_built_in_code_come_from_reverse(settings):
    from apps.notifications import services

    token = "5f0f8e2c-3a55-4d5e-9d2a-2f5d3b4b8b0a"
    html = services._with_open_pixel("<p>x</p>", token)
    assert reverse("email_open_pixel", args=[token]) in html and html.startswith("<p>x</p>")
    assert "http://localhost:8000" in html  # on the configured site address


def test_the_email_failure_alert_links_to_the_failed_emails(staff, manager):
    from apps.notifications import services
    from apps.notifications.models import EmailMessage, Notification

    admin = staff(Role.ADMIN)
    message = EmailMessage.objects.create(to_email="x@example.com", subject="S", body_text="b", attempts=6,
                                          last_error="boom")
    services._alert_email_failing(message)
    note = Notification.objects.get(user=admin, event="email.failed")
    assert note.link == reverse("notifications_staff:emails") + "?status=failed"


def test_the_payment_webhook_address_shown_in_admin_comes_from_the_route(admin_client):
    from apps.billing.models import PaymentProvider

    provider = PaymentProvider.objects.create(name="Test", kind="test")
    page = admin_client.get(reverse("admin:billing_paymentprovider_change", args=[provider.pk]))
    assert page.status_code == 200
    assert reverse("v1:payment-webhook", args=[provider.pk]).encode() in page.content


# --- The route inventory -------------------------------------------------------------------------------------------

def test_every_route_has_a_url_name():
    """A route without a name cannot be linked with ``{% url %}`` (Rule 5.1)."""
    from apps.core.management.commands.route_inventory import collect

    unnamed = [path for _, path, name, _, _ in collect() if name == "(unnamed)"]
    assert unnamed == []


def test_the_inventory_command_lists_the_site(capsys):
    from django.core.management import call_command

    call_command("route_inventory")
    out = capsys.readouterr().out
    assert out.startswith("# Route inventory") and "## Staff" in out and "## API" in out
    assert "| `/staff/reports/` | `reports_staff:index` |" in out and "perm: view_reports" in out


# --- NUL bytes (PostgreSQL rejects them; SQLite accepts them, which hid the problem) -----------------------------------

def test_nul_bytes_are_stripped_from_queries_and_forms_before_views_see_them():
    from django.http import HttpResponse

    from apps.core.middleware import StripNullBytesMiddleware

    seen = {}

    def view(request):
        seen["get"], seen["post"] = request.GET.getlist("q"), request.POST.getlist("note")
        return HttpResponse("ok")

    middleware = StripNullBytesMiddleware(view)
    factory = RequestFactory()
    middleware(factory.get("/", {"q": ["a\x00b", "c"], "x\x00": "1"}))
    assert seen["get"] == ["ab", "c"]
    middleware(factory.post("/", {"note": "no\x00te"}))
    assert seen["post"] == ["note"]
    clean = factory.get("/", {"q": "fine"})
    assert middleware(clean).status_code == 200 and seen["get"] == ["fine"]


def test_a_nul_byte_in_a_search_box_or_form_is_not_a_server_error(client, manager, some_client):
    client.force_login(manager)
    for path in ("/staff/clients/", "/staff/affiliates/commissions/", "/staff/billing/invoices/", "/staff/support/tickets/",
                 "/staff/reports/orders/"):
        assert client.get(path, {"q": "a\x00b", "status": "x\x00"}).status_code < 500, path
    response = client.post("/staff/affiliates/commissions/1/reject/", {"note": "no\x00te"})
    assert response.status_code < 500
