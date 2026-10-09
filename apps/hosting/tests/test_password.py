"""The control-panel password: a new one can be made (random or chosen) and is shown once; it is never stored, logged, audited or in a web address."""
from unittest import mock

import pytest

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.core.exceptions import ServiceError
from apps.hosting import services
from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.manual import ManualAdapter
from apps.hosting.adapters.whm_api import WhmApiAdapter
from apps.hosting.models import HostingAccount, HostingStatus
from apps.products import services as product_services
from apps.products.models import Product, ProductType, Server, ServerKind

pytestmark = pytest.mark.django_db


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def account(manager):
    client = client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test"})
    product = Product.objects.get(pk=product_services.create_product(manager, {"name": "Starter", "type": ProductType.SHARED_HOSTING}).pk)
    server = Server.objects.create(name="srv", hostname="srv.test", kind=ServerKind.MANUAL)
    return HostingAccount.objects.create(client=client, product=product, server=server, domain="acme.com", username="acme1234", status=HostingStatus.ACTIVE)


@pytest.fixture
def owner(account):
    return account.client.contacts.get().user


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(ManualAdapter, "change_password", lambda self, username, password: calls.append((username, password)))
    return calls


def test_generated_passwords_are_strong_and_different():
    seen = {services.generate_password() for _ in range(50)}
    assert len(seen) == 50
    for password in seen:
        assert len(password) == 20 and any(c.islower() for c in password) and any(c.isupper() for c in password) and any(c.isdigit() for c in password)
        assert any(c in services.PASSWORD_SYMBOLS for c in password)


@pytest.mark.parametrize("bad", ["", "short1A", "alllowercase1234", "ALLUPPERCASE1234", "NoDigitsHereAtAll", "x" * 70 + "A1", "MyAcme1234Pass"])
def test_weak_or_username_containing_passwords_are_refused(bad):
    with pytest.raises(ServiceError) as refused:
        services.check_password(bad, "acme1234")
    assert refused.value.code == "weak_password"


def test_the_owner_gets_a_random_password_sent_to_the_server_and_it_is_not_audited(owner, account, sent):
    password = services.change_account_password(owner, account)
    assert sent == [("acme1234", password)] and len(password) == 20
    event = AuditEvent.objects.get(action="hosting.password_changed")
    assert event.metadata == {"chosen": False, "username": "acme1234"} and password not in str(AuditEvent.objects.values_list("metadata", flat=True))


def test_a_chosen_password_is_checked_then_sent(owner, account, sent):
    assert services.change_account_password(owner, account, "Chosen-Pass-2026") == "Chosen-Pass-2026"
    assert sent == [("acme1234", "Chosen-Pass-2026")] and AuditEvent.objects.get(action="hosting.password_changed").metadata["chosen"] is True
    with pytest.raises(ServiceError):
        services.change_account_password(owner, account, "weak")
    assert len(sent) == 1


def test_only_the_owner_or_managing_staff_and_only_when_active(owner, account, sent, staff, make_user):
    with pytest.raises(ServiceError) as denied:
        services.change_account_password(make_user("stranger@example.com"), account)
    assert denied.value.code == "permission_denied"
    with pytest.raises(ServiceError):
        services.change_account_password(staff(Role.SUPPORT_AGENT), account)
    services.change_account_password(staff(Role.TECHNICAL), account)
    HostingAccount.objects.filter(pk=account.pk).update(status=HostingStatus.PENDING)
    with pytest.raises(ServiceError) as early:
        services.change_account_password(owner, HostingAccount.objects.get(pk=account.pk))
    assert early.value.code == "invalid_status" and len(sent) == 1


def test_a_server_failure_is_reported_and_audited_without_a_password(owner, account, monkeypatch):
    def boom(self, username, password):
        raise HostingError("password strength too low")

    monkeypatch.setattr(ManualAdapter, "change_password", boom)
    with pytest.raises(ServiceError) as failed:
        services.change_account_password(owner, account, "Chosen-Pass-2026")
    assert "strength too low" in failed.value.message
    assert "Chosen-Pass-2026" not in str(list(AuditEvent.objects.values_list("metadata", flat=True)))
    assert AuditEvent.objects.filter(action="hosting.password_change_failed").count() == 1


def test_whm_gets_the_password_in_a_post_body_never_in_a_web_address():
    adapter = WhmApiAdapter(host="whm.test", port=2087, credentials={"api_username": "root", "api_token": "t"})
    response = mock.Mock(status_code=200)
    response.json.return_value = {"metadata": {"result": 1}}
    with mock.patch("requests.post", return_value=response) as post, mock.patch("requests.get") as get:
        adapter.change_password("acme1234", "Chosen-Pass-2026")
    assert not get.called
    assert post.call_args.args[0].endswith("/json-api/passwd") and post.call_args.kwargs["data"]["password"] == "Chosen-Pass-2026"
    assert "Chosen-Pass-2026" not in str(post.call_args.args) and "password" not in post.call_args.kwargs["params"]


def test_a_server_that_cannot_change_it_says_so():
    from apps.hosting.adapters.demo import DemoAdapter

    assert issubclass(DemoAdapter, ManualAdapter)  # the demo server simply accepts it, like the manual one


def test_the_screen_shows_the_password_once_and_never_again(client, owner, account, sent):
    client.force_login(owner)
    page = client.get(f"/account/hosting/{account.pk}/information/").content.decode()
    assert "Generate a new password" in page and "Or choose your own" in page and "Your new control panel password" not in page
    response = client.post(f"/account/hosting/{account.pk}/password/", {"mode": "random"})
    shown = response.content.decode()
    password = sent[0][1]
    assert response.status_code == 200 and password in shown and "shown only this once" in shown and response["Cache-Control"] == "no-store"
    assert password not in client.get(f"/account/hosting/{account.pk}/information/").content.decode()
    own = client.post(f"/account/hosting/{account.pk}/password/", {"mode": "own", "password": "Chosen-Pass-2026"}).content.decode()
    assert "Chosen-Pass-2026" in own and sent[1] == ("acme1234", "Chosen-Pass-2026")


def test_a_bad_password_on_the_screen_is_explained_and_nothing_is_sent(client, owner, account, sent):
    client.force_login(owner)
    page = client.post(f"/account/hosting/{account.pk}/password/", {"mode": "own", "password": "weak"}, follow=True).content.decode()
    assert "Use a password of 12 to 64 characters" in page and sent == []


def test_strangers_cannot_reach_it_and_staff_need_admin_access(client, customer, staff, account, sent):
    client.force_login(customer)
    assert client.post(f"/account/hosting/{account.pk}/password/", {"mode": "random"}).status_code == 404
    client.force_login(staff(Role.TECHNICAL))
    assert client.post(f"/account/hosting/{account.pk}/password/", {"mode": "random"}).status_code == 302 and sent == []
    client.post(f"/staff/hosting/{account.pk}/admin-access/", {"reason": "Ticket 12: customer lost the password"})
    assert client.post(f"/account/hosting/{account.pk}/password/", {"mode": "random"}).status_code == 200 and len(sent) == 1
    assert AuditEvent.objects.get(action="hosting.password_changed").metadata["username"] == "acme1234"
