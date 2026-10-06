"""Email (H4): the demo provider's rules, and the customer screens end to end."""
import pytest
from django.core.management import call_command

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.core.exceptions import ServiceError
from apps.hosting import control
from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.capabilities import SpamSettings
from apps.hosting.adapters.demo_email import MAX_FORWARDERS, MAX_MAILBOXES, DemoEmail
from apps.hosting.models import HostingAccount
from apps.products import services as product_services
from apps.products.models import ProductType

pytestmark = pytest.mark.django_db

GOOD = "Sup3rSecretPass"


# --- The provider's rules ---------------------------------------------------------------------------------------

@pytest.fixture
def mail(tmp_path):
    return DemoEmail(tmp_path / "state", "shop.example.com")


def test_only_the_accounts_own_domain_and_plain_names_are_accepted(mail):
    mail.create_mailbox("info", GOOD, None)
    mail.create_mailbox("Sales@shop.example.com", GOOD, 512)  # typed in capitals: stored lower case
    assert [m.address for m in mail.mailboxes()] == ["info@shop.example.com", "sales@shop.example.com"]
    for bad in ("someone@other.com", "", "a b", "-x", "x-", "a..b", "x" * 60, "a@b@shop.example.com", "../x"):
        with pytest.raises(HostingError):
            mail.create_mailbox(bad, GOOD, None)
    with pytest.raises(HostingError, match="already exists"):
        mail.create_mailbox("info", GOOD, None)


def test_the_plan_limits_quota_range_and_password_rules_are_enforced(mail):
    for i in range(MAX_MAILBOXES):
        mail.create_mailbox(f"user{i}", GOOD, None)
    with pytest.raises(HostingError, match="allows"):
        mail.create_mailbox("onetoomany", GOOD, None)
    fresh = DemoEmail(mail.file.parent / "other", "shop.example.com")
    for password in ("short1A", "nodigitsatallhere", "has space 1234Ab"):
        with pytest.raises(HostingError):
            fresh.create_mailbox("ok", password, None)
    for quota in (0, 5, 99999, "abc"):
        with pytest.raises(HostingError):
            fresh.create_mailbox("ok", GOOD, quota)
    assert fresh.mailboxes() == []


def test_passwords_are_only_a_hash_and_can_be_changed(mail):
    mail.create_mailbox("info", GOOD, None)
    assert mail.password_matches("info@shop.example.com", GOOD)
    assert GOOD not in mail.file.read_text(encoding="utf-8")
    mail.set_password("info", "An0therStrongOne")
    assert mail.password_matches("info@shop.example.com", "An0therStrongOne")
    with pytest.raises(HostingError):
        mail.set_password("nobody", GOOD)


def test_the_quota_can_be_changed_within_the_allowed_range_and_not_below_what_is_used(mail, monkeypatch):
    mail.create_mailbox("info", GOOD, 1024)
    mail.set_quota("info", 2048)
    assert mail.mailboxes()[0].quota_mb == 2048
    for bad in (5, 99999, "abc"):
        with pytest.raises(HostingError):
            mail.set_quota("info", bad)
    monkeypatch.setattr(DemoEmail, "_used_mb", lambda self, data, address: 500.0)  # the mailbox holds 500 MB
    with pytest.raises(HostingError, match="less than"):
        mail.set_quota("info", 256)
    mail.set_quota("info", 512)


def test_forwarders_validate_destinations_duplicates_loops_and_limits(mail):
    mail.create_forwarder("sales", "me@gmail.com")
    with pytest.raises(HostingError, match="already exists"):
        mail.create_forwarder("sales", "me@gmail.com")
    for bad in ("not an email", "", "a@b"):
        with pytest.raises(HostingError):
            mail.create_forwarder("sales", bad)
    with pytest.raises(HostingError, match="own address"):
        mail.create_forwarder("sales", "sales@shop.example.com")
    mail.create_forwarder("a", "b@shop.example.com")
    with pytest.raises(HostingError, match="circle"):
        mail.create_forwarder("b", "a@shop.example.com")
    with pytest.raises(HostingError, match="Only addresses at"):
        mail.create_forwarder("a@other.com", "y@gmail.com")  # a forwarder can only start at your own domain
    mail.delete_forwarder("sales@shop.example.com", "me@gmail.com")
    with pytest.raises(HostingError):
        mail.delete_forwarder("sales@shop.example.com", "me@gmail.com")
    for i in range(MAX_FORWARDERS):
        try:
            mail.create_forwarder(f"f{i}", f"t{i}@gmail.com")
        except HostingError:
            break
    with pytest.raises(HostingError, match="allows"):
        mail.create_forwarder("overflow", "t@gmail.com")


def test_deleting_a_mailbox_removes_its_reply_forwarders_and_mail(mail):
    mail.create_mailbox("info", GOOD, None)
    mail.set_autoresponder("info", "Away", "Back soon", True)
    mail.create_forwarder("info", "me@gmail.com")
    mail.delete_mailbox("info")
    assert mail.mailboxes() == [] and mail.autoresponders() == [] and mail.forwarders() == []
    with pytest.raises(HostingError):
        mail.messages("info")


def test_autoresponder_rules(mail):
    with pytest.raises(HostingError):
        mail.set_autoresponder("ghost", "Away", "Back soon", True)  # no such mailbox
    mail.create_mailbox("info", GOOD, None)
    for subject, body in (("", "x"), ("x", ""), ("s" * 121, "x"), ("x", "b" * 2001)):
        with pytest.raises(HostingError):
            mail.set_autoresponder("info", subject, body, True)
    mail.set_autoresponder("info", "Away", "Back soon", False)
    assert [(r.address, r.enabled) for r in mail.autoresponders()] == [("info@shop.example.com", False)]
    mail.delete_autoresponder("info")
    with pytest.raises(HostingError):
        mail.delete_autoresponder("info")


def test_routing_and_spam_settings_are_validated_and_remembered(mail):
    assert mail.routing() == "local" and mail.spam() == SpamSettings(True, "medium", False)
    mail.set_routing("remote")
    assert mail.routing() == "remote"
    with pytest.raises(HostingError):
        mail.set_routing("anywhere")
    mail.set_spam(SpamSettings(True, "high", True))
    assert mail.spam() == SpamSettings(True, "high", True)
    with pytest.raises(HostingError):
        mail.set_spam(SpamSettings(True, "extreme", False))


def test_the_sample_inbox_marks_a_message_read_when_opened(mail):
    mail.create_mailbox("info", GOOD, None)
    assert [m.unread for m in mail.messages("info")] == [True, False, False]
    message = mail.read_message("info", "m1")
    assert message.body and not message.unread and not mail.messages("info")[0].unread
    with pytest.raises(HostingError):
        mail.read_message("info", "m99")


# --- The screens -----------------------------------------------------------------------------------------------

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
def account(owner, manager):
    product_services.create_product(manager, {"name": "Starter", "type": ProductType.SHARED_HOSTING})
    call_command("demo_hosting_data")
    return HostingAccount.objects.get(domain="demo-shop.example.com")


def url(account, tail=""):
    return f"/account/hosting/{account.pk}/manage/email/{tail}"


def test_the_page_starts_empty_with_the_domain_and_counts(client, owner, account):
    client.force_login(owner)
    page = client.get(url(account)).content.decode()
    assert "Email accounts" in page and "@demo-shop.example.com" in page and "0 / 10 mailboxes" in page.replace("\n", " ")
    assert "You have no mailboxes yet" in page and "Spam protection" in page and "Where your mail is delivered" in page


def test_create_change_forward_reply_and_delete_end_to_end(client, owner, account):
    client.force_login(owner)
    act = url(account, "do/")
    client.post(act, {"action": "create_mailbox", "local": "info", "password": GOOD, "quota": "512"})
    page = client.get(url(account)).content.decode()
    assert "info@demo-shop.example.com" in page and "512 MB" in page and "Webmail" in page
    client.post(act, {"action": "set_quota", "address": "info@demo-shop.example.com", "quota": "2048"})
    client.post(act, {"action": "set_password", "address": "info@demo-shop.example.com", "password": "Brand-new-Pass1"})
    client.post(act, {"action": "create_forwarder", "source": "sales", "destination": "me@gmail.com"})
    page = client.get(url(account)).content.decode()
    assert "sales@demo-shop.example.com" in page and "me@gmail.com" in page and "2048 MB" in page
    client.post(act, {"action": "set_autoresponder", "address": "info@demo-shop.example.com", "subject": "Away",
                      "body": "Back on Monday", "enabled": "1"})
    assert "Auto reply on" in client.get(url(account)).content.decode()
    client.post(act, {"action": "delete_autoresponder", "address": "info@demo-shop.example.com"})
    assert "Auto reply on" not in client.get(url(account)).content.decode()
    client.post(act, {"action": "delete_forwarder", "source": "sales@demo-shop.example.com", "destination": "me@gmail.com"})
    client.post(act, {"action": "set_routing", "mode": "remote"})
    client.post(act, {"action": "set_spam", "level": "high", "enabled": "1", "delete_spam": "1"})
    page = client.get(url(account)).content.decode()
    assert 'value="remote" checked' in page and "Spam filter on" in page
    client.post(act, {"action": "delete_mailbox", "address": "info@demo-shop.example.com"})
    assert "info@demo-shop.example.com" not in client.get(url(account)).content.decode()


def test_errors_are_shown_not_raised(client, owner, account):
    client.force_login(owner)
    act = url(account, "do/")
    body = client.post(act, {"action": "create_mailbox", "local": "info", "password": "weak"}, follow=True).content.decode()
    assert "Use a password of 12 to 128 characters" in body
    body = client.post(act, {"action": "create_mailbox", "local": "Bad Name!", "password": GOOD}, follow=True).content.decode()
    assert "letters, numbers, dots" in body
    body = client.post(act, {"action": "create_forwarder", "source": "a", "destination": "nope"}, follow=True).content.decode()
    assert "valid email address" in body
    body = client.post(act, {"action": "set_routing", "mode": "x"}, follow=True).content.decode()
    assert "Choose where your mail is delivered" in body
    assert client.post(act, {"action": "nonsense"}, follow=True).status_code == 200


def test_the_limit_disables_the_form_and_says_why(client, owner, account):
    client.force_login(owner)
    for i in range(10):
        client.post(url(account, "do/"), {"action": "create_mailbox", "local": f"u{i}", "password": GOOD})
    page = client.get(url(account)).content.decode()
    assert "reached your plan's mailbox limit" in page and "10 / 10 mailboxes" in page.replace("\n", " ")


def test_passwords_never_reach_the_audit_trail_or_the_page(client, owner, account):
    client.force_login(owner)
    secret = "Hunter2-Is-A-Terrible1"
    client.post(url(account, "do/"), {"action": "create_mailbox", "local": "info", "password": secret})
    client.post(url(account, "do/"), {"action": "set_password", "address": "info@demo-shop.example.com", "password": secret + "x"})
    events = list(AuditEvent.objects.filter(action="hosting.control").values_list("metadata", flat=True))
    assert len(events) == 2 and secret not in str(events)
    assert secret not in client.get(url(account)).content.decode()


def test_webmail_shows_the_sample_inbox_marks_read_and_escapes(client, owner, account):
    client.force_login(owner)
    client.post(url(account, "do/"), {"action": "create_mailbox", "local": "info", "password": GOOD})
    address = "info@demo-shop.example.com"
    inbox = client.get(url(account, "webmail/"), {"address": address}).content.decode()
    assert "Welcome to your new mailbox" in inbox and "Choose a message to read it" in inbox and "new" in inbox
    opened = client.get(url(account, "webmail/"), {"address": address, "message": "m1"}).content.decode()
    assert "Your mailbox is ready" in opened
    assert client.get(url(account, "webmail/"), {"address": address, "message": "m99"}, follow=True).status_code == 200
    assert client.get(url(account, "webmail/"), {"address": "ghost@demo-shop.example.com"}, follow=True).status_code == 200


def test_an_external_webmail_redirects_only_to_a_secure_address(client, owner, account, monkeypatch):
    from apps.hosting.adapters.capabilities import WebmailAccess
    from apps.hosting.adapters.demo import DemoAdapter

    client.force_login(owner)
    client.post(url(account, "do/"), {"action": "create_mailbox", "local": "info", "password": GOOD})
    address = "info@demo-shop.example.com"
    monkeypatch.setattr(DemoAdapter, "webmail_access", lambda self, u, a: WebmailAccess("external", "https://mail.example/once"))
    response = client.get(url(account, "webmail/"), {"address": address})
    assert response.status_code == 302 and response["Location"] == "https://mail.example/once"
    monkeypatch.setattr(DemoAdapter, "webmail_access", lambda self, u, a: WebmailAccess("external", "http://plain.example/x"))
    refused = client.get(url(account, "webmail/"), {"address": address})
    assert refused.status_code == 302 and refused["Location"].endswith("/manage/email/")


def test_a_stranger_gets_nothing_and_changes_nothing(client, customer, account):
    client.force_login(customer)
    assert client.get(url(account)).status_code == 404
    assert client.post(url(account, "do/"), {"action": "create_mailbox", "local": "evil", "password": GOOD}).status_code == 404
    assert client.get(url(account, "webmail/"), {"address": "x@demo-shop.example.com"}).status_code == 404


def test_an_account_without_email_gets_the_not_available_page(client, owner, account):
    from apps.products.models import Server

    Server.objects.filter(kind="demo").update(kind="manual")
    client.force_login(owner)
    assert "not available for your hosting server yet" in client.get(url(account)).content.decode()
    with pytest.raises(ServiceError):
        control.perform(owner, account, "email", "list_mailboxes")
