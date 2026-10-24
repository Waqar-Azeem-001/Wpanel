"""Many people (or one impatient double click) at the same instant: nothing is created twice and nothing crashes.

Needs a real database with row locks, so it only runs on PostgreSQL (the CI and the server); the local SQLite run skips it.
"""
import threading
from unittest import mock

import pytest
from django.db import connection, connections
from django.test import Client

from apps.accounts import services as account_services
from apps.core.exceptions import ServiceError
from apps.orders.models import CartItem, Order

pytestmark = [pytest.mark.django_db(transaction=True),
              pytest.mark.skipif(connection.vendor != "postgresql", reason="needs PostgreSQL row locks")]

PEOPLE = 8


def burst(count, work):
    """Run ``work(i)`` in ``count`` threads released at the same instant. Returns each one's result (or the exception it raised)."""
    gate = threading.Barrier(count)
    results = [None] * count

    def run(i):
        try:
            gate.wait(timeout=20)
            results[i] = work(i)
        except Exception as exc:  # noqa: BLE001 - what each thread hit is the thing being measured
            results[i] = exc
        finally:
            connections.close_all()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results


def signed_in(user):
    browser = Client()
    browser.force_login(user)
    return browser


def no_crash(results):
    return [r for r in results if isinstance(r, Exception) or getattr(r, "status_code", 0) >= 500]


def test_a_double_clicked_checkout_makes_one_order(owner, shop):
    base = signed_in(owner)
    base.post("/cart/add/hosting/", {"product": shop["product"].pk, "cycle": "annual", "domain": "double.example"})
    browsers = [signed_in(owner) for _ in range(PEOPLE)]
    results = burst(PEOPLE, lambda i: browsers[i].post("/checkout/", {"payment_method": shop["bank"].code}))
    assert not no_crash(results), no_crash(results)
    assert Order.objects.count() == 1


def test_adding_the_same_plan_to_the_cart_at_once_adds_it_once(owner, shop):
    browsers = [signed_in(owner) for _ in range(PEOPLE)]
    results = burst(PEOPLE, lambda i: browsers[i].post(
        "/cart/add/hosting/", {"product": shop["product"].pk, "cycle": "annual", "domain": "twice.example"}))
    assert not no_crash(results), no_crash(results)
    assert CartItem.objects.filter(domain_name="twice.example").count() == 1


def test_the_same_email_signing_up_at_once_makes_one_account_and_the_rest_are_told_politely():
    results = burst(PEOPLE, lambda i: account_services.register_user(email="same@example.test", password="Str0ng-Passw0rd!x", first_name="Same"))
    from apps.accounts.models import User

    assert User.objects.filter(email="same@example.test").count() == 1
    refused = [r for r in results if isinstance(r, Exception)]
    assert all(isinstance(r, ServiceError) for r in refused), refused  # a clear "email taken", never a database error


def test_many_people_signing_in_at_the_same_moment_all_get_in(make_user):
    users = [make_user(f"person{i}@example.test") for i in range(30)]
    browsers = [Client() for _ in users]
    results = burst(len(users), lambda i: browsers[i].post("/account/login/", {"email": users[i].email, "password": "Str0ng-Passw0rd!x"}))
    assert not no_crash(results), no_crash(results)
    assert all(r.status_code == 302 for r in results)
    assert all("_auth_user_id" in browsers[i].session for i in range(len(users)))


def test_one_person_signing_in_from_several_places_at_once_is_never_an_error(make_user):
    user = make_user("many@example.test")
    browsers = [Client() for _ in range(PEOPLE)]
    results = burst(PEOPLE, lambda i: browsers[i].post("/account/login/", {"email": user.email, "password": "Str0ng-Passw0rd!x"}))
    assert not no_crash(results), no_crash(results)
    assert all(r.status_code == 302 for r in results)


def test_the_sign_in_device_record_survives_the_same_person_verifying_on_many_tabs_at_once(make_user):
    from django.utils import timezone

    from apps.accounts.models import SignInDevice

    user = make_user("tabs@example.test")
    results = burst(PEOPLE, lambda i: SignInDevice.objects.update_or_create(user=user, defaults={"token_hash": f"h{i}", "created_at": timezone.now()}))
    assert not [r for r in results if isinstance(r, Exception)], results
    assert SignInDevice.objects.filter(user=user).count() == 1


def test_a_double_clicked_ticket_is_opened_once(owner, client_obj, manager):
    from apps.support.models import Department, Ticket

    department = Department.objects.create(name="Support", is_active=True)
    browsers = [signed_in(owner) for _ in range(PEOPLE)]
    data = {"department": department.pk, "subject": "Site is down", "priority": "normal", "body": "Please help, nothing loads.", "submit_token": "one-page-view"}
    results = burst(PEOPLE, lambda i: browsers[i].post("/account/support/new/", data))
    assert not no_crash(results), no_crash(results)
    assert Ticket.objects.filter(subject="Site is down").count() == 1


def test_the_transfer_code_hourly_limit_holds_when_asked_for_all_at_once(owner, client_obj, settings):
    from apps.domains import services as domain_services
    from apps.domains.models import Domain, DomainStatus, RegistrarProvider

    provider = RegistrarProvider(name="OnlineNIC", kind="onlinenic", sandbox=False)
    provider.set_credentials({"user": "1", "password": "p", "apikey": "k"})
    provider.save()
    domain = Domain.objects.create(client=client_obj, name="race.com", status=DomainStatus.ACTIVE, provider_ref="x", is_locked=False)
    owner.set_password("Secret-pass-1")
    owner.save()
    asked = []

    def ask(i):
        with mock.patch("apps.domains.adapters.onlinenic.OnlineNicAdapter.unlock_domain", lambda *a, **k: None), \
                mock.patch("apps.domains.adapters.onlinenic.OnlineNicAdapter.get_auth_code", lambda *a, **k: asked.append(i) or "EPP"):
            return domain_services.get_transfer_code(owner, domain, "Secret-pass-1")

    results = burst(PEOPLE, ask)
    assert len(asked) <= domain_services.TRANSFER_CODE_PER_HOUR, f"{len(asked)} codes were handed out"
