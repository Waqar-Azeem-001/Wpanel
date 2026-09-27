import pytest
from rest_framework.test import APIClient

from apps.accounts import services
from apps.accounts.models import User
from apps.accounts.roles import STAFF_ROLES, Role

PASSWORD = "Str0ng-Passw0rd!x"


@pytest.fixture(autouse=True)
def fresh_cache():
    """Brand settings (and any other cached value) must not leak from one test into the next."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


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
        # Two-factor sign-in is mandatory for staff (Phase 16): a fixture staff account models one that has already
        # completed that setup, so existing tests keep exercising the page behind it rather than the setup redirect.
        # A test for the redirect itself passes mfa_enabled=False explicitly, which this default never overrides.
        extra.setdefault("mfa_enabled", role in STAFF_ROLES)
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
