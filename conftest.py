import pytest
from rest_framework.test import APIClient

from apps.accounts import services
from apps.accounts.models import User
from apps.accounts.roles import Role

PASSWORD = "Str0ng-Passw0rd!x"


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
