"""Brand settings: reading, validation (colour contrast, image content), serving, the staff page, the API and emails."""
from io import BytesIO

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.branding import services
from apps.branding.models import BrandSettings
from apps.core.exceptions import ServiceError

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin(staff):
    return staff(Role.ADMIN)


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


def image(fmt="PNG", size=(64, 32), color=(30, 90, 200)):
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return buffer.getvalue()


# --- Reading ----------------------------------------------------------------------------------------------------------

def test_the_defaults_come_from_settings(settings):
    settings.SITE_NAME = "Acme Hosting"
    brand = services.get()
    assert brand.name == "Acme Hosting" and brand.primary == "#2459d6" and not brand.has_logo and not brand.has_favicon
    assert brand.date_format == "%d %b %Y" and brand.money_format == "code_before"


def test_derived_colours_follow_the_primary(admin):
    services.save_settings(admin, values={"primary_color": "#123456"})
    brand = services.get()
    assert brand.primary_rgb == "18, 52, 86"
    lum = services._luminance
    assert lum(brand.primary_dark) < lum(brand.primary) < lum(brand.primary_subtle)  # a darker hover, a lighter tint


def test_reading_the_brand_never_fails_a_page(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(BrandSettings, "load", classmethod(lambda cls: broken()))
    cache.clear()
    assert services.get().primary == "#2459d6"  # the defaults, not an exception


def test_the_brand_is_cached_and_the_cache_is_cleared_on_change(admin, django_assert_num_queries):
    services.get()
    with django_assert_num_queries(0):
        services.get()
    services.save_settings(admin, values={"site_name": "New Name"})
    assert services.get().name == "New Name"


# --- Changing ---------------------------------------------------------------------------------------------------------

def test_saving_changes_the_brand_bumps_the_version_and_is_audited(admin):
    before = services.get().version
    row = services.save_settings(admin, values={"site_name": " Zed Hosting ", "support_email": "help@zed.test",
                                                "footer_text": "Zed Ltd, Karachi", "date_format": "%Y-%m-%d",
                                                "money_format": "code_after", "accent_color": "#0b6b3a"})
    assert (row.site_name, row.support_email, row.date_format, row.money_format) == (
        "Zed Hosting", "help@zed.test", "%Y-%m-%d", "code_after")
    assert services.get().version == before + 1
    event = AuditEvent.objects.get(action="brand.updated")
    assert event.actor == admin and "site_name" in event.metadata["changed"]
    services.save_settings(admin, values={"site_name": "Zed Hosting"})  # nothing changed: no new version, no audit row
    assert services.get().version == before + 1 and AuditEvent.objects.filter(action="brand.updated").count() == 1


def test_only_people_who_manage_settings_may_change_it(manager, customer):
    for user in (manager, customer):
        with pytest.raises(ServiceError) as exc:
            services.save_settings(user, values={"site_name": "Hacked"})
        assert exc.value.code == "permission_denied"
    assert services.get().name != "Hacked"


@pytest.mark.parametrize("bad", ["red", "#12345", "#gggggg", "123456", "#1234567", ""])
def test_a_colour_must_be_a_hex_colour(admin, bad):
    with pytest.raises(ValidationError):
        services.save_settings(admin, values={"primary_color": bad})
    assert services.get().primary == "#2459d6"


def test_a_colour_white_text_cannot_be_read_on_is_refused(admin):
    for light in ("#ffee00", "#aaaaaa", "#ffffff", "#7fbfff"):
        with pytest.raises(ValidationError) as exc:
            services.save_settings(admin, values={"primary_color": light})
        assert "too light" in str(exc.value) and "contrast" in str(exc.value)
    with pytest.raises(ValidationError):
        services.save_settings(admin, values={"accent_color": "#808080"})  # a mid-tone: neither white nor dark text reads on it
    assert services.get().primary == "#2459d6"
    services.save_settings(admin, values={"primary_color": "#1a1a1a"})  # dark is fine
    assert services.contrast_ratio("#2459d6", "#ffffff") >= 4.5


def test_the_formats_are_limited_to_the_offered_choices(admin):
    for values in ({"date_format": "%Y"}, {"money_format": "crypto"}, {"support_email": "not-an-email"},
                   {"footer_text": "x" * 301}):
        with pytest.raises(ValidationError):
            services.save_settings(admin, values=values)


# --- Images -----------------------------------------------------------------------------------------------------------

def test_a_valid_logo_and_favicon_are_stored_and_served(admin, client):
    png, ico = image("PNG"), image("ICO", (32, 32))
    services.save_settings(admin, logo=png, favicon=ico)
    brand = services.get()
    assert brand.has_logo and brand.has_favicon
    response = client.get(reverse("brand_logo"))
    assert response.status_code == 200 and response["Content-Type"] == "image/png" and response.content == png
    assert response["X-Content-Type-Options"] == "nosniff" and "sandbox" in response["Content-Security-Policy"]
    assert "max-age" in response["Cache-Control"]
    assert client.get(reverse("brand_favicon"))["Content-Type"] == "image/x-icon"
    etag = response["ETag"]
    assert client.get(reverse("brand_logo"), HTTP_IF_NONE_MATCH=etag).status_code == 304


@pytest.mark.parametrize("fmt,content_type", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("GIF", "image/gif"),
                                               ("WEBP", "image/webp")])
def test_every_accepted_image_type_is_recognised_by_content(fmt, content_type):
    assert services.validate_image("logo", image(fmt)) == content_type


@pytest.mark.parametrize("data,reason", [
    (b"", "empty"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "SVG"),
    (b"\x89PNG\r\n\x1a\n" + b"not really a png", "not a valid image"),
    (b"GIF89a" + b"\x00" * 20, "not a valid image"),
    (b"MZ\x90\x00 an executable", "PNG, JPEG"),
    (b"<html><script>alert(1)</script></html>", "PNG, JPEG"),
])
def test_anything_that_is_not_really_an_image_is_refused(data, reason):
    with pytest.raises(ValidationError) as exc:
        services.validate_image("logo", data)
    assert reason.lower() in str(exc.value).lower()


def test_image_size_limits(admin):
    with pytest.raises(ValidationError) as exc:
        services.validate_image("logo", image("PNG", (2100, 20)))
    assert "2048 pixels" in str(exc.value)
    with pytest.raises(ValidationError) as exc:
        services.validate_image("favicon", image("PNG", (600, 600)))
    assert "512 pixels" in str(exc.value)
    with pytest.raises(ValidationError) as exc:
        services.validate_image("logo", image("PNG", (600, 600)) + b"\x00" * (600 * 1024))
    assert "512 KB" in str(exc.value)
    with pytest.raises(ValidationError):
        services.validate_image("logo", image("ICO", (32, 32)))  # an icon is for the favicon only


def test_a_bad_upload_saves_nothing(admin):
    with pytest.raises(ValidationError):
        services.save_settings(admin, values={"site_name": "Changed"}, logo=b"<svg/>")
    assert services.get().name != "Changed" and not services.get().has_logo


def test_removing_an_image(admin, client):
    services.save_settings(admin, logo=image("PNG"))
    services.save_settings(admin, remove_logo=True)
    assert not services.get().has_logo and client.get(reverse("brand_logo")).status_code == 404
    assert services.image("logo") is None


def test_the_stylesheet_carries_the_tokens(admin, client):
    services.save_settings(admin, values={"primary_color": "#123456"})
    response = client.get(reverse("brand_css"))
    assert response.status_code == 200 and response["Content-Type"].startswith("text/css")
    assert "--brand-primary: #123456;" in response.content.decode() and "--brand-primary-rgb: 18, 52, 86;" in response.content.decode()
    assert "--brand-primary-light-rgb: " in response.content.decode()  # the lighter shade dark mode uses for links
    assert response["Cache-Control"].startswith("public") and response["X-Content-Type-Options"] == "nosniff"


# --- The staff page ---------------------------------------------------------------------------------------------------

def test_the_brand_page_is_for_people_who_view_settings(client, admin, manager, customer):
    url = reverse("brand_staff:settings")
    client.force_login(manager)
    assert client.get(url).status_code == 403
    client.force_login(customer)
    assert client.get(url).status_code == 403
    client.logout()
    assert client.get(url)["Location"].startswith("/account/login/")
    client.force_login(admin)
    page = client.get(url)
    assert page.status_code == 200 and b"Save brand settings" in page.content and b'type="color"' in page.content


def test_saving_the_brand_from_the_page_including_an_upload(client, admin):
    client.force_login(admin)
    logo = SimpleUploadedFile("logo.png", image("PNG"), content_type="image/png")
    response = client.post(reverse("brand_staff:settings"), {
        "site_name": "Page Hosting", "primary_color": "#123456", "accent_color": "#0b6b3a", "support_email": "a@b.test",
        "footer_text": "Footer", "date_format": "%Y-%m-%d", "money_format": "plain", "logo": logo}, follow=True)
    assert b"Brand settings saved." in response.content and b"Page Hosting" in response.content
    brand = services.get()
    assert (brand.name, brand.date_format, brand.has_logo) == ("Page Hosting", "%Y-%m-%d", True)
    assert b'<img src="/brand/logo/?v=' in response.content  # the navigation now shows the logo


def test_the_page_explains_a_refused_colour_or_image(client, admin):
    client.force_login(admin)
    base = {"site_name": "", "primary_color": "#ffee00", "accent_color": "#1f7a4d", "support_email": "",
            "footer_text": "", "date_format": "%d %b %Y", "money_format": "code_before"}
    page = client.post(reverse("brand_staff:settings"), base)
    assert page.status_code == 200 and b"too light" in page.content and services.get().primary == "#2459d6"
    svg = SimpleUploadedFile("logo.svg", b"<svg/>", content_type="image/svg+xml")
    page = client.post(reverse("brand_staff:settings"), {**base, "primary_color": "#123456", "logo": svg})
    assert b"SVG is not accepted" in page.content and not services.get().has_logo


def test_a_manager_cannot_post_the_brand_page(client, manager):
    client.force_login(manager)
    assert client.post(reverse("brand_staff:settings"), {"site_name": "x"}).status_code == 403


# --- The API ----------------------------------------------------------------------------------------------------------

def test_the_api_publishes_the_brand_without_signing_in(api, admin):
    services.save_settings(admin, values={"site_name": "Api Hosting", "primary_color": "#123456"}, logo=image("PNG"))
    data = api.get("/api/v1/brand/").json()
    assert data["name"] == "Api Hosting" and data["primary_color"] == "#123456" and data["logo_url"].startswith("/brand/logo/?v=")
    assert data["favicon_url"] is None and data["version"] >= 2 and "primary_dark_color" in data


def test_the_api_change_needs_manage_settings(api, admin, manager, customer):
    assert api.patch("/api/v1/brand/", {"site_name": "x"}, format="json").status_code == 403  # anonymous
    for user in (manager, customer):
        api.force_authenticate(user)
        assert api.patch("/api/v1/brand/", {"site_name": "x"}, format="json").status_code == 403
    api.force_authenticate(admin)
    ok = api.patch("/api/v1/brand/", {"site_name": "Changed", "footer_text": "F"}, format="json")
    assert ok.status_code == 200 and ok.json()["name"] == "Changed" and ok.json()["footer_text"] == "F"
    bad = api.patch("/api/v1/brand/", {"primary_color": "#ffee00"}, format="json")
    assert bad.status_code == 400 and services.get().primary == "#2459d6"
    assert api.patch("/api/v1/brand/", {"date_format": "%Q"}, format="json").status_code == 400


def test_the_api_accepts_an_image_upload_and_removal(api, admin):
    api.force_authenticate(admin)
    upload = SimpleUploadedFile("f.png", image("PNG", (32, 32)), content_type="image/png")
    response = api.patch("/api/v1/brand/", {"favicon": upload}, format="multipart")
    assert response.status_code == 200 and response.json()["favicon_url"].startswith("/brand/favicon/")
    bad = api.patch("/api/v1/brand/", {"logo": SimpleUploadedFile("l.svg", b"<svg/>")}, format="multipart")
    assert bad.status_code == 400
    removed = api.patch("/api/v1/brand/", {"remove_favicon": True}, format="json")
    assert removed.json()["favicon_url"] is None


# --- Where the brand shows up -----------------------------------------------------------------------------------------

def test_every_page_carries_the_brand(client, admin, settings):
    services.save_settings(admin, values={"site_name": "Shown Hosting", "footer_text": "Shown footer text"},
                           favicon=image("PNG", (32, 32)))
    page = client.get(reverse("accounts:login")).content.decode()
    assert "Shown Hosting" in page and "Shown footer text" in page and 'href="/brand.css?v=' in page
    assert 'rel="icon" href="/brand/favicon/?v=' in page


def test_emails_use_the_brand(admin, customer):
    from apps.notifications import services as notifications

    services.save_settings(admin, values={"site_name": "Mail Hosting", "primary_color": "#123456",
                                          "footer_text": "Mail footer", "support_email": "help@mail.test"},
                           logo=image("PNG"))
    mail.outbox.clear()
    message = notifications.dispatch("account.verification", user=customer, context={"link": "http://x/y/"}).message
    sent = mail.outbox[0]
    html = sent.alternatives[0][0]
    assert "Mail Hosting" in sent.subject and "#123456" in html and "Mail footer" in html and "help@mail.test" in html
    assert "/brand/logo/?v=" in html and message is not None
