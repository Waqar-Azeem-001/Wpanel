import re

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import AccountStatus, User
from apps.audit.models import AuditEvent
from conftest import PASSWORD

pytestmark = pytest.mark.django_db


def _link_path(message):
    return re.search(r"https?://[^/\s]+(/\S+)", message.body).group(1)


def _login(api, email, password=PASSWORD):
    return api.post("/api/v1/auth/login/", {"email": email, "password": password})


# --- Registration & verification ------------------------------------------------

def test_register_creates_customer_sends_verification_and_audits(api):
    response = api.post("/api/v1/auth/register/", {
        "email": "New@Example.com", "password": PASSWORD, "first_name": "Ada",
    })
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["email"] == "new@example.com"
    assert body["role"] == "customer"
    assert body["email_verified"] is False
    assert body["permissions"] == []

    user = User.objects.get(email="new@example.com")
    assert list(user.groups.values_list("name", flat=True)) == ["Customer"]
    assert not user.is_staff
    assert len(mail.outbox) == 1 and "Verify your email" in mail.outbox[0].subject
    assert AuditEvent.objects.filter(action="account.registered", target_id=str(user.pk)).exists()


def test_register_rejects_duplicate_email_case_insensitively(api, customer):
    response = api.post("/api/v1/auth/register/", {"email": "CUSTOMER@example.com", "password": PASSWORD})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "email_taken"


def test_register_rejects_weak_password(api):
    response = api.post("/api/v1/auth/register/", {"email": "a@example.com", "password": "password"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert not User.objects.filter(email="a@example.com").exists()


def test_verify_email_via_link_token(api):
    api.post("/api/v1/auth/register/", {"email": "v@example.com", "password": PASSWORD})
    token = _link_path(mail.outbox[0]).rstrip("/").split("/")[-1]
    response = api.post("/api/v1/auth/verify-email/", {"token": token})
    assert response.status_code == 200
    assert User.objects.get(email="v@example.com").is_email_verified
    assert AuditEvent.objects.filter(action="account.email_verified").count() == 1
    # Re-using the token is harmless and does not re-audit.
    assert api.post("/api/v1/auth/verify-email/", {"token": token}).status_code == 200
    assert AuditEvent.objects.filter(action="account.email_verified").count() == 1


def test_verification_token_for_old_email_is_rejected(api, customer):
    token = services.make_email_verification_token(customer)
    customer.email = "changed@example.com"
    customer.save()
    response = api.post("/api/v1/auth/verify-email/", {"token": token})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "token_invalid"


def test_tampered_verification_token_is_rejected(api):
    response = api.post("/api/v1/auth/verify-email/", {"token": "garbage"})
    assert response.status_code == 400


def test_resend_verification_skips_verified_users(api, customer):
    api.force_authenticate(customer)
    api.post("/api/v1/auth/resend-verification/")
    assert len(mail.outbox) == 1
    customer.email_verified_at = timezone.now()
    customer.save()
    api.post("/api/v1/auth/resend-verification/")
    assert len(mail.outbox) == 1


# --- Login / tokens -----------------------------------------------------------------

def test_login_returns_jwt_pair_usable_on_api(api, customer):
    response = _login(api, "Customer@Example.com")
    assert response.status_code == 200
    tokens = response.json()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    me = api.get("/api/v1/me/")
    assert me.status_code == 200 and me.json()["email"] == "customer@example.com"
    assert AuditEvent.objects.filter(action="auth.login", metadata__channel="api").exists()
    customer.refresh_from_db()
    assert customer.last_login is not None


def test_login_failure_is_generic_and_audited(api, customer):
    bad_password = _login(api, "customer@example.com", "wrong-password")
    unknown = _login(api, "nobody@example.com", "wrong-password")
    assert bad_password.status_code == unknown.status_code == 401
    assert bad_password.json()["error"]["message"] == unknown.json()["error"]["message"]
    reasons = set(AuditEvent.objects.filter(action="auth.login_failed").values_list("metadata__reason", flat=True))
    assert reasons == {"bad_password", "unknown_email"}
    for event in AuditEvent.objects.filter(action="auth.login_failed"):
        assert "password" not in event.metadata


def test_suspended_account_cannot_login(api, customer):
    customer.status = AccountStatus.SUSPENDED
    customer.save()
    assert _login(api, "customer@example.com").status_code == 401
    assert AuditEvent.objects.filter(action="auth.login_failed", metadata__reason="inactive_account").exists()


def test_refresh_rotates_and_logout_blacklists(api, customer):
    tokens = _login(api, "customer@example.com").json()
    refreshed = api.post("/api/v1/auth/refresh/", {"refresh": tokens["refresh"]})
    assert refreshed.status_code == 200
    new = refreshed.json()
    # Rotated: the old refresh token is now blacklisted.
    assert api.post("/api/v1/auth/refresh/", {"refresh": tokens["refresh"]}).status_code == 401

    api.credentials(HTTP_AUTHORIZATION=f"Bearer {new['access']}")
    assert api.post("/api/v1/auth/logout/", {"refresh": new["refresh"]}).status_code == 204
    api.credentials()
    assert api.post("/api/v1/auth/refresh/", {"refresh": new["refresh"]}).status_code == 401


def test_logout_rejects_another_users_refresh_token(api, customer, make_user):
    other = make_user("other@example.com")
    other_tokens = _login(api, "other@example.com").json()
    api.force_authenticate(customer)
    response = api.post("/api/v1/auth/logout/", {"refresh": other_tokens["refresh"]})
    assert response.status_code == 400
    api.force_authenticate(None)
    assert api.post("/api/v1/auth/refresh/", {"refresh": other_tokens["refresh"]}).status_code == 200
    assert other  # still active


# --- Passwords ------------------------------------------------------------------------

def test_password_reset_flow(api, customer):
    assert api.post("/api/v1/auth/password-reset/", {"email": "customer@example.com"}).status_code == 200
    assert len(mail.outbox) == 1
    uid, token = _link_path(mail.outbox[0]).strip("/").split("/")[-2:]
    response = api.post("/api/v1/auth/password-reset/confirm/", {
        "uid": uid, "token": token, "new_password": "An0ther-Strong-Pass",
    })
    assert response.status_code == 200
    assert _login(api, "customer@example.com", "An0ther-Strong-Pass").status_code == 200
    # Token is single-use: the password hash changed.
    again = api.post("/api/v1/auth/password-reset/confirm/", {
        "uid": uid, "token": token, "new_password": "Yet-An0ther-Pass!",
    })
    assert again.status_code == 400


def test_password_reset_does_not_reveal_unknown_email(api):
    response = api.post("/api/v1/auth/password-reset/", {"email": "nobody@example.com"})
    assert response.status_code == 200
    assert len(mail.outbox) == 0


def test_password_change_revokes_existing_tokens(api, customer):
    tokens = _login(api, "customer@example.com").json()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    wrong = api.post("/api/v1/auth/password-change/", {"old_password": "nope", "new_password": "An0ther-Strong-Pass"})
    assert wrong.status_code == 400 and wrong.json()["error"]["code"] == "invalid_password"

    ok = api.post("/api/v1/auth/password-change/", {"old_password": PASSWORD, "new_password": "An0ther-Strong-Pass"})
    assert ok.status_code == 200
    assert api.get("/api/v1/me/").status_code == 401  # old access token revoked
    api.credentials()
    assert api.post("/api/v1/auth/refresh/", {"refresh": tokens["refresh"]}).status_code == 401
    assert AuditEvent.objects.filter(action="auth.password_changed").exists()


# --- Profile ---------------------------------------------------------------------------

def test_profile_update_only_changes_allowed_fields(api, customer):
    api.force_authenticate(customer)
    response = api.patch("/api/v1/me/", {
        "first_name": "Grace", "phone": "+1 555 0100", "role": "super_admin", "email": "x@example.com",
        "status": "active",
    })
    assert response.status_code == 200
    customer.refresh_from_db()
    assert customer.first_name == "Grace" and customer.phone == "+1 555 0100"
    assert customer.role == "customer" and customer.email == "customer@example.com"
    event = AuditEvent.objects.get(action="account.profile_updated")
    assert event.metadata["fields"] == ["first_name", "phone"]
