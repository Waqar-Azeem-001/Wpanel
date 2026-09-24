import re

import pytest
from django.core import mail

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from conftest import PASSWORD

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("url", ["/account/login/", "/account/register/", "/account/password-reset/"])
def test_public_pages_render(client, url):
    response = client.get(url)
    assert response.status_code == 200
    assert b"<form" in response.content


def test_profile_requires_login(client):
    response = client.get("/account/profile/")
    assert response.status_code == 302 and "/account/login/" in response["Location"]


def test_web_register_logs_in_and_sends_verification(client):
    response = client.post("/account/register/", {
        "email": "web@example.com", "password": PASSWORD, "password_confirm": PASSWORD,
    })
    assert response.status_code == 302
    assert User.objects.filter(email="web@example.com").exists()
    assert len(mail.outbox) == 1
    profile = client.get("/account/profile/")
    assert b"web@example.com" in profile.content and b"Unverified" in profile.content


def test_web_register_shows_password_mismatch(client):
    response = client.post("/account/register/", {
        "email": "web@example.com", "password": PASSWORD, "password_confirm": PASSWORD + "x",
    })
    assert response.status_code == 200 and b"Passwords do not match" in response.content


def test_web_login_logout_audited(client, customer):
    bad = client.post("/account/login/", {"email": customer.email, "password": "wrong"})
    assert b"Invalid email or password" in bad.content
    ok = client.post("/account/login/", {"email": customer.email, "password": PASSWORD})
    assert ok.status_code == 302
    assert AuditEvent.objects.filter(action="auth.login", metadata__channel="web").exists()
    assert client.get("/account/logout/").status_code == 405  # logout is POST-only
    client.post("/account/logout/")
    assert AuditEvent.objects.filter(action="auth.logout", metadata__channel="web").exists()
    assert client.get("/account/profile/").status_code == 302


def test_web_login_ignores_offsite_next(client, customer):
    response = client.post("/account/login/?next=https://evil.example/", {
        "email": customer.email, "password": PASSWORD, "next": "https://evil.example/",
    })
    assert response["Location"] == "/account/profile/"


def test_web_verify_email_link(client):
    client.post("/account/register/", {"email": "v@example.com", "password": PASSWORD, "password_confirm": PASSWORD})
    path = re.search(r"https?://[^/\s]+(/\S+)", mail.outbox[0].body).group(1)
    response = client.get(path)
    assert response.status_code == 200 and b"Email verified" in response.content
    assert client.get("/account/verify-email/bogus/").status_code == 400


def test_web_password_reset_flow(client, customer):
    client.post("/account/password-reset/", {"email": customer.email})
    path = re.search(r"https?://[^/\s]+(/\S+)", mail.outbox[0].body).group(1)
    assert client.get(path).status_code == 200
    response = client.post(path, {"new_password": "An0ther-Strong-Pass", "new_password_confirm": "An0ther-Strong-Pass"})
    assert response.status_code == 302
    assert client.login(email=customer.email, password="An0ther-Strong-Pass")
    assert client.get(path).status_code == 400  # link is single-use


def test_web_password_change_keeps_session(client, customer):
    client.force_login(customer)
    response = client.post("/account/password-change/", {
        "old_password": PASSWORD, "new_password": "An0ther-Strong-Pass", "new_password_confirm": "An0ther-Strong-Pass",
    })
    assert response.status_code == 302
    assert client.get("/account/profile/").status_code == 200


def test_web_profile_update(client, customer):
    client.force_login(customer)
    client.post("/account/profile/", {"first_name": "Lin", "last_name": "", "phone": ""})
    customer.refresh_from_db()
    assert customer.first_name == "Lin"
