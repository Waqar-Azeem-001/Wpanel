import pytest
from django.db import transaction

from apps.core import harness


@pytest.fixture(scope="module")
def world(django_db_setup, django_db_blocker):
    """One customer with an object in every status (see harness.py), built once per test module and rolled back after."""
    patch = pytest.MonkeyPatch()
    patch.setattr(transaction, "on_commit", lambda func, using=None, robust=False: func())
    with django_db_blocker.unblock():
        atomic = transaction.atomic()
        atomic.__enter__()
        try:
            yield harness.build_world()
        finally:
            transaction.set_rollback(True)
            atomic.__exit__(None, None, None)
            patch.undo()
