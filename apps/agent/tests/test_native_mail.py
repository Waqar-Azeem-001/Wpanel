"""Native mail (N7): a separate mail node (Postfix, Dovecot, Rspamd configuration; Maildir mailboxes) behind the customer's unchanged Email and webmail screens."""
import json
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.agent import catalog, enrollment, placement, reputation, services as agent
from apps.agent.models import AgentJob, AgentNode
from apps.agent.node import mail as node_mail
from apps.agent.node.crypt512 import sha512_crypt
from apps.agent.reference_agent import NativeMachine
from apps.audit.models import AuditEvent
from apps.hosting import control, services as hosting
from apps.hosting.adapters import native_dns
from apps.hosting.adapters.capabilities import Capability, MailProfile, SpamSettings, supported_capabilities
from apps.hosting.adapters.powerdns import FakePowerDns

from .test_native_website import make_node, order, world  # noqa: F401

pytestmark = pytest.mark.django_db
MAIL_IP = "198.51.100.20"


@pytest.fixture
def dns(monkeypatch):
    fake = FakePowerDns()
    monkeypatch.setattr(native_dns, "get_dns", lambda: fake)
    return fake


@pytest.fixture
def mailworld(dns, world, tmp_path):  # noqa: F811
    manager, client, product = world
    product.resource_limits = {"disk_mb": 2048, "email_accounts": 3, "emails_per_hour": 5, "mailbox_quota_mb": 2048}
    product.save()
    web_server, web = make_node(manager, "web-1")
    web.machine = NativeMachine(files_dir=tmp_path / "web")
    account = order(manager, client, product, web_server)
    web.poll_once()
    account.refresh_from_db()
    mail_server, mail = make_node(manager, "mail-1", capabilities=("mail",), ips=[MAIL_IP])
    mail.machine = NativeMachine(files_dir=tmp_path / "mailnode")
    enrollment.heartbeat(AgentNode.objects.get(server=mail_server), {"services": {"mail": "up"}})
    agent.inline_agent = lambda: (web.poll_once(), mail.poll_once())
    yield manager, account, mail, client.contacts.get().user, mail_server, product
    agent.inline_agent = None


def m(owner, account, op, *args, **kw):
    return control.perform(owner, account, "email", op, *args, **kw)


def files(mail):
    return mail.machine.system.files


# --- password hashing ------------------------------------------------------------------------------------------------------------------

def test_sha512_crypt_matches_the_published_test_vectors():
    assert sha512_crypt("Hello world!", "saltstring") == "$6$saltstring$svn8UoSVapNtMuq1ukKS4tPQd8iKwSMHWjl/O817G3uBnIFNjnQJuesI68u4OTLiBFdcbYEdFCoEOfaS35inz1"
    assert sha512_crypt("Hello world!", "saltstringsaltstring", rounds=10000) == \
        "$6$rounds=10000$saltstringsaltst$OW1/O6BYHV6BcXZu8QVeXbDWra3Oeqh0sbHbbMCVNSnCM/UrjmM0Dp8vOuZeHBy/YTBmSK6H9qs/y3RnOaw5v."


# --- setting mail up ------------------------------------------------------------------------------------------------------------------

def test_the_first_use_picks_a_mail_node_and_sets_the_domain_up(mailworld):
    manager, account, mail, owner, mail_server, product = mailworld
    assert m(owner, account, "list_mailboxes") == []
    account.refresh_from_db()
    assert account.mail_server == mail_server
    system = files(mail)
    assert "acme.com OK" in system["/etc/postfix/whp/virtual_domains"]
    assert ["postmap", "/etc/postfix/whp/virtual_domains"] in mail.machine.system.commands and ["postfix", "reload"] in mail.machine.system.commands
    assert "/etc/rspamd/dkim/acme.com.whp.key" in system and "BEGIN PRIVATE KEY" in system["/etc/rspamd/dkim/acme.com.whp.key"]
    assert ["write", "/etc/rspamd/dkim/acme.com.whp.key", "0o600", "_rspamd"] in mail.machine.system.commands  # the DKIM key is readable by Rspamd only
    assert 'selector = "whp"' in system["/etc/rspamd/local.d/dkim_signing.conf"]
    limits = m(owner, account, "email_limits")
    assert (limits.max_mailboxes, limits.max_quota_mb, limits.domain) == (3, 2048, "acme.com")


def test_the_mail_dns_records_are_published_and_existing_text_is_kept(mailworld, dns):
    manager, account, mail, owner, mail_server, product = mailworld
    zone = dns.zones["acme.com"]
    zone[("acme.com.", "TXT")] = (3600, ['"google-site-verification=abc"', '"v=spf1 include:old.example -all"'])
    m(owner, account, "list_mailboxes")
    assert zone[("acme.com.", "MX")][1] == ["10 mail-1.example.com."]
    spf = zone[("acme.com.", "TXT")][1]
    assert '"google-site-verification=abc"' in spf and '"v=spf1 ip4:198.51.100.20 -all"' in spf and len(spf) == 2  # the old SPF was replaced, the other record kept
    dkim = native_dns.from_content("TXT", zone[("whp._domainkey.acme.com.", "TXT")][1][0])[0]
    assert dkim.startswith("v=DKIM1; k=rsa; p=") and len(zone[("whp._domainkey.acme.com.", "TXT")][1][0]) > 400  # longer than one 255-character string: sent in pieces
    assert dkim == json.loads(files(mail)["/var/lib/whp/mail/acme.com.json"])["dkim_txt"]
    assert native_dns.from_content("TXT", zone[("_dmarc.acme.com.", "TXT")][1][0])[0] == "v=DMARC1; p=none; rua=mailto:postmaster@acme.com"


def test_without_a_dns_zone_mail_still_works(mailworld, dns):
    manager, account, mail, owner, mail_server, product = mailworld
    dns.zones.clear()
    assert m(owner, account, "list_mailboxes") == []


def test_no_healthy_mail_node_gives_a_plain_message_and_leaves_nothing_half_done(mailworld):
    manager, account, mail, owner, mail_server, product = mailworld
    AgentNode.objects.filter(server=mail_server).update(last_seen_at=timezone.now() - timedelta(hours=1))
    with pytest.raises(Exception, match="No mail server is available"):
        m(owner, account, "list_mailboxes")
    account.refresh_from_db()
    assert account.mail_server is None


def test_the_email_tool_is_offered_only_when_a_mail_node_exists(world):  # noqa: F811
    manager, client, product = world
    web_server, web = make_node(manager, "web-1")
    from apps.hosting.adapters.registry import build_adapter

    assert Capability.EMAIL not in supported_capabilities(build_adapter(web_server))
    make_node(manager, "mail-1", capabilities=("mail",), ips=[MAIL_IP])
    assert Capability.EMAIL in supported_capabilities(build_adapter(web_server))
    assert placement.has_mail_nodes()


# --- mailboxes, forwarders, auto-replies, spam, routing ---------------------------------------------------------------------------

def test_mailboxes_are_created_with_a_hashed_password_and_a_quota(mailworld):
    manager, account, mail, owner, mail_server, product = mailworld
    m(owner, account, "create_mailbox", "info@acme.com", "Kq7vRt2mXzLp", quota_mb=500)
    passwd = files(mail)["/etc/dovecot/whp/passwd"]
    assert "info@acme.com:{SHA512-CRYPT}$6$" in passwd and "Kq7vRt2mXzLp" not in passwd and "storage=500M" in passwd
    assert "info@acme.com acme.com/info/" in files(mail)["/etc/postfix/whp/virtual_mailboxes"]
    box = m(owner, account, "list_mailboxes")[0]
    assert (box.address, box.quota_mb, box.used_mb) == ("info@acme.com", 500, 0.0)
    m(owner, account, "set_mailbox_quota", "info@acme.com", 800)
    assert "storage=800M" in files(mail)["/etc/dovecot/whp/passwd"]
    before = files(mail)["/etc/dovecot/whp/passwd"]
    m(owner, account, "set_mailbox_password", "info@acme.com", "Wp9sLn4cHdQe")
    assert files(mail)["/etc/dovecot/whp/passwd"] != before and "Wp9sLn4cHdQe" not in files(mail)["/etc/dovecot/whp/passwd"]
    m(owner, account, "delete_mailbox", "info@acme.com")
    assert m(owner, account, "list_mailboxes") == [] and "info@acme.com" not in files(mail)["/etc/dovecot/whp/passwd"]


def test_mailbox_rules_come_from_the_plan_and_the_node(mailworld):
    manager, account, mail, owner, mail_server, product = mailworld
    for name in ("a", "b", "c"):
        m(owner, account, "create_mailbox", f"{name}@acme.com", "Kq7vRt2mXzLp", quota_mb=100)
    with pytest.raises(Exception, match="mailbox limit"):
        m(owner, account, "create_mailbox", "d@acme.com", "Kq7vRt2mXzLp", quota_mb=100)
    with pytest.raises(Exception, match="already exists"):
        m(owner, account, "create_mailbox", "a@acme.com", "Kq7vRt2mXzLp", quota_mb=100)
    with pytest.raises(Exception, match="at most 2048"):
        m(owner, account, "set_mailbox_quota", "a@acme.com", 5000)
    with pytest.raises(Exception, match="own domain"):
        m(owner, account, "create_mailbox", "x@other.com", "Kq7vRt2mXzLp")
    with pytest.raises(Exception, match="12 to 128"):
        m(owner, account, "set_mailbox_password", "a@acme.com", "short")


def test_forwarders_and_a_mailbox_that_is_also_forwarded(mailworld):
    manager, account, mail, owner, mail_server, product = mailworld
    m(owner, account, "create_mailbox", "info@acme.com", "Kq7vRt2mXzLp", quota_mb=100)
    m(owner, account, "create_forwarder", "info@acme.com", "boss@gmail.example.com")
    m(owner, account, "create_forwarder", "sales@acme.com", "info@acme.com")
    aliases = files(mail)["/etc/postfix/whp/virtual_aliases"]
    assert "info@acme.com info@acme.com,boss@gmail.example.com" in aliases and "sales@acme.com info@acme.com" in aliases  # info keeps its own copy
    assert {(f.source, f.destination) for f in m(owner, account, "list_forwarders")} == {("info@acme.com", "boss@gmail.example.com"), ("sales@acme.com", "info@acme.com")}
    with pytest.raises(Exception, match="itself"):
        m(owner, account, "create_forwarder", "sales@acme.com", "sales@acme.com")
    m(owner, account, "delete_forwarder", "sales@acme.com", "info@acme.com")
    m(owner, account, "delete_mailbox", "info@acme.com")
    assert m(owner, account, "list_forwarders") == []  # deleting a mailbox removes the forwards from it


def test_autoreplies_are_written_as_escaped_sieve_scripts(mailworld):
    manager, account, mail, owner, mail_server, product = mailworld
    m(owner, account, "create_mailbox", "info@acme.com", "Kq7vRt2mXzLp", quota_mb=100)
    m(owner, account, "set_autoresponder", "info@acme.com", 'Away "now"', 'Back soon\\ line2" ; discard; "', True)
    sieve = files(mail)["/var/mail/vhosts/acme.com/info/.dovecot.sieve"]
    assert 'vacation :days 1 :subject "Away \\"now\\""' in sieve and '\\" ; discard; \\"' in sieve  # quotes cannot close the string and add commands
    assert m(owner, account, "list_autoresponders")[0].enabled
    m(owner, account, "set_autoresponder", "info@acme.com", "Away", "x", False)
    assert "/var/mail/vhosts/acme.com/info/.dovecot.sieve" not in files(mail)
    m(owner, account, "delete_autoresponder", "info@acme.com")
    assert m(owner, account, "list_autoresponders") == []


def test_spam_settings_and_routing(mailworld):
    manager, account, mail, owner, mail_server, product = mailworld
    m(owner, account, "set_spam_settings", SpamSettings(enabled=True, level="high", delete_spam=False))
    assert '"add header" = 4; "reject" = 10' in files(mail)["/etc/rspamd/local.d/whp_spam.conf"]
    m(owner, account, "set_spam_settings", SpamSettings(enabled=True, level="low", delete_spam=True))
    assert '"add header" = 10; "reject" = 10' in files(mail)["/etc/rspamd/local.d/whp_spam.conf"]  # deleting means rejecting what would be marked
    m(owner, account, "set_spam_settings", SpamSettings(enabled=False, level="medium", delete_spam=False))
    assert "want_spam = yes" in files(mail)["/etc/rspamd/local.d/whp_spam.conf"]
    assert m(owner, account, "get_spam_settings") == SpamSettings(enabled=False, level="medium", delete_spam=False)
    m(owner, account, "set_mail_routing", "remote")
    assert "acme.com OK" not in files(mail)["/etc/postfix/whp/virtual_domains"] and m(owner, account, "get_mail_routing") == "remote"


def test_a_failed_reload_puts_the_old_configuration_back(mailworld):
    manager, account, mail, owner, mail_server, product = mailworld
    m(owner, account, "create_mailbox", "info@acme.com", "Kq7vRt2mXzLp", quota_mb=100)
    system = mail.machine.system
    before = files(mail)["/etc/postfix/whp/virtual_mailboxes"]
    system.fail_on = len(system.commands) + 3  # the maps are written, then a command fails
    with pytest.raises(Exception):
        m(owner, account, "create_mailbox", "new@acme.com", "Kq7vRt2mXzLp", quota_mb=100)
    system.fail_on = None
    assert files(mail)["/etc/postfix/whp/virtual_mailboxes"] == before
    assert [b.address for b in m(owner, account, "list_mailboxes")] == ["info@acme.com"]


# --- reading and writing mail ------------------------------------------------------------------------------------------------------------

RAW = "From: Bob <bob@example.org>\nTo: info@acme.com\nSubject: Hello there\nDate: Sat, 10 Oct 2026 10:00:00 +0000\nMessage-ID: <1@example.org>\n\nFirst line of the body.\n"


@pytest.fixture
def inbox(mailworld):
    manager, account, mail, owner, mail_server, product = mailworld
    m(owner, account, "create_mailbox", "info@acme.com", "Kq7vRt2mXzLp", quota_mb=100)
    ids = [mail.machine.mail.deliver("acme.com", "info", RAW.replace("Hello there", f"Hello {i}")) for i in range(3)]
    return manager, account, mail, owner, ids


def test_the_inbox_lists_reads_and_counts_unread(inbox):
    manager, account, mail, owner, ids = inbox
    assert m(owner, account, "message_folders", "info@acme.com")["inbox"] == (3, 3)
    messages = m(owner, account, "list_messages", "info@acme.com", "inbox")
    assert len(messages) == 3 and messages[0].unread and messages[0].snippet == "First line of the body." and messages[0].sender == "Bob <bob@example.org>"
    full = m(owner, account, "read_message", "info@acme.com", ids[0])
    assert full.body.strip() == "First line of the body." and not full.unread
    assert m(owner, account, "message_folders", "info@acme.com")["inbox"] == (3, 2)


def test_star_move_trash_and_delete_for_good(inbox):
    manager, account, mail, owner, ids = inbox
    m(owner, account, "set_message_starred", "info@acme.com", ids[0], True)
    assert [x.message_id for x in m(owner, account, "list_messages", "info@acme.com", "starred")] == [ids[0]]
    m(owner, account, "move_message", "info@acme.com", ids[1], "archive")
    assert m(owner, account, "message_folders", "info@acme.com")["archive"][0] == 1
    m(owner, account, "delete_message", "info@acme.com", ids[1])  # from the archive: to the trash
    assert m(owner, account, "message_folders", "info@acme.com")["trash"][0] == 1
    m(owner, account, "delete_message", "info@acme.com", ids[1])  # already in the trash: gone
    assert m(owner, account, "message_folders", "info@acme.com")["trash"][0] == 0
    m(owner, account, "set_message_starred", "info@acme.com", ids[0], False)
    assert m(owner, account, "list_messages", "info@acme.com", "starred") == []
    with pytest.raises(Exception, match="no longer exists"):
        m(owner, account, "read_message", "info@acme.com", ids[1])


def test_sending_uses_an_argument_list_stores_a_copy_and_clears_the_draft(inbox):
    manager, account, mail, owner, ids = inbox
    m(owner, account, "set_mail_profile", "info@acme.com", MailProfile(display_name='Acme "Info"', reply_to="help@acme.com"))
    draft = m(owner, account, "save_draft", "info@acme.com", "bob@example.org", "Draft", "typing...")
    assert m(owner, account, "message_folders", "info@acme.com")["drafts"][0] == 1
    m(owner, account, "send_message", "info@acme.com", "bob@example.org, ann@example.net", "Hi there", "Plain body", "<p>Formatted</p>", draft)
    command = next(c for c in mail.machine.system.commands if c[:1] == ["sendmail"])
    assert command == ["sendmail", "-i", "-f", "info@acme.com", "--", "bob@example.org", "ann@example.net"]
    assert m(owner, account, "message_folders", "info@acme.com")["sent"][0] == 1 and m(owner, account, "message_folders", "info@acme.com")["drafts"][0] == 0
    sent = m(owner, account, "list_messages", "info@acme.com", "sent")[0]
    assert sent.subject == "Hi there" and sent.sender.startswith("Acme") and "<info@acme.com>" in sent.sender
    full = m(owner, account, "read_message", "info@acme.com", sent.message_id)
    assert "Plain body" in full.body and "Formatted" in full.html
    assert m(owner, account, "get_mail_profile", "info@acme.com").reply_to == "help@acme.com"


def test_header_injection_and_bad_recipients_are_refused(inbox):
    manager, account, mail, owner, ids = inbox
    m(owner, account, "send_message", "info@acme.com", "bob@example.org", "Subject\nBcc: evil@example.org", "Body")
    sent = m(owner, account, "list_messages", "info@acme.com", "sent")[0]
    raw = next(mail.machine.mail._maildir("acme.com", "info").joinpath(".Sent/cur").iterdir()).read_text()
    assert "\nBcc:" not in raw and sent.subject.startswith("Subject Bcc")
    for to in ("", "not-an-address", "bob@example.org; rm -rf /", "a@b.c, ,x y@z.com"):
        with pytest.raises(Exception):
            m(owner, account, "send_message", "info@acme.com", to, "Hi", "Body")


def test_the_hourly_sending_limit_and_a_blocked_mailbox(inbox):
    manager, account, mail, owner, ids = inbox
    for i in range(5):
        m(owner, account, "send_message", "info@acme.com", f"u{i}@example.org", "Hi", "Body")
    with pytest.raises(Exception, match="5 messages an hour"):
        m(owner, account, "send_message", "info@acme.com", "late@example.org", "Hi", "Body")
    mail.machine.clock = lambda: timezone.now().timestamp() + 3700  # an hour later it works again
    m(owner, account, "send_message", "info@acme.com", "late@example.org", "Hi", "Body")


def test_log_scanning_blocks_a_mailbox_that_sends_too_much(inbox):
    manager, account, mail, owner, ids = inbox
    lines = ["Oct 10 10:00:01 mx postfix/qmgr[123]: 4A1B2C3D4E: from=<info@acme.com>, size=2048, nrcpt=4 (queue active)"] * 2 + \
            ["Oct 10 10:00:02 mx postfix/qmgr[123]: 5B1B2C3D4E: from=<other@elsewhere.org>, size=100, nrcpt=900 (queue active)", "garbage line"]
    assert mail.machine.mail.scan_logs(lines) == ["info@acme.com"] and mail.machine.mail.scan_logs(lines) == []  # 8 > 5 per hour; not blocked twice
    assert "info@acme.com REJECT" in files(mail)["/etc/postfix/whp/blocked_senders"]
    assert m(owner, account, "list_mailboxes")[0].address == "info@acme.com"
    with pytest.raises(Exception, match="blocked"):
        m(owner, account, "send_message", "info@acme.com", "bob@example.org", "Hi", "Body")


# --- isolation, lifecycle, privacy ---------------------------------------------------------------------------------------------------

def test_another_account_cannot_touch_this_accounts_mail(inbox, make_user):
    manager, account, mail, owner, ids = inbox
    from apps.core.exceptions import ServiceError

    with pytest.raises(ServiceError):
        m(make_user("stranger@example.com"), account, "list_mailboxes")
    node_side = mail.machine.mail
    for command, params in (("MAIL_LIST", {"account": "otheracct", "domain": "acme.com"}), ("MAIL_BOX_DELETE", {"account": "otheracct", "address": "info@acme.com"}),
                            ("MAIL_READ", {"account": "otheracct", "address": "info@acme.com", "message_id": ids[0]}),
                            ("MAIL_DOMAIN_ADD", {"account": "otheracct", "domain": "acme.com"})):
        with pytest.raises(Exception):
            node_side.run(command, params)
    assert len(m(owner, account, "list_mailboxes")) == 1


def test_changing_the_plan_updates_the_mail_limits_and_terminating_removes_the_mail(mailworld):
    manager, account, mail, owner, mail_server, product = mailworld
    from apps.products import services as product_services
    from apps.products.models import Product, ProductType

    m(owner, account, "create_mailbox", "info@acme.com", "Kq7vRt2mXzLp", quota_mb=100)
    bigger = Product.objects.get(pk=product_services.create_product(manager, {"name": "Big", "type": ProductType.SHARED_HOSTING}).pk)
    bigger.whm_package_name, bigger.resource_limits = "native_big", {"disk_mb": 4096, "email_accounts": 20}
    bigger.save()
    hosting.change_package(manager, account, bigger)
    agent.inline_agent()
    assert json.loads(files(mail)["/var/lib/whp/mail/acme.com.json"])["limits"]["max_mailboxes"] == 20
    account.refresh_from_db()
    hosting.terminate_account(manager, account)
    agent.inline_agent()
    assert "/var/lib/whp/mail/acme.com.json" not in files(mail) and "acme.com" not in files(mail)["/etc/postfix/whp/virtual_domains"]
    assert AuditEvent.objects.filter(action="hosting.mail_removal_queued").exists()


def test_mail_content_and_passwords_are_not_kept_by_whpanel(inbox):
    manager, account, mail, owner, ids = inbox
    mail.machine.mail.deliver("acme.com", "info", RAW.replace("First line", "SECRET-BODY-TEXT"))
    for message in m(owner, account, "list_messages", "info@acme.com", "inbox"):
        m(owner, account, "read_message", "info@acme.com", message.message_id)
    m(owner, account, "send_message", "info@acme.com", "bob@example.org", "A private subject", "A private body")
    blob = json.dumps([[j.params, j.result_data] for j in AgentJob.objects.all()]) + json.dumps([e.metadata for e in AuditEvent.objects.all()])
    assert "SECRET-BODY-TEXT" not in blob and "Kq7vRt2mXzLp" not in blob and "Hello 0" not in blob
    assert AgentJob.objects.filter(command="CREATE_MAILBOX").get().params["password"] == "***"


def test_the_customer_email_page_works_through_the_native_provider(inbox, client):
    manager, account, mail, owner, ids = inbox
    client.force_login(owner)
    assert "info@acme.com" in client.get(reverse("hosting_customer:email", args=[account.pk])).content.decode()


def test_mail_commands_are_typed():
    ok = {"account": "abcd1234", "address": "info@acme.com", "to": "b@c.de", "subject": "s", "body": "b", "html": "", "draft_id": ""}
    assert catalog.validate("MAIL_SEND", ok)["to"] == "b@c.de"
    for bad in ({**ok, "address": "Info@acme.com"}, {**ok, "address": "info@acme"}, {**ok, "body": "x" * 100001}, {**ok, "subject": "a\x00b"}, {**ok, "extra": 1}):
        with pytest.raises(catalog.InvalidCommand):
            catalog.validate("MAIL_SEND", bad)
    with pytest.raises(catalog.InvalidCommand):
        catalog.validate("MAIL_READ", {"account": "abcd1234", "address": "info@acme.com", "message_id": "../../etc/passwd"})
    _ = node_mail


# --- reputation ------------------------------------------------------------------------------------------------------------------------------

def test_blacklist_checks_tell_listed_unchecked_and_clean_apart():
    answers = {"20.100.51.198.zen.spamhaus.org": ["127.0.0.2"], "20.100.51.198.b.barracudacentral.org": ["127.255.255.254"], "20.100.51.198.bl.spamcop.net": []}

    def lookup(name):
        if name.endswith("sorbs.net"):
            raise OSError("timeout")
        return answers.get(name, [])

    listed, failed = reputation.check_ip(MAIL_IP, lookup)
    assert listed == {"zen.spamhaus.org"} and failed == {"b.barracudacentral.org", "dnsbl.sorbs.net"}  # a refused or failed question is never "clean"
    assert reputation.check_ip("2001:db8::1", lookup) == (set(), set())


def test_reputation_is_saved_shown_and_reported_once(mailworld, client, staff, capsys):
    manager, account, mail, owner, mail_server, product = mailworld
    node = AgentNode.objects.get(server=mail_server)
    enrollment.heartbeat(node, {"services": {"mail": "up"}, "mail_queue": 450})
    node.refresh_from_db()
    clean = reputation.check_node(node, lambda name: [])
    assert clean["queue_high"] and not clean["ok"] and clean["listed"] == {}
    reputation.check_node(node, lambda name: [])
    assert AuditEvent.objects.filter(action="agent.mail_reputation_bad").count() == 1  # told once, when it turned bad
    enrollment.heartbeat(node, {"services": {"mail": "up"}, "mail_queue": 3})
    node.refresh_from_db()
    assert reputation.check_node(node, lambda name: [])["ok"]
    listed = reputation.check_node(node, lambda name: ["127.0.0.2"] if "spamhaus" in name else [])
    assert listed["listed"] == {MAIL_IP: ["zen.spamhaus.org"]} and not listed["ok"]
    client.force_login(manager)
    html = client.get(reverse("agent_staff:nodes")).content.decode()
    assert "Mail reputation" in html and "zen.spamhaus.org" in html
    assert reputation.check_all(lambda name: [])["mail-1"]["ok"]
    call_command("check_mail_reputation")
    assert "mail-1:" in capsys.readouterr().out
