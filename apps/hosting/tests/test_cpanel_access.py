"""
Open cPanel (H15, transitional): the one-time sign-in, who may start it, what is audited, and that no secret or link is kept.
Tested against a fake WHM; whether a real WHM answers ``create_user_session`` exactly like this is for the staging checklist.
"""
import pytest
from django.test import override_settings

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.core.exceptions import ServiceError
from apps.hosting import control
from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.capabilities import supported_capabilities
from apps.hosting.adapters.whm_api import WhmApiAdapter
from apps.hosting.models import HostingAccount, HostingStatus
from apps.products import services as product_services
from apps.products.models import Product, ProductType, Server, ServerKind

pytestmark = pytest.mark.django_db

LINK = "https://whm.test:2083/cpsess1234567890/login/?session=acme%3AAbCdEfSecretToken"
TOKEN = "whm-secret-api-token"


class Whm:
    def __init__(self, answer=None, error=None):
        self.calls = []
        self.answer = answer if answer is not None else {"data": {"url": LINK, "expires": 1_900_000_000, "session": "acme:AbCdEfSecretToken",
                                                                   "cp_security_token": "/cpsess1234567890", "service": "cpaneld"}}
        self.error = error

    def __call__(self, function, params=None, **kw):
        self.calls.append((function, dict(params or {})))
        if self.error:
            raise self.error
        return {"metadata": {"result": 1}, **self.answer}


@pytest.fixture
def fake(monkeypatch):
    whm = Whm()
    monkeypatch.setattr(WhmApiAdapter, "_call", whm)
    return whm


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def client_obj(manager):
    return client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test"})


@pytest.fixture
def owner(client_obj):
    return client_obj.contacts.get().user


@pytest.fixture
def account(client_obj, manager):
    product = product_services.create_product(manager, {"name": "Starter", "type": ProductType.SHARED_HOSTING})
    server = Server.objects.create(name="cPanel one", hostname="whm.test", kind=ServerKind.WHM_API)
    return HostingAccount.objects.create(client=client_obj, product=Product.objects.get(pk=product.pk), server=server, domain="acme.com",
                                         username="acme", status=HostingStatus.ACTIVE)


def page(account, tail=""):
    return f"/account/hosting/{account.pk}/manage/cpanel/{tail}"


# --- The adapter ------------------------------------------------------------------------------------------------------------

def test_the_tool_is_off_until_staff_enable_it():
    adapter = WhmApiAdapter(host="whm.test", port=2087, credentials={})
    assert "cpanel_access" not in {c.value for c in supported_capabilities(adapter)}
    with override_settings(HOSTING_CPANEL_CAPABILITIES=["cpanel_access"]):
        assert "cpanel_access" in {c.value for c in supported_capabilities(adapter)}


def test_the_session_is_asked_for_by_account_with_no_password_and_returns_the_link_and_expiry(fake):
    adapter = WhmApiAdapter(host="whm.test", port=2087, credentials={"api_username": "root", "api_token": TOKEN})
    session = adapter.open_cpanel_access("acme")
    assert fake.calls == [("create_user_session", {"user": "acme", "service": "cpaneld"})]
    assert session.url == LINK and session.expires_at.startswith("2030-")


@pytest.mark.parametrize("answer", [{"data": {}}, {"data": {"url": ""}}, {"data": {"url": "javascript:alert(1)"}}, {"data": {"url": "data:text/html,x"}},
                                    {"data": {"url": "/relative/path"}}, {"data": {"url": "https://"}}, {}])
def test_an_unusable_or_dangerous_link_is_refused(monkeypatch, answer):
    monkeypatch.setattr(WhmApiAdapter, "_call", Whm(answer=answer))
    with pytest.raises(HostingError, match="usable sign-in link"):
        WhmApiAdapter(host="whm.test", port=2087, credentials={}).open_cpanel_access("acme")


def test_a_missing_expiry_is_fine(monkeypatch):
    monkeypatch.setattr(WhmApiAdapter, "_call", Whm(answer={"data": {"url": LINK}}))
    assert WhmApiAdapter(host="whm.test", port=2087, credentials={}).open_cpanel_access("acme").expires_at is None


# --- The menu ---------------------------------------------------------------------------------------------------------------

@override_settings(HOSTING_CPANEL_CAPABILITIES=["cpanel_access"])
def test_the_menu_shows_advanced_tools_only_where_it_exists(client, owner, account, fake):
    client.force_login(owner)
    assert page(account) in client.get(f"/account/hosting/{account.pk}/").content.decode()
    with override_settings(HOSTING_CPANEL_CAPABILITIES=[]):
        assert "Advanced tools" not in client.get(f"/account/hosting/{account.pk}/").content.decode()
        assert "not available for your hosting server yet" in client.get(page(account)).content.decode()


@pytest.fixture
def demo(owner, manager):
    from django.core.management import call_command

    product_services.create_product(manager, {"name": "Demo plan", "type": ProductType.SHARED_HOSTING})
    call_command("demo_hosting_data")
    return HostingAccount.objects.get(domain="demo-shop.example.com")


def test_a_demo_account_can_try_the_whole_flow_with_a_sample_page(client, owner, demo):
    client.force_login(owner)
    assert page(demo) in client.get(f"/account/hosting/{demo.pk}/").content.decode()
    response = client.post(page(demo, "open/"))
    assert response.status_code == 302 and response.url.startswith(page(demo, "sample/?session="))
    sample = client.get(response.url)
    text = sample.content.decode()
    assert sample.status_code == 200 and "sample page standing in for cPanel" in text and "File Manager" in text and sample["Cache-Control"] == "no-store"
    assert "has expired" in client.get(response.url).content.decode()  # the link works once


def test_the_sample_link_is_single_use_wrongly_guessed_or_expired_links_fail_and_burn_it(client, owner, demo, monkeypatch):
    import time

    client.force_login(owner)
    url = client.post(page(demo, "open/")).url
    assert "has expired" in client.get(page(demo, "sample/?session=guess")).content.decode()
    assert "has expired" in client.get(url).content.decode()  # a wrong guess spent the real link too
    url = client.post(page(demo, "open/")).url
    monkeypatch.setattr(time, "time", lambda: 4_000_000_000)  # long after the two minutes
    assert "has expired" in client.get(url).content.decode()
    assert "has expired" in client.get(page(demo, "sample/")).content.decode()


def test_the_sample_page_is_only_for_demo_servers_and_only_for_the_owner(client, owner, customer, demo, account):
    client.force_login(owner)
    assert client.get(page(account, "sample/")).status_code == 404  # a cPanel account has no sample page
    client.force_login(customer)
    assert client.get(page(demo, "sample/")).status_code == 404


def test_only_a_hash_of_the_demo_link_is_kept(client, owner, demo):
    from apps.hosting.adapters.demo import files_root

    client.force_login(owner)
    url = client.post(page(demo, "open/")).url
    token = url.split("session=")[1]
    assert token not in (files_root() / f"{demo.username}.state" / "cpanel_session.json").read_text()
    everything = str(list(AuditEvent.objects.values_list("metadata", flat=True)))
    assert token not in everything


# --- The page and the sign-in -------------------------------------------------------------------------------------------------

@override_settings(HOSTING_CPANEL_CAPABILITIES=["cpanel_access"])
def test_the_page_says_it_is_temporary_and_shows_no_secret(client, owner, account, fake):
    client.force_login(owner)
    text = client.get(page(account)).content.decode()
    assert "Temporary" in text and "only while WHPanel does not cover everything" in text and "Open cPanel" in text and "No sign-ins yet" in text
    assert TOKEN not in text and "password" in text.lower() and fake.calls == []  # opening the page makes no sign-in


@override_settings(HOSTING_CPANEL_CAPABILITIES=["cpanel_access"])
def test_a_click_redirects_once_with_no_caching_and_no_referrer(client, owner, account, fake):
    client.force_login(owner)
    response = client.post(page(account, "open/"))
    assert response.status_code == 302 and response.url == LINK
    assert response["Cache-Control"] == "no-store" and response["Referrer-Policy"] == "no-referrer"
    assert fake.calls == [("create_user_session", {"user": "acme", "service": "cpaneld"})]
    assert client.get(page(account, "open/")).status_code == 405  # a link cannot be followed by a GET


@override_settings(HOSTING_CPANEL_CAPABILITIES=["cpanel_access"])
def test_the_request_is_audited_but_neither_the_link_nor_the_token_is_kept_anywhere(client, owner, account, fake):
    client.force_login(owner)
    client.post(page(account, "open/"))
    event = AuditEvent.objects.get(action="hosting.control", metadata__operation="open_cpanel_access")
    assert event.actor == owner and event.target_id == str(account.pk)
    assert event.metadata == {"capability": "cpanel_access", "operation": "open_cpanel_access", "transitional": True}
    everything = str([(e.action, e.metadata, e.target_repr, e.actor_repr) for e in AuditEvent.objects.all()])
    assert "AbCdEfSecretToken" not in everything and "cpsess" not in everything and TOKEN not in everything
    recent = client.get(page(account)).content.decode()
    assert "Opened" in recent and "AbCdEf" not in recent and "cpsess" not in recent


@override_settings(HOSTING_CPANEL_CAPABILITIES=["cpanel_access"])
def test_a_provider_failure_is_shown_as_a_message_and_audited_as_failed(client, owner, account, monkeypatch):
    monkeypatch.setattr(WhmApiAdapter, "_call", Whm(error=HostingError("The server refused the login.")))
    client.force_login(owner)
    response = client.post(page(account, "open/"), follow=True)
    assert "could not complete that" in response.content.decode() and "Refused" in response.content.decode()
    assert AuditEvent.objects.filter(action="hosting.control_failed", metadata__operation="open_cpanel_access").count() == 1


@override_settings(HOSTING_CPANEL_CAPABILITIES=["cpanel_access"])
def test_too_many_sign_ins_are_held_back(client, owner, account, fake):
    from apps.hosting.views_cpanel_access import LIMIT

    client.force_login(owner)
    for _ in range(LIMIT):
        assert client.post(page(account, "open/")).status_code == 302
    held = client.post(page(account, "open/"), follow=True).content.decode()
    assert "Too many sign-in requests" in held and len(fake.calls) == LIMIT


# --- Who may start it -------------------------------------------------------------------------------------------------------

@override_settings(HOSTING_CPANEL_CAPABILITIES=["cpanel_access"])
def test_other_customers_get_nothing_and_make_no_sign_in(client, customer, account, fake):
    client.force_login(customer)
    assert client.get(page(account)).status_code == 404
    assert client.post(page(account, "open/")).status_code == 404
    assert fake.calls == []


@override_settings(HOSTING_CPANEL_CAPABILITIES=["cpanel_access"])
def test_staff_need_admin_access_and_a_managing_role_before_any_session_is_created(client, staff, account, fake):
    agent, technical = staff(Role.SUPPORT_AGENT), staff(Role.TECHNICAL)
    client.force_login(technical)
    assert client.post(page(account, "open/")).status_code == 302 and fake.calls == []  # no Admin Access: sent to the service page
    client.post(f"/staff/hosting/{account.pk}/admin-access/", {"reason": "Ticket 77: site stuck in cPanel"})
    client.force_login(agent)
    client.post(f"/staff/hosting/{account.pk}/admin-access/", {"reason": "Ticket 77: looking only"})
    denied = client.post(page(account, "open/"), follow=True).content.decode()
    assert "permission" in denied.lower() and fake.calls == []  # a view-only role cannot open cPanel
    client.force_login(technical)  # a new sign-in is a new session: Admin Access has to be started again
    client.post(f"/staff/hosting/{account.pk}/admin-access/", {"reason": "Ticket 77: site stuck in cPanel"})
    response = client.post(page(account, "open/"))
    assert response.status_code == 302 and response.url == LINK and len(fake.calls) == 1
    assert AuditEvent.objects.get(action="hosting.control", metadata__operation="open_cpanel_access").metadata["admin_access"] is True


@override_settings(HOSTING_CPANEL_CAPABILITIES=["cpanel_access"])
def test_a_pending_or_terminated_account_cannot_sign_in(client, owner, account, fake):
    HostingAccount.objects.filter(pk=account.pk).update(status=HostingStatus.PENDING)
    client.force_login(owner)
    assert "must be active" in client.post(page(account, "open/"), follow=True).content.decode()
    assert fake.calls == []
    HostingAccount.objects.filter(pk=account.pk).update(status=HostingStatus.SUSPENDED)
    assert "must be active" in client.post(page(account, "open/"), follow=True).content.decode() and fake.calls == []
    HostingAccount.objects.filter(pk=account.pk).update(status=HostingStatus.TERMINATED)
    with pytest.raises(ServiceError):
        control.perform(owner, HostingAccount.objects.get(pk=account.pk), "cpanel_access", "open_cpanel_access")
