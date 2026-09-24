import pytest
from django.core.cache import cache
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.core import crypto
from apps.core.permissions import HasPortalPermission


@pytest.mark.django_db
def test_health_reports_database_and_cache(api):
    response = api.get("/api/v1/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "cache": "ok"}}


@pytest.mark.django_db
def test_request_id_is_generated_and_echoed(api):
    generated = api.get("/api/v1/health/")
    assert len(generated["X-Request-ID"]) == 32
    echoed = api.get("/api/v1/health/", HTTP_X_REQUEST_ID="abc-123")
    assert echoed["X-Request-ID"] == "abc-123"
    rejected = api.get("/api/v1/health/", HTTP_X_REQUEST_ID="bad id with spaces")
    assert rejected["X-Request-ID"] != "bad id with spaces"


@pytest.mark.django_db
def test_unauthenticated_error_uses_standard_format(api):
    response = api.get("/api/v1/me/", HTTP_X_REQUEST_ID="req-1")
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "not_authenticated"
    assert error["request_id"] == "req-1"
    assert error["message"]


@pytest.mark.django_db
def test_validation_error_uses_standard_format(api):
    response = api.post("/api/v1/auth/login/", {"email": "not-an-email"})
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert set(error["details"]) == {"email", "password"}


@pytest.mark.django_db
def test_not_found_uses_standard_format(api, customer):
    api.force_authenticate(customer)
    response = api.get("/api/v1/notifications/999999/")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.django_db
def test_unversioned_api_path_does_not_exist(api):
    assert api.get("/api/health/").status_code == 404


class _NoDeclaration(APIView):
    permission_classes = [HasPortalPermission]


@pytest.mark.django_db
def test_portal_permission_fails_closed_without_declaration(make_user):
    from apps.accounts.roles import Role

    admin = make_user("admin@example.com", role=Role.SUPER_ADMIN)
    request = APIRequestFactory().get("/")
    request.user = admin
    assert HasPortalPermission().has_permission(request, _NoDeclaration()) is False


def test_credentials_encrypt_round_trip_and_are_not_plaintext():
    token = crypto.encrypt("s3cret")
    assert token and "s3cret" not in token
    assert crypto.decrypt(token) == "s3cret"
    assert crypto.encrypt("") == "" and crypto.decrypt("") == ""


def test_decrypt_with_wrong_key_raises(settings):
    token = crypto.encrypt("s3cret")
    settings.SECRET_KEY = "another-key"
    with pytest.raises(ValueError):
        crypto.decrypt(token)


def test_cache_backend_round_trip():
    cache.set("k", "v", 5)
    assert cache.get("k") == "v"


@pytest.mark.django_db
def test_api_docs_are_staff_only(api, customer, make_user):
    from apps.accounts.roles import Role

    assert api.get("/api/v1/schema/").status_code == 401
    api.force_authenticate(customer)
    assert api.get("/api/v1/schema/").status_code == 403
    api.force_authenticate(make_user("agent@example.com", role=Role.SUPPORT_AGENT))
    assert api.get("/api/v1/schema/").status_code == 200


def test_check_celery_command_round_trip():
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    call_command("check_celery", stdout=out)
    assert "pong" in out.getvalue()
