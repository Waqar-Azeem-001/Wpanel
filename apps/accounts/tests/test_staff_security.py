"""Staff must have two-factor sign-in on and are signed out when idle; customers are not affected."""
import time

import pytest
from django.test import Client

from apps.accounts.roles import Role
from apps.accounts.staff_security import LAST_SEEN
from conftest import PASSWORD

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def rules_on(settings):
    settings.REQUIRE_STAFF_2FA = True
    settings.STAFF_IDLE_TIMEOUT_MINUTES = 60


def login(user):
    browser = Client()
    browser.force_login(user)
    return browser


def test_a_staff_member_without_two_factor_is_sent_to_set_it_up(staff):
    manager = staff(Role.MANAGER)
    browser = login(manager)
    response = browser.get("/staff/")
    assert response.status_code == 302 and response["Location"].endswith("/account/mfa/setup/")
    assert browser.get("/account/mfa/setup/").status_code == 200  # the way out stays open
    assert browser.get("/account/profile/").status_code == 200


def test_the_admin_is_gated_too_and_the_customer_area_is_not(staff, customer, settings):
    admin = staff(Role.ADMIN)
    browser = login(admin)
    response = browser.get("/admin/")
    assert response.status_code == 302 and "/account/mfa/setup/" in response["Location"]
    assert login(customer).get("/account/profile/").status_code == 200


def test_with_two_factor_on_the_staff_area_opens(staff):
    manager = staff(Role.MANAGER)
    manager.mfa_enabled = True
    manager.save(update_fields=["mfa_enabled"])
    assert login(manager).get("/staff/").status_code == 200


def test_a_staff_session_idle_for_too_long_is_ended(staff):
    manager = staff(Role.MANAGER)
    manager.mfa_enabled = True
    manager.save(update_fields=["mfa_enabled"])
    browser = login(manager)
    assert browser.get("/staff/").status_code == 200
    session = browser.session
    session[LAST_SEEN] = time.time() - 61 * 60
    session.save()
    response = browser.get("/staff/")
    assert response.status_code == 302 and "/account/login/" in response["Location"]
    assert browser.get("/staff/").status_code == 302  # really signed out


def test_the_switch_can_turn_both_rules_off(staff, settings):
    settings.REQUIRE_STAFF_2FA = False
    settings.STAFF_IDLE_TIMEOUT_MINUTES = 0
    browser = login(staff(Role.MANAGER))
    session = browser.session
    session[LAST_SEEN] = time.time() - 10 * 3600
    session.save()
    assert browser.get("/staff/").status_code == 200


def test_the_admin_address_can_be_moved(settings):
    from apps.accounts.staff_security import _gated_prefixes

    settings.ADMIN_URL = "secret-door-4f2/"
    assert "/secret-door-4f2/" in _gated_prefixes()
