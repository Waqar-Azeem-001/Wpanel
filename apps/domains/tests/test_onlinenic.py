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
