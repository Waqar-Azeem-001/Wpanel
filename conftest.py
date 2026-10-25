import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from rest_framework.test import APIClient

from apps.accounts import services
from apps.accounts.models import User
from apps.accounts.roles import Role

PASSWORD = "Str0ng-Passw0rd!x"

# Settings that only a live deployment has. A developer's own .env never sets these; the live server's does.
PRODUCTION_ONLY_SETTINGS = ("DJANGO_SECURE_SSL_REDIRECT", "DJANGO_CSRF_TRUSTED_ORIGINS", "DJANGO_HSTS_SECONDS", "DJANGO_ADMIN_URL", "REDIS_PASSWORD",
                            "PREVIEW_PROXY_KEY", "SENTRY_DSN", "PANEL_BACKUP_S3_BUCKET", "PANEL_BACKUP_S3_ENDPOINT")
LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1", "postgres", "redis"}  # the names CI and a developer's machine use
OPT_IN = "WHPANEL_TESTS_ALLOW_REMOTE_SERVICES"  # set to 1 only on a throwaway machine whose database and Redis are for testing


def production_signs(environ, env_file_text="", settings_module=""):
    """Why this looks like the live server, as a list of reasons (empty = safe to test). The tests create and delete data, so they must never run against live services."""
    reasons = []
    if settings_module and not settings_module.endswith(".test"):
        reasons.append(f"DJANGO_SETTINGS_MODULE is {settings_module}, not config.settings.test")
    present = set(environ) | {line.split("=", 1)[0].strip() for line in env_file_text.splitlines() if "=" in line and not line.lstrip().startswith("#")}
    for name in PRODUCTION_ONLY_SETTINGS:
        if name in present and (environ.get(name) or name in env_file_text):
            reasons.append(f"{name} is set (only the live server has it)")
    if environ.get(OPT_IN) != "1":
        for name in ("DATABASE_URL", "REDIS_URL"):
            value = environ.get(name, "")
            host = (urlsplit(value).hostname or "") if value else ""
            if value and host not in LOCAL_HOSTS:
                reasons.append(f"{name} points at {host}, not at this machine")
    return reasons


def pytest_sessionstart(session):
    """Refuse to start when the environment is the live panel's (its .env, its database, its Redis). Runs before any test database is made."""
    root = Path(__file__).resolve().parent
    env_file = root / ".env"
    text = env_file.read_text(errors="ignore") if env_file.is_file() else ""
    reasons = production_signs(os.environ, text, os.environ.get("DJANGO_SETTINGS_MODULE", ""))
    if reasons:
        lines = ["Refusing to run the tests: this looks like the live server."] + ["  - " + r for r in reasons]
        lines.append(f"Run them with only the CI's three settings (DATABASE_URL, REDIS_URL, DJANGO_SECRET_KEY), or set {OPT_IN}=1 on a throwaway machine.")
        raise pytest.UsageError(chr(10).join(lines))


@pytest.fixture(autouse=True)
def fresh_cache():
    """Brand settings (and any other cached value) must not leak from one test into the next."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def no_shipped_brand_pictures(settings, tmp_path_factory):
    """The pictures shipped in static/img/brand would make every page show a logo; tests start from "nothing uploaded, nothing shipped" and opt in (see apps/branding/tests)."""
    settings.BRAND_ASSET_DIR = str(tmp_path_factory.mktemp("no-brand-pictures"))


@pytest.fixture(autouse=True)
def run_on_commit_immediately(monkeypatch):
    """Test DB transactions never commit; run on_commit callbacks (e.g. email delivery) inline."""
    from django.db import transaction

    monkeypatch.setattr(transaction, "on_commit", lambda func, using=None, robust=False: func())


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def make_user(db):
    def _make(email="user@example.com", role=Role.CUSTOMER, password=PASSWORD, **extra):
        user = User.objects.create_user(email=email, password=password, role=role, **extra)
        services.sync_role_membership(user)
        return user

    return _make


@pytest.fixture
def customer(make_user):
    return make_user("customer@example.com")


@pytest.fixture
def staff(make_user):
    """Factory: staff(role) -> user with that role."""

    def _staff(role):
        return make_user(f"{role}@example.com", role=role)

    return _staff


def auth(api, user):
    api.force_authenticate(user)
    return api


@pytest.fixture(autouse=True)
def _cpanel_tools_off_unless_a_test_turns_them_on(settings):
    """The developer's own .env may switch cPanel tools on for the live server; tests must start from the safe default (none)."""
    settings.HOSTING_CPANEL_CAPABILITIES = []


@pytest.fixture(autouse=True)
def _empty_cache_between_tests():
    """Remembered server answers are keyed by account id, and every test starts its ids again from 1."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _celery_join_flag_reset():
    """Tasks run in several threads at once (the concurrency tests) can leave Celery's process-wide "a task may not wait for another" flag set; every test starts with it off."""
    yield
    from celery._state import _set_task_join_will_block

    _set_task_join_will_block(False)
