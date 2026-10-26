"""Many people (or one impatient double click) at the same instant: nothing is created twice and nothing crashes.

Needs a real database with row locks, so it only runs on PostgreSQL (the CI and the server); the local SQLite run skips it.
"""
import threading

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


def test_the_transfer_code_hourly_limit_holds_when_asked_for_all_at_once(owner, client_obj, settings, monkeypatch):
    from apps.domains import services as domain_services
    from apps.domains.models import Domain, DomainStatus, RegistrarProvider

    provider = RegistrarProvider(name="OnlineNIC", kind="onlinenic", sandbox=False)
    provider.set_credentials({"user": "1", "password": "p", "apikey": "k"})
    provider.save()
    domain = Domain.objects.create(client=client_obj, name="race.com", status=DomainStatus.ACTIVE, provider_ref="x", is_locked=False)
    owner.set_password("Secret-pass-1")
    owner.save()
    asked = []

    # Patched ONCE, before the threads start: patching and un-patching from several threads at the same moment can leave the real method replaced for every later test.
    monkeypatch.setattr("apps.domains.adapters.onlinenic.OnlineNicAdapter.unlock_domain", lambda *a, **k: None)
    monkeypatch.setattr("apps.domains.adapters.onlinenic.OnlineNicAdapter.get_auth_code", lambda *a, **k: asked.append(1) or "EPP")

    results = burst(PEOPLE, lambda i: domain_services.get_transfer_code(owner, domain, "Secret-pass-1"))
    assert len(asked) <= domain_services.TRANSFER_CODE_PER_HOUR, f"{len(asked)} codes were handed out"


# --- Jobs that must reach the registrar or the server once ---------------------------------------------------

def _slow_counter(calls, result, pause=0.6):
    """A stand-in for the outside call: counts how often it is made and takes long enough for a twin to overlap."""
    import time

    def call(*args, **kwargs):
        calls.append(1)
        time.sleep(pause)
        return result() if callable(result) else result

    return call


def test_a_domain_registration_triggered_twice_at_once_calls_the_registrar_once(manager, client_obj, registrar, com, monkeypatch):
    from datetime import timedelta

    from django.utils import timezone

    from apps.domains import services as domain_services
    from apps.domains.adapters.manual import ManualAdapter

    calls = []
    monkeypatch.setattr(ManualAdapter, "register_domain", _slow_counter(
        calls, lambda: {"provider_ref": "ref-1", "expires_at": timezone.now() + timedelta(days=365)}))
    domain = domain_services.request_registration(manager, client_obj, "twice.com", 1)

    results = burst(2, lambda i: domain_services.complete_registration(manager, domain))

    assert len(calls) == 1, f"the registrar was called {len(calls)} times"
    assert all(not isinstance(r, Exception) or getattr(r, "code", "") == "in_progress" for r in results), results


def test_an_inbound_transfer_triggered_twice_at_once_is_sent_once(manager, client_obj, registrar, com, monkeypatch):
    from datetime import timedelta

    from django.utils import timezone

    from apps.domains import services as domain_services
    from apps.domains.adapters.manual import ManualAdapter

    calls = []
    monkeypatch.setattr(ManualAdapter, "transfer_domain", _slow_counter(
        calls, lambda: {"provider_ref": "ref-2", "expires_at": timezone.now() + timedelta(days=365)}))
    domain = domain_services.request_transfer_in(manager, client_obj, "moving.com", "EPP-1")

    results = burst(2, lambda i: domain_services.complete_transfer(manager, domain))

    assert len(calls) == 1, f"the registrar was called {len(calls)} times"
    assert all(not isinstance(r, Exception) or getattr(r, "code", "") == "in_progress" for r in results), results


def test_hosting_provisioned_twice_at_once_creates_one_account_on_the_server(manager, client_obj, shop, monkeypatch):
    from apps.hosting import services as hosting_services

    created = []
    adapter_class = type(hosting_services.get_adapter(_one_server(shop["product"], manager)))
    original = adapter_class.create_account

    def slow_create(self, **kwargs):
        import time

        created.append(kwargs["username"])
        time.sleep(0.6)
        return original(self, **kwargs)

    monkeypatch.setattr(adapter_class, "create_account", slow_create)
    account = hosting_services.request_hosting(manager, client_obj, shop["product"], "host-twice.com")

    results = burst(2, lambda i: hosting_services.complete_provisioning(manager, account))

    assert len(created) == 1, f"the server was asked {len(created)} times"
    assert all(not isinstance(r, Exception) or getattr(r, "code", "") == "in_progress" for r in results), results


def _one_server(product, manager):
    from apps.products import services as product_services
    from apps.products.models import Server

    server = Server.objects.filter(status="active").first()
    if server is None:
        server = product_services.create_server(manager, {"name": "Demo", "hostname": "demo.example.test", "kind": "manual", "status": "active"})
        product_services.map_product_server(manager, product, server) if hasattr(product_services, "map_product_server") else product.servers.add(server)
    return server


def test_a_single_use_coupon_used_by_several_people_at_once_is_honoured_once(manager, shop):
    from apps.billing import services as billing
    from apps.clients import services as client_services
    from apps.orders import services as order_services

    billing.save_coupon(manager, "ONCE", {"discount_type": "percent", "value": 10, "max_redemptions": 1})
    people = []
    for n in range(PEOPLE):
        account = client_services.create_client(manager, {"first_name": f"P{n}", "email": f"p{n}@race.test"})
        user = account.contacts.get().user
        order_services.add_hosting(user, account, shop["product"], f"coupon{n}.com", "annual")
        order_services.apply_coupon(user, order_services.get_open_cart(user, account), "ONCE")
        people.append((user, account))

    results = burst(PEOPLE, lambda i: order_services.checkout(
        people[i][0], order_services.get_open_cart(people[i][0], people[i][1]), payment_method_code=shop["bank"].code))

    placed = [r for r in results if not isinstance(r, Exception)]
    refused = [r for r in results if isinstance(r, Exception)]
    assert len(placed) == 1, f"{len(placed)} orders used a code that allows one: {refused[:2]}"
    assert all(isinstance(r, ServiceError) for r in refused), refused
    from apps.orders.models import CouponRedemption

    assert CouponRedemption.objects.count() == 1


def test_inviting_the_same_person_twice_at_once_adds_them_once(owner, client_obj):
    from apps.clients import services as client_services
    from apps.clients.models import ClientContact

    results = burst(2, lambda i: client_services.invite_contact(owner, client_obj, email="newhire@race.test"))

    assert ClientContact.objects.filter(client=client_obj, user__email="newhire@race.test").count() == 1
    assert all(not isinstance(r, Exception) or isinstance(r, ServiceError) for r in results), results  # a clear refusal, never a database error


def test_adding_the_same_dns_record_twice_at_once_keeps_one(owner, client_obj, registrar, com, manager):
    from apps.domains import services as domain_services
    from apps.domains.models import DnsRecord

    domain = domain_services.complete_registration(manager, domain_services.request_registration(manager, client_obj, "dnsrace.com", 1))
    record = {"record_type": "A", "name": "www", "content": "203.0.113.5", "ttl": 3600}

    results = burst(2, lambda i: domain_services.add_dns_record(owner, domain, record))

    assert DnsRecord.objects.filter(domain=domain, record_type="A", name="www").count() == 1
    assert all(not isinstance(r, Exception) or isinstance(r, ServiceError) for r in results), results


def test_a_double_clicked_ticket_reply_is_one_message(owner, client_obj):
    from apps.support import services as support_services
    from apps.support.models import Department, Ticket, TicketMessage

    department = Department.objects.create(name="Support", is_active=True)
    ticket = support_services.open_ticket(owner, client_obj, department=department, subject="Hello", body="First message")

    results = burst(PEOPLE, lambda i: support_services.reply(owner, ticket, "Any news on this please?"))

    assert not [r for r in results if isinstance(r, Exception)], results
    assert TicketMessage.objects.filter(ticket=ticket, body="Any news on this please?").count() == 1


def test_a_double_clicked_hosting_backup_asks_the_server_once(manager, client_obj, shop, monkeypatch):
    from apps.hosting import services as hosting_services

    server = _one_server(shop["product"], manager)
    adapter_class = type(hosting_services.get_adapter(server))
    asked = []
    monkeypatch.setattr(adapter_class, "backup_account", _slow_counter(asked, {"backup_ref": "b-1"}))
    account = hosting_services.complete_provisioning(manager, hosting_services.request_hosting(manager, client_obj, shop["product"], "bk-twice.com"))

    results = burst(2, lambda i: hosting_services.backup_account(manager, account))

    assert len(asked) == 1, f"the server was asked {len(asked)} times"
    assert all(not isinstance(r, Exception) or getattr(r, "code", "") == "in_progress" for r in results), results


def test_applying_the_whmcs_prices_twice_at_once_ends_with_the_same_prices_and_no_error(manager, shop):
    from apps.products import services as product_services
    from apps.products.models import ProductPrice

    choices = [(shop["product"].pk, {"annual": {"cycle": "annual", "price": "55.00", "custom_months": 0, "setup": "0"}})]

    results = burst(2, lambda i: product_services.apply_whmcs_prices(manager, choices))

    assert not [r for r in results if isinstance(r, Exception)], results
    assert ProductPrice.objects.filter(product=shop["product"], billing_cycle="annual").count() == 1
