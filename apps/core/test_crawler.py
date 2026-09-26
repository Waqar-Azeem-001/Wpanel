"""The link crawler (roadmap Rule 5.5 / Section 12.3): every role follows every link; nothing is broken."""
import pytest
from django.core import mail
from django.db import transaction
from django.http import HttpResponse
from django.test import override_settings
from django.urls import path

from apps.core import harness
from apps.core.management.commands.route_inventory import collect

pytestmark = pytest.mark.django_db

# Pages nobody can reach by clicking, with the reason. Everything else that answers GET must be reached by some role.
NOT_CLICKABLE = {
    "retired_": "old addresses that only redirect (tested in test_redirects)",
    "affiliates_public:referral": "the referral link /r/<code>/ is shared outside the site, not linked from it",
}
NOT_CLICKABLE_EXACT = {
    "billing_customer:test_gateway": "the simulated gateway's page, reached from a redirect after starting a test payment",
    "email_open_pixel": "the tracking pixel inside emails (tested with the email links)",
    "accounts:verify_email": "reached from the verification email (tested with the email links)",
    "accounts:password_reset_confirm": "reached from the password-reset email",
    "accounts:register": "the sign-up form is only offered to signed-out visitors",
    "accounts:login": "signed-in people are sent away from it; the anonymous crawl reaches it",
    "console:staff_users": "the old Staff & Roles address, kept for bookmarks; it redirects to the Users screen",
}


@pytest.fixture(scope="module")
def crawled(django_db_setup, django_db_blocker):
    """Build the world and crawl it once for the whole module (about half a minute); everything rolls back afterwards."""
    patch = pytest.MonkeyPatch()
    patch.setattr(transaction, "on_commit", lambda func, using=None, robust=False: func())  # deliver emails inline
    with django_db_blocker.unblock():
        atomic = transaction.atomic()
        atomic.__enter__()
        try:
            mail.outbox = []
            world = harness.build_world()
            world.emails = list(mail.outbox)
            world.crawlers = harness.crawl_everyone(world)
            yield world
        finally:
            transaction.set_rollback(True)
            atomic.__exit__(None, None, None)
            patch.undo()


def describe(findings):
    return [f"{f.role}: {f.path} -> {f.status} (linked from {f.found_on} by {f.kind})" for f in findings]


# --- The crawl --------------------------------------------------------------------------------------------------------

def test_the_world_has_all_eight_roles(crawled):
    assert list(crawled.people) == harness.ROLE_NAMES


@pytest.mark.parametrize("role", harness.ROLE_NAMES)
def test_no_role_meets_a_broken_link(crawled, role):
    """404 (a link to nothing), 500 (a page that crashes) or 403 (a link to a page this person may not open)."""
    crawler = crawled.crawlers[role]
    assert describe(crawler.findings) == []
    assert len(crawler.visited) > 15, "the crawl barely started; something is wrong with the fixture or the parser"


def test_every_role_gets_a_home_page_of_its_own_shape(crawled):
    reached = {role: c.reached_names for role, c in crawled.crawlers.items()}
    assert "public_home" in reached["anonymous"] or "home" in reached["anonymous"]
    assert "catalog_staff:product_list" in reached["manager"] and "catalog_staff:product_list" not in reached["support agent"]
    assert "accounts:profile" in reached["customer owner"]


def test_every_page_that_answers_get_is_reached_by_some_role(crawled):
    """The coverage floor: a page nobody links to is either dead or unreachable, and the crawl would never notice it."""
    reached = set().union(*(c.reached_names for c in crawled.crawlers.values()))
    missing = []
    for area, route, name, view, access in collect():
        if area in ("API", "Django admin") or not name or "POST only" in access:
            continue
        if name in reached or name in NOT_CLICKABLE_EXACT or any(name.startswith(p) for p in NOT_CLICKABLE):
            continue
        missing.append(f"{name} {route}")
    assert missing == [], "pages no role can click through to (link them, or allowlist them with a reason):\n" + "\n".join(missing)


# --- The crawler itself must be able to see a broken link --------------------------------------------------------------

def _page(links):
    return lambda request: HttpResponse(f"<html><body>{links}</body></html>")


handler404 = lambda request, exception: HttpResponse("missing", status=404)  # noqa: E731 - the site's own pages need its URLs
handler500 = lambda request: HttpResponse("crashed", status=500)  # noqa: E731

urlpatterns = [
    path("", _page('<a href="/ok/">ok</a><a href="/gone/">gone</a><a href="/boom/">boom</a><a href="/secret/">secret</a>'
                   '<form method="get" action="/search-gone/"></form><a href="https://elsewhere.example/x">out</a>'
                   '<a href="mailto:a@b.c">mail</a><a href="#top">top</a><a href="#">nowhere</a><a href="">empty</a>'
                   '<a href="javascript:void(0)">js</a><a href="#here">here</a><p id="here"></p><img src="/static/no-such-file.png">'
                   '<div hx-get="/hx-gone/"></div>')),
    path("ok/", _page("fine")),
    path("boom/", lambda request: 1 / 0),
    path("secret/", lambda request: HttpResponse(status=403)),
]


@override_settings(ROOT_URLCONF=__name__)
def test_the_crawler_reports_every_kind_of_broken_link_and_ignores_the_rest(db):
    crawler = harness.Crawler("anonymous", None).run()
    broken = {f.path: (f.status, f.kind) for f in crawler.findings}
    assert broken == {"/gone/": (404, "a"), "/boom/": (500, "a"), "/secret/": (403, "a"),
                      "/search-gone/": (404, "form"), "/hx-gone/": (404, "hx-get"),
                      "/static/no-such-file.png": (404, "img"), "#top": (0, "dead link"), "#": (0, "dead link"),
                      "(empty)": (0, "dead link"), "javascript:void(0)": (0, "dead link")}
    assert "/ok/" in crawler.visited and not any("elsewhere" in p or "mailto" in p for p in crawler.visited)


def test_the_parser_finds_every_kind_of_reference():
    parser = harness.LinkParser()
    parser.feed('<a href="/a">x</a><link rel="stylesheet" href="/c.css"><script src="/s.js"></script><img src="/i.png">'
                '<form action="/g"></form><form method="post" action="/p"></form><span hx-get="/h"></span><a name="x">')
    assert parser.found == [("a", "/a"), ("link", "/c.css"), ("script", "/s.js"), ("img", "/i.png"), ("form", "/g"),
                            ("hx-get", "/h")]


@pytest.mark.parametrize("url,expected", [
    ("/staff/x/?q=1", "/staff/x/?q=1"), ("https://localhost/a/", "/a/"), ("https://evil.example/a/", None),
    ("//evil.example/a/", None), ("mailto:a@b.c", None), ("tel:123", None), ("javascript:void(0)", None),
    ("../up/", "/staff/up/"), ("", "/staff/here/"), ("?page=2", "/staff/here/?page=2"),
])
def test_relative_and_foreign_links_are_understood(url, expected):
    assert harness.internal_target(url, "/staff/here/") == expected



# --- Links inside emails (Rule 5.4) -----------------------------------------------------------------------------------

@pytest.fixture
def emailed(crawled):
    return harness.email_links(crawled.emails)


def kind_of(url):
    for marker, kind in (("/e/o/", "pixel"), ("/password-reset/", "reset"), ("/verify-email/", "verify")):
        if marker in url:
            return kind
    return "page"


def test_the_world_sent_a_useful_range_of_emails(crawled, emailed):
    kinds = {kind_of(url) for _m, url in emailed}
    paths = " ".join(url for _m, url in emailed)
    assert len(crawled.emails) > 30 and {"pixel", "reset", "page"} <= kinds
    for fragment in ("/billing/invoices/", "/billing/quotes/", "/support/", "/cancellations/"):
        assert fragment in paths, fragment


def test_every_link_in_every_email_starts_with_the_site_address(emailed, settings):
    base = settings.SITE_URL.rstrip("/")
    assert [url for _m, url in emailed if not url.startswith(base + "/")] == []


def test_every_emailed_link_opens_for_the_person_it_was_sent_to(crawled, emailed, settings):
    from django.test import Client
    from django.urls import Resolver404, resolve
    from urllib.parse import urlparse

    from apps.accounts.models import User

    base = settings.SITE_URL.rstrip("/")
    checked = set()
    for message, url in emailed:
        path_and_query = url[len(base):]
        route = urlparse(path_and_query).path
        try:
            resolve(route)
        except Resolver404:
            pytest.fail(f"{message.subject!r} links to {url}, which is not a page on this site")
        recipient = message.to[0]
        if (recipient, path_and_query) in checked:
            continue
        checked.add((recipient, path_and_query))
        client = Client(HTTP_HOST=urlparse(base).netloc, raise_request_exception=False)
        kind = kind_of(url)
        if kind == "page":  # a person clicking a link in their inbox is signed in as themselves
            client.force_login(User.objects.get(email=recipient))
        response = client.get(path_and_query, follow=True)
        assert response.status_code == 200, f"{message.subject!r} to {recipient}: {url} -> {response.status_code}"
        if kind == "pixel":
            assert response["Content-Type"] == "image/gif"
        if kind == "reset":
            assert 'type="password"' in response.content.decode(), f"{url} does not offer a way to set a password"


def test_no_address_is_written_by_hand_in_a_template_or_in_code():
    """Every emailed or displayed link is built from SITE_URL and a URL name, never typed in (a typed one goes stale)."""
    import re
    from pathlib import Path

    from django.conf import settings

    root = Path(settings.BASE_DIR)
    offenders = []
    for base, pattern in (("templates", "*.html"), ("templates", "*.txt"), ("apps", "*.py")):
        for file in (root / base).rglob(pattern):
            rel = file.relative_to(root).as_posix()
            if rel == "apps/core/checks.py":  # the deploy check that insists SITE_URL is https names the scheme
                continue
            if "/tests/" in rel or "/test_" in rel or rel.endswith("tests.py") or "migrations" in rel or "vendor" in rel:
                continue
            for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"https?://", line):
                    offenders.append(f"{rel}:{number}: {line.strip()[:80]}")
    assert offenders == []


def test_the_deploy_check_insists_on_a_public_https_site_address(settings):
    from apps.core.checks import site_url_is_https

    for good in ("https://portal.example.com",):
        settings.SITE_URL = good
        assert site_url_is_https(None) == []
    for bad in ("http://portal.example.com", "http://localhost:8000", "https://localhost", "https://portal.example.com/", ""):
        settings.SITE_URL = bad
        assert [e.id for e in site_url_is_https(None)] == ["core.E002"], bad


# --- Links inside PDFs (Rule 5.4) -------------------------------------------------------------------------------------

def test_no_pdf_contains_a_link(crawled):
    """A clickable link inside a PDF cannot be crawled and cannot be kept current, so documents carry none."""
    from django.test import Client

    manager = crawled.people["manager"]
    client = Client(HTTP_HOST="localhost")
    client.force_login(manager)
    urls = [f"/staff/billing/invoices/{i.pk}/pdf/" for i in crawled.objects["invoices"].values()]
    urls += [f"/staff/billing/quotes/{q.pk}/pdf/" for q in crawled.objects["quotes"].values()]
    reports = sorted({p.split("?")[0] for p in crawled.crawlers["manager"].visited
                      if p.startswith("/staff/reports/") and p.count("/") == 4})
    urls += [f"{r}?export=pdf" for r in reports]
    assert len(urls) > 10 and reports
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200 and response["Content-Type"] == "application/pdf", url
        body = response.content
        assert body.startswith(b"%PDF") and b"/URI" not in body and b"/Annots" not in body, url
