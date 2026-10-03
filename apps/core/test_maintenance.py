"""
Maintenance mode (Phase 15 "Admin Operations"): a staff toggle (Setup > Brand > Maintenance) that shows visitors
and customers the dependency-free maintenance page, while staff keep using the site and the API stays up.
"""
import pytest
from django.urls import reverse

from apps.accounts.roles import Role
from apps.branding import services as branding

pytestmark = pytest.mark.django_db


def turn_on(admin):
    branding.save_settings(admin, values={"maintenance_mode": True})


@pytest.fixture
def admin(staff):
    return staff(Role.ADMIN)


def test_off_by_default_every_page_is_normal(client):
    response = client.get(reverse("home"))
    assert response.status_code != 503


def test_on_an_anonymous_visitor_sees_the_maintenance_page(client, admin):
    turn_on(admin)
    response = client.get(reverse("home"))
    assert response.status_code == 503 and "We will be right back" in response.content.decode()


def test_on_a_signed_in_customer_also_sees_it(client, admin, customer):
    turn_on(admin)
    client.force_login(customer)
    response = client.get(reverse("home"))
    assert response.status_code == 503


def test_on_staff_are_unaffected(client, admin):
    turn_on(admin)
    client.force_login(admin)
    response = client.get(reverse("home"))
    assert response.status_code != 503


def test_the_sign_in_page_stays_reachable_so_staff_can_get_in(client, admin):
    turn_on(admin)
    response = client.get(reverse("accounts:login"))
    assert response.status_code != 503


def test_brand_assets_the_sign_in_page_needs_stay_reachable(client, admin):
    turn_on(admin)
    for name in ("brand_css", "brand_logo", "brand_favicon"):
        response = client.get(reverse(name))
        assert response.status_code != 503


def test_the_api_is_never_affected(client, admin):
    turn_on(admin)
    response = client.get("/api/v1/products/")
    assert response.status_code != 503


def test_turning_it_back_off_restores_every_page(client, admin):
    turn_on(admin)
    branding.save_settings(admin, values={"maintenance_mode": False})
    response = client.get(reverse("home"))
    assert response.status_code != 503
