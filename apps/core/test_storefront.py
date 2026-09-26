"""The modern look: the storefront pages, the vendored font, the navigation bar with the logo, and the design tokens."""
import re
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.core.management import call_command
from django.urls import reverse

from apps.core.templatetags.ui import feature_lines
from apps.products.models import Product

pytestmark = pytest.mark.django_db

STATIC = Path(django_settings.BASE_DIR) / "static"


@pytest.fixture
def store(settings):
    settings.STORE_CURRENCY = "PKR"
    call_command("seed_webhostera", verbosity=0)


# --- The front page ---------------------------------------------------------------------------------------------------

def test_a_visitor_sees_the_storefront_on_the_home_page(client, store):
    response = client.get("/")
    page = response.content.decode()
    assert response.status_code == 200 and "Web hosting and domains that just work" in page
    assert reverse("domains_public:search") in page and 'name="domain"' in page and 'name="years" value="1"' in page
    assert 'aria-label="Breadcrumb"' not in page, "a hero page has no breadcrumb bar above it"
    assert page.count("<h1") == 1


def test_the_home_page_shows_the_four_cheapest_plans_cheapest_first(client, store):
    plans = client.get("/").context["products"]
    assert [p.name for p in plans] == ["Shared Starter", "WordPress Hosting", "Business Hosting", "Shared Grow"]
    prices = [min(x.price for x in p.prices.all()) for p in plans]
    assert prices == sorted(prices)


def test_the_plan_page_lists_every_active_plan_in_price_order(client, store):
    Product.objects.filter(name="Reseller Maximized").update(status="hidden")
    plans = client.get(reverse("catalog:product_list")).context["products"]
    assert len(plans) == 7 and "Reseller Maximized" not in [p.name for p in plans]
    prices = [min(x.price for x in p.prices.all()) for p in plans]
    assert prices == sorted(prices)


def test_a_plan_card_shows_its_type_price_features_and_a_working_button(client, store):
    page = client.get(reverse("catalog:product_list")).content.decode()
    starter = Product.objects.get(name="Shared Starter")
    assert "plan-card" in page and "Shared Hosting" in page and "PKR 600.00" in page and "billed monthly" in page
    assert "10 GB NVMe storage" in page and reverse("catalog:product_detail", args=[starter.slug]) in page
    assert client.get(reverse("catalog:product_detail", args=[starter.slug])).status_code == 200


def test_an_empty_shop_says_so_instead_of_showing_nothing(client, settings):
    assert b"Our plans are coming soon" in client.get("/").content
    assert b"No plans are available yet" in client.get(reverse("catalog:product_list")).content


def test_customers_and_staff_do_not_get_the_storefront_at_the_front_door(client, store, customer, staff):
    from apps.accounts.roles import Role

    client.force_login(customer)
    assert client.get("/").status_code == 302
    client.force_login(staff(Role.MANAGER))
    assert client.get("/").headers["Location"] == reverse("console:dashboard")


def test_feature_lines_split_a_description_into_clean_lines():
    assert feature_lines("  one \n\n two\n   \nthree") == ["one", "two", "three"]
    assert feature_lines("") == [] and feature_lines(None) == []


# --- Domain search ----------------------------------------------------------------------------------------------------

def test_the_domain_page_has_a_hero_search_and_price_tiles(client, store):
    response = client.get(reverse("domains_public:search"))
    page = response.content.decode()
    assert response.status_code == 200 and "hero-search" in page and "tld-tile" in page
    assert page.count("tld-tile") == 35 and ".com" in page and "PKR 4,849.00" in page
    assert 'aria-label="Breadcrumb"' not in page


def test_a_price_tile_mentions_renewal_only_when_it_differs(client, store):
    from apps.domains.models import TldPricing

    TldPricing.objects.filter(tld=".xyz").update(renew_price=4000)
    page = client.get(reverse("domains_public:search")).content.decode()
    assert page.count("renews at") == 1 and "renews at PKR 4,000.00" in page


def test_a_search_still_reports_availability_inside_the_new_page(client, store):
    response = client.get(reverse("domains_public:search"), {"domain": "definitely-free-name.com", "years": 1})
    assert response.status_code == 200 and b"hero-search" in response.content and b"is available" in response.content
    assert b'value="definitely-free-name.com"' in response.content


def test_a_bad_search_shows_the_error_in_the_hero_not_a_crash(client, store):
    response = client.get(reverse("domains_public:search"), {"domain": "", "years": 0})
    assert response.status_code == 200 and b"hero-search" in response.content


# --- Navigation and shells --------------------------------------------------------------------------------------------

def test_the_logo_is_in_the_navigation_bar_for_every_kind_of_visitor(client, store, customer, staff):
    from apps.accounts.roles import Role

    assert 'class="navbar-brand"' in client.get("/").content.decode()
    for user in (customer, staff(Role.MANAGER)):
        client.force_login(user)
        page = client.get(reverse("accounts:profile")).content.decode()
        assert re.search(r'<img src="/brand/logo/\?v=\d+" alt="">', page) and "Web Host Era" in page


def test_visitors_and_customers_get_the_light_bar_and_staff_the_dark_one(client, store, customer, staff):
    from apps.accounts.roles import Role

    assert "navbar-public" in client.get("/").content.decode()
    client.force_login(customer)
    page = client.get(reverse("accounts:profile")).content.decode()
    assert "rail-client" in page and "navbar-dark" not in page
    client.force_login(staff(Role.MANAGER))
    assert "rail-staff" in client.get(reverse("accounts:profile")).content.decode()


def test_without_a_logo_the_name_is_the_brand(client):
    page = client.get("/").content.decode()
    assert '<a class="navbar-brand"' in page and "<img" not in page.split("</nav>")[0]


# --- The font and the stylesheet --------------------------------------------------------------------------------------

def test_the_font_is_vendored_with_its_licence_and_the_stylesheet_points_at_real_files():
    css = (STATIC / "css" / "theme.css").read_text(encoding="utf-8")
    files = re.findall(r'url\("\.\./vendor/fonts/([^"]+)"\)', css)
    assert len(files) == 2 and all((STATIC / "vendor" / "fonts" / name).stat().st_size > 10_000 for name in files)
    licence = (STATIC / "vendor" / "fonts" / "LICENSE-plus-jakarta-sans.txt").read_text(encoding="utf-8")
    assert "SIL Open Font License" in licence
    assert "Plus Jakarta Sans" in css.split("--font-ui:")[1].split(";")[0]


def test_the_stylesheets_load_nothing_from_the_internet():
    for name in ("theme.css",):
        assert not re.search(r"https?://", (STATIC / "css" / name).read_text(encoding="utf-8")), name


def test_the_font_files_are_served_and_fingerprinted_in_production(tmp_path, monkeypatch):
    import importlib

    from django.contrib.staticfiles import finders

    assert finders.find("vendor/fonts/plus-jakarta-sans-latin-wght-normal.woff2")
    monkeypatch.setenv("DJANGO_SECRET_KEY", "test-only")
    from django.test import override_settings
    from django.utils.functional import empty

    prod = importlib.import_module("config.settings.prod")
    with override_settings(STORAGES=prod.STORAGES, STATIC_ROOT=str(tmp_path), DEBUG=False):
        from django.contrib.staticfiles import storage

        storage.staticfiles_storage._wrapped = empty
        try:
            call_command("collectstatic", interactive=False, verbosity=0)  # fails if the stylesheet names a missing file
            manifest = storage.staticfiles_storage.load_manifest()[0]
            assert "vendor/fonts/plus-jakarta-sans-latin-wght-normal.woff2" in manifest
        finally:
            storage.staticfiles_storage._wrapped = empty


def test_the_brand_colours_are_tokens_the_stylesheet_uses_not_literals(client, store):
    css = (STATIC / "css" / "theme.css").read_text(encoding="utf-8")
    assert "var(--brand-accent)" in css and "var(--brand-accent-text)" in css and "var(--brand-primary)" in css
    served = client.get(reverse("brand_css")).content.decode()
    assert "--brand-primary: #2a6af2;" in served and "--brand-accent: #c8fc35;" in served


def test_dark_mode_keeps_the_logo_readable():
    css = (STATIC / "css" / "theme.css").read_text(encoding="utf-8")
    assert re.search(r'\[data-bs-theme="dark"\] \.app-navbar \.navbar-brand img \{[^}]*background: #fff', css)


def test_no_page_hand_codes_the_old_navigation_colour():
    css = (STATIC / "css" / "theme.css").read_text(encoding="utf-8")
    assert ".app-navbar.navbar-client { background: var(--brand-primary); }" not in css
