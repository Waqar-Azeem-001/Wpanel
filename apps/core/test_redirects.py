"""Old addresses keep working (Rule 5.7), sign-in returns people only to this site, and static files are fingerprinted (5.4)."""
import importlib
import re

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.urls import resolve, reverse

from apps.core.redirects import RETIRED

pytestmark = pytest.mark.django_db


# --- Retired and vanity paths -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("old,name,deep", RETIRED)
def test_every_retired_path_answers_301_to_a_page_that_exists(client, old, name, deep):
    response = client.get("/" + old)
    assert response.status_code == 301 and response["Location"] == reverse(name)
    final = client.get(response["Location"])
    assert final.status_code == 200, "the retired path leads to a page that does not open"


@pytest.mark.parametrize("old,name,deep", [r for r in RETIRED if r[2]])
def test_deep_retired_paths_carry_the_rest_of_the_address(client, old, name, deep):
    response = client.get(f"/{old}starter/")
    assert response.status_code == 301 and response["Location"] == reverse(name) + "starter/"


def test_the_query_string_survives_the_redirect(client):
    assert client.get("/login/?next=/account/profile/").headers["Location"] == "/account/login/?next=/account/profile/"
    assert client.get("/store/?q=a%26b").headers["Location"] == "/products/?q=a%26b"


def test_a_retired_path_cannot_be_turned_into_an_open_redirect(client):
    for hostile in ("/store//evil.example/", "/knowledgebase///evil.example", "/store/\\evil.example/"):
        location = client.get(hostile).headers["Location"]
        assert location.startswith("/products/") or location.startswith("/help/"), location


def test_the_stated_vanity_paths_are_all_there():
    assert {old for old, _n, _d in RETIRED} == {"login/", "register/", "store/", "knowledgebase/"}


def test_retired_paths_have_names_and_no_existing_name_changed():
    for old, name, deep in RETIRED:
        resolved = resolve("/" + old)
        assert resolved.url_name.startswith("retired_")
        assert reverse(name)  # the current page keeps its name


def test_every_retired_path_still_redirects_when_signed_in(client, customer):
    client.force_login(customer)
    assert client.get("/login/").status_code == 301


# --- Safe "next" ------------------------------------------------------------------------------------------------------

HOSTILE = ["//evil.example/x", "https://evil.example/x", "http://evil.example", "///evil.example", "/\\evil.example",
           "\\\\evil.example", "javascript:alert(1)", "data:text/html,x", "//localhost.evil.example/"]


@pytest.mark.parametrize("target", HOSTILE)
def test_sign_in_never_sends_people_to_another_site(client, customer, target):
    response = client.post("/account/login/", {"email": customer.email, "password": "Str0ng-Passw0rd!x", "next": target})
    assert response.status_code == 302 and response["Location"] == reverse("accounts:profile")


@pytest.mark.parametrize("target", HOSTILE)
def test_a_hostile_next_in_the_address_is_ignored_too(client, customer, target):
    response = client.post(f"/account/login/?next={target}", {"email": customer.email, "password": "Str0ng-Passw0rd!x"})
    assert response["Location"] == reverse("accounts:profile")


def test_sign_in_returns_to_the_page_that_asked_for_it(client, customer):
    response = client.post("/account/login/", {"email": customer.email, "password": "Str0ng-Passw0rd!x",
                                               "next": "/account/billing/invoices/"})
    assert response["Location"] == "/account/billing/invoices/"


def test_the_login_form_escapes_a_hostile_next(client):
    page = client.get("/account/login/", {"next": '"><script>alert(1)</script>'}).content.decode()
    assert "<script>alert(1)" not in page


@pytest.mark.parametrize("path", ["/account/profile/", "/account/billing/invoices/", "/account/support/",
                                  "/staff/clients/", "/staff/billing/invoices/", "/staff/reports/", "/staff/settings/brand/",
                                  "/account/hosting/", "/account/domains/", "/staff/lifecycle/"])
def test_a_protected_page_sends_a_visitor_to_sign_in_and_back(client, path):
    response = client.get(path)
    assert response.status_code == 302 and response["Location"] == f"/account/login/?next={path}", path


# --- Static files -----------------------------------------------------------------------------------------------------

def test_production_settings_use_fingerprinted_static_files(monkeypatch):
    monkeypatch.setenv("DJANGO_SECRET_KEY", "test-only")
    prod = importlib.import_module("config.settings.prod")
    assert prod.STORAGES["staticfiles"]["BACKEND"] == "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"


def test_collectstatic_with_fingerprinting_succeeds_and_every_template_file_is_in_the_manifest(tmp_path, monkeypatch):
    """The build fails if a stylesheet names a missing file; the page then names files by their fingerprinted names."""
    from django.conf import settings
    from django.contrib.staticfiles.storage import staticfiles_storage  # noqa: F401 - imported to reset below
    from django.utils.functional import empty

    monkeypatch.setenv("DJANGO_SECRET_KEY", "test-only")
    prod = importlib.import_module("config.settings.prod")
    with override_settings(STORAGES=prod.STORAGES, STATIC_ROOT=str(tmp_path), DEBUG=False):
        from django.contrib.staticfiles import storage

        storage.staticfiles_storage._wrapped = empty
        try:
            call_command("collectstatic", interactive=False, verbosity=0)
            manifest = storage.staticfiles_storage.load_manifest()[0]
            assert re.search(r"css/theme\.[0-9a-f]{12}\.css", manifest["css/theme.css"])
            for name in ("css/theme.css", "js/theme.js"):
                assert name in manifest, name
            assert storage.staticfiles_storage.url("css/theme.css") == settings.STATIC_URL + manifest["css/theme.css"]
            page_names = set(re.findall(r"""\{%\s*static\s+['"]([^'"]+)['"]""", "".join(
                p.read_text(encoding="utf-8") for p in (settings.BASE_DIR / "templates").rglob("*.html"))))
            assert page_names and page_names <= set(manifest), sorted(page_names - set(manifest))
        finally:
            storage.staticfiles_storage._wrapped = empty
