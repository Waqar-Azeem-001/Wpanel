"""The database itself, not only the sign-up form, refuses two accounts whose emails differ by capital letters."""
import pytest
from django.db import IntegrityError, transaction

from apps.accounts.models import User

pytestmark = pytest.mark.django_db


def test_the_database_refuses_an_email_that_differs_only_by_case(make_user):
    make_user("Case@Example.test")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.bulk_create([User(email="CASE@example.TEST", password="x")])  # bulk_create skips save(), so only the constraint can stop it



def test_the_migration_check_passes_on_clean_data_and_names_a_clash_if_there_were_one(make_user):
    import importlib

    from django.apps import apps as django_apps

    migration = importlib.import_module("apps.accounts.migrations.0008_email_case_insensitive_unique")
    make_user("one@example.test")
    assert migration.refuse_if_duplicates(django_apps, None) is None

    class Fake:
        class objects:
            @staticmethod
            def order_by(_):
                class Rows:
                    @staticmethod
                    def values_list(*_):
                        return [(1, "A@x.test"), (2, "a@x.test")]

                return Rows

    class FakeApps:
        @staticmethod
        def get_model(*_):
            return Fake

    with pytest.raises(RuntimeError, match="a@x.test"):
        migration.refuse_if_duplicates(FakeApps, None)
