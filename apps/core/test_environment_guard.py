"""The test suite refuses to start on the live server's settings, database or Redis."""
import pytest

from conftest import OPT_IN, production_signs


def test_a_developer_machine_and_the_ci_pass():
    assert production_signs({"DATABASE_URL": "postgres://u:p@localhost:5432/whpanel", "REDIS_URL": "redis://localhost:6379/0", "DJANGO_SECRET_KEY": "x"},
                            "DJANGO_DEBUG=True\nDJANGO_ALLOWED_HOSTS=localhost\nSITE_URL=http://localhost:8000\n", "config.settings.test") == []


@pytest.mark.parametrize("environ,text,module", [
    ({"DJANGO_SECURE_SSL_REDIRECT": "True"}, "", "config.settings.test"),
    ({}, "DJANGO_CSRF_TRUSTED_ORIGINS=https://x\n", "config.settings.test"),
    ({"REDIS_PASSWORD": "secret"}, "", "config.settings.test"),
    ({"DATABASE_URL": "postgres://u:p@db:5432/whpanel"}, "", "config.settings.test"),
    ({"REDIS_URL": "redis://:pw@cache.internal:6379/0"}, "", "config.settings.test"),
    ({}, "", "config.settings.prod"),
])
def test_anything_that_looks_like_the_live_panel_stops_the_run(environ, text, module):
    assert production_signs(environ, text, module)


def test_a_throwaway_machine_can_opt_in_to_a_remote_database():
    assert production_signs({"DATABASE_URL": "postgres://u:p@scratch-db:5432/x", OPT_IN: "1"}, "", "config.settings.test") == []
    assert production_signs({"DATABASE_URL": "postgres://u:p@scratch-db:5432/x", OPT_IN: "1", "DJANGO_ADMIN_URL": "manage-x/"}, "", "config.settings.test")  # still not for live settings
