"""OnlineNIC adapter (signing, answers, errors) and the transfer-code page. No real network: the HTTP call is replaced."""
import hashlib
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.urls import reverse

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.domains import services
from apps.domains.adapters import onlinenic
from apps.domains.adapters.base import RegistrarError
from apps.domains.adapters.onlinenic import OnlineNicAdapter
from apps.domains.models import Domain, DomainStatus, RegistrarProvider

pytestmark = pytest.mark.django_db
CREDS = {"user": "100", "password": "pw", "apikey": "KEY"}


def fake_post(monkeypatch, answers, seen=None):
    def post(url, data=None, timeout=None):
        if seen is not None:
            seen.append((url, data))
        command = url.rsplit("=", 1)[1]
        return SimpleNamespace(json=lambda: answers[command])

    monkeypatch.setattr(onlinenic.requests, "post", post)


def test_a_call_is_signed_the_way_onlinenic_asks_and_the_code_is_read_from_the_answer(monkeypatch):
    seen = []
    fake_post(monkeypatch, {"getAuthCode": {"code": 1000, "message": "ok", "data": {"Domain": "a.com", "Transfercode": "X9y!z"}}}, seen)
    code = OnlineNicAdapter(CREDS, sandbox=False).get_auth_code("a.com")
    url, body = seen[0]
    assert code == "X9y!z" and url.startswith("https://api.onlinenic.com/api4/domain/index.php?command=getAuthCode")
    md5 = lambda t: hashlib.md5(t.encode()).hexdigest()
    assert body["token"] == md5("100" + md5("pw") + body["timestamp"] + "getAuthCode") and body["apikey"] == "KEY" and body["domain"] == "a.com"


def test_the_test_service_is_used_in_sandbox_and_a_refusal_becomes_a_readable_error(monkeypatch):
    seen = []
    fake_post(monkeypatch, {"getAuthCode": {"code": 1021, "message": "Your IP does not exist in IP whitelist."}}, seen)
    with pytest.raises(RegistrarError, match="IP whitelist"):
        OnlineNicAdapter(CREDS, sandbox=True).get_auth_code("a.com")
    assert "ote.onlinenic.com:5999" in seen[0][0]
    with pytest.raises(RegistrarError, match="not all set"):
        OnlineNicAdapter({}, sandbox=False).get_auth_code("a.com")


def test_lock_unlock_send_the_transfer_lock_flag(monkeypatch):
    seen = []
    fake_post(monkeypatch, {"updateDomainStatus": {"code": 1000, "message": "ok"}}, seen)
    adapter = OnlineNicAdapter(CREDS, sandbox=False)
    adapter.unlock_domain("a.com", "")
    adapter.lock_domain("a.com", "")
    assert [b["ctp"] for _, b in seen] == ["N", "Y"]


@pytest.fixture
def setup(client, staff, make_user, monkeypatch):
    cache.clear()
    account = client_services.create_client(staff(Role.MANAGER), {"first_name": "Ada", "email": "ada@acme.test"})
    owner = account.contacts.get().user
    owner.set_password("Secret-pass-1")
    owner.save()
    provider = RegistrarProvider(name="OnlineNIC", kind="onlinenic", sandbox=False)
    provider.set_credentials(CREDS)
    provider.save()
    domain = Domain.objects.create(client=account, name="mine.com", status=DomainStatus.ACTIVE, provider_ref="ref", is_locked=False)
    fake_post(monkeypatch, {"updateDomainStatus": {"code": 1000, "message": "ok"},
                            "getAuthCode": {"code": 1000, "message": "ok", "data": {"Transfercode": "EPP-123"}}})
    client.force_login(owner)
    return client, domain, owner


def test_the_owner_sees_the_code_once_after_typing_the_password_and_it_is_not_stored(setup):
    client, domain, owner = setup
    url = reverse("domains_customer:transfer_code", args=[domain.pk])
    wrong = client.post(url, {"password": "nope"})
    assert wrong.status_code == 302 and not AuditEvent.objects.filter(action="domain.transfer_code_shown").exists()
    page = client.post(url, {"password": "Secret-pass-1"})
    assert page.status_code == 200 and "EPP-123" in page.content.decode() and "no-store" in page["Cache-Control"]
    assert AuditEvent.objects.filter(action="domain.transfer_code_shown").count() == 1
    assert "EPP-123" not in str(AuditEvent.objects.filter(action="domain.transfer_code_shown").first().metadata)
    assert "EPP-123" not in client.get(reverse("domains_customer:transfer_tab", args=[domain.pk])).content.decode()


def test_a_locked_domain_and_too_many_requests_are_refused(setup):
    client, domain, owner = setup
    url = reverse("domains_customer:transfer_code", args=[domain.pk])
    domain.is_locked = True
    domain.save()
    assert "EPP-123" not in client.post(url, {"password": "Secret-pass-1"}, follow=True).content.decode()
    domain.is_locked = False
    domain.save()
    for _ in range(services.TRANSFER_CODE_PER_HOUR):
        assert "EPP-123" in client.post(url, {"password": "Secret-pass-1"}).content.decode()
    assert "EPP-123" not in client.post(url, {"password": "Secret-pass-1"}, follow=True).content.decode()


def test_someone_else_cannot_ask_for_the_code(setup, make_user):
    client, domain, owner = setup
    client.force_login(make_user("other@example.com", password="Other-pass-1"))
    assert client.post(reverse("domains_customer:transfer_code", args=[domain.pk]), {"password": "Other-pass-1"}).status_code in (403, 404)


# --- registering, transferring in, renewing ------------------------------------------------------------------------------

OWNER = {"name": "Ada Khan", "organization": "Acme", "street": "1 Mall Road", "city": "Lahore", "state": "Punjab", "postcode": "54000",
         "country": "PK", "phone": "0300 1234567", "email": "ada@acme.test"}
GOOD = {"code": 1000, "message": "ok"}


def test_phone_numbers_are_written_the_way_the_registry_wants():
    from apps.domains.adapters.onlinenic import phone_for_registry

    assert phone_for_registry("+92 300 1234567", "PK") == "+92.3001234567"
    assert phone_for_registry("0300 1234567", "PK") == "+92.3001234567"
    assert phone_for_registry("+971501234567", "AE") == "+971.501234567"
    with pytest.raises(RegistrarError):
        phone_for_registry("5551234", "ZZ")


def test_registering_makes_the_owner_a_contact_first_and_sends_the_nameservers(monkeypatch):
    seen = []
    fake_post(monkeypatch, {"createContact": {**GOOD, "data": {"contactid": "C77"}},
                            "registerDomain": {**GOOD, "data": {"expdate": "2027-10-19"}}}, seen)
    adapter = OnlineNicAdapter({**CREDS, "nameservers": ["ns1.x.pk", "ns2.x.pk"]}, sandbox=False)
    result = adapter.register_domain("new.com", 2, [], OWNER)
    commands = [u.rsplit("=", 1)[1] for u, _ in seen]
    assert commands == ["createContact", "registerDomain"]
    contact, register = seen[0][1], seen[1][1]
    assert contact["voice"] == "+92.3001234567" and contact["ext"] == "com" and contact["country"] == "PK"
    assert register["registrant"] == register["admin"] == register["tech"] == register["billing"] == "C77"
    assert register["period"] == "2" and register["dns1"] == "ns1.x.pk" and register["dns2"] == "ns2.x.pk"
    assert result["provider_ref"] == "new.com" and result["expires_at"].year == 2027


def test_registering_says_what_is_missing_and_makes_no_call(monkeypatch):
    seen = []
    fake_post(monkeypatch, {}, seen)
    adapter = OnlineNicAdapter({**CREDS, "nameservers": ["a", "b"]}, sandbox=False)
    with pytest.raises(RegistrarError, match="address, city"):
        adapter.register_domain("new.com", 1, [], {**OWNER, "street": "", "city": ""})
    with pytest.raises(RegistrarError, match="owner's details"):
        adapter.register_domain("new.com", 1, [], None)
    assert seen == []
    fake_post(monkeypatch, {"createContact": {**GOOD, "data": {"contactid": "C1"}}})
    with pytest.raises(RegistrarError, match="No nameservers"):
        OnlineNicAdapter(CREDS, sandbox=False).register_domain("new.com", 1, [], OWNER)


def test_a_premium_name_is_reported_unavailable_because_it_costs_extra(monkeypatch):
    fake_post(monkeypatch, {"checkDomain": {**GOOD, "data": {"avail": "1", "Premium": "true"}}})
    assert OnlineNicAdapter(CREDS, sandbox=False).check_availability("rich.com") is False
    fake_post(monkeypatch, {"checkDomain": {**GOOD, "data": {"avail": "1", "Premium": "false"}}})
    assert OnlineNicAdapter(CREDS, sandbox=False).check_availability("plain.com") is True


def test_an_inbound_transfer_is_sent_once_then_waits_until_the_registrar_lists_the_domain(monkeypatch, setup):
    from apps.domains import services

    client, domain, owner = setup
    seen = []
    fake_post(monkeypatch, {"createContact": {**GOOD, "data": {"contactid": "C9"}}, "transferDomain": GOOD,
                            "infoDomain": {"code": 1005, "message": "domain not found"},
                            "queryTransferStatus": {**GOOD, "data": {"status": "pending at the old registrar"}}}, seen)
    account = domain.client
    for field, value in (("address_line1", "1 Mall Road"), ("city", "Lahore"), ("state", "Punjab"), ("postcode", "54000"), ("country", "PK"), ("phone", "+923001234567")):
        setattr(account, field, value)
    account.save()
    provider = services.transfer_code_provider()
    provider.is_active = True
    provider.save()
    incoming = Domain.objects.create(client=account, name="moving.com", status=DomainStatus.PENDING_TRANSFER_IN, years=1)
    incoming.set_auth_code("EPP-OLD")
    incoming.save()
    from apps.accounts.roles import Role
    from apps.accounts.models import User

    boss = User.objects.create_user(email="boss@example.com", password="x", role=Role.SUPER_ADMIN)
    services.complete_transfer(boss, incoming)
    incoming.refresh_from_db()
    assert incoming.status == DomainStatus.PENDING_TRANSFER_IN and incoming.provider_ref == "moving.com"
    sent = [u.rsplit("=", 1)[1] for u, _ in seen]
    assert sent.count("transferDomain") == 1
    services.complete_transfer(boss, incoming)  # a retry must not send (and pay for) it again
    assert [u.rsplit("=", 1)[1] for u, _ in seen].count("transferDomain") == 1
    incoming.refresh_from_db()
    assert "pending at the old registrar" in incoming.last_error
    fake_post(monkeypatch, {"infoDomain": {**GOOD, "data": {"status": "ok", "expdate": "2027-11-01"}}})
    assert services.poll_pending_transfers() == 1
    incoming.refresh_from_db()
    assert incoming.status == DomainStatus.ACTIVE and incoming.expires_at.year == 2027 and not incoming.get_auth_code()
