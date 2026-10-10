"""Native DNS and SSL (N5): PowerDNS zones and records, certificates over HTTP-01, renewal, and the customer's own DNS and SSL screens working unchanged."""
import datetime
import json

import pytest
import requests
from django.urls import reverse

from apps.agent import catalog, services as agent
from apps.agent.models import AgentJob
from apps.agent.node import render
from apps.agent.node.acme import AcmeError, FakeAcme
from apps.audit.models import AuditEvent
from apps.hosting import control, services as hosting
from apps.hosting.adapters import native_dns, native_ssl
from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.capabilities import Capability, DnsRecord, supported_capabilities
from apps.hosting.adapters.powerdns import FakePowerDns, PowerDnsClient
from apps.hosting.models import HostingStatus

from .test_native_databases import live, owner_of  # noqa: F401  (a live, active native account with an in-process agent)
from .test_native_website import make_node, order, world  # noqa: F401

pytestmark = pytest.mark.django_db
NODE_IP = "203.0.113.10"


@pytest.fixture
def dns(monkeypatch):
    fake = FakePowerDns()
    monkeypatch.setattr(native_dns, "get_dns", lambda: fake)
    return fake


@pytest.fixture
def points_here(monkeypatch):
    monkeypatch.setattr(native_ssl, "resolve", lambda host: [NODE_IP])


@pytest.fixture
def ssl_world(dns, live, points_here):  # noqa: F811
    manager, c, account, ref = live
    return manager, c, account, ref, owner_of(c)


def perform(owner, account, op, *args, cap="dns"):
    return control.perform(owner, account, cap, op, *args)


# --- DNS ---------------------------------------------------------------------------------------------------------------------------------

def test_the_zone_is_made_when_the_website_becomes_active_and_removed_when_it_is_deleted(world, dns):  # noqa: F811
    manager, client, product = world
    server, ref = make_node(manager)
    account = order(manager, client, product, server)
    assert "acme.com" not in dns.zones  # nothing yet: the website is not active
    ref.poll_once()
    zone = dns.zones["acme.com"]
    assert zone[("acme.com.", "A")][1] == [NODE_IP] and zone[("www.acme.com.", "CNAME")][1] == ["acme.com."]
    account.refresh_from_db()
    hosting.terminate_account(manager, account)
    ref.poll_once()
    assert "acme.com" not in dns.zones
    assert {"hosting.dns_zone_created", "hosting.dns_zone_deleted"} <= set(AuditEvent.objects.values_list("action", flat=True))


def test_a_dns_outage_is_recorded_but_does_not_undo_the_website(world, dns):  # noqa: F811
    manager, client, product = world
    server, ref = make_node(manager)
    account = order(manager, client, product, server)
    dns.down = True
    ref.poll_once()
    account.refresh_from_db()
    assert account.status == HostingStatus.ACTIVE and AuditEvent.objects.filter(action="hosting.dns_zone_failed").exists()


def test_the_dns_screen_lists_adds_changes_and_removes_records(ssl_world):
    manager, c, account, ref, owner = ssl_world
    shown = perform(owner, account, "list_dns_records", "acme.com")
    assert {(r.type, r.name) for r in shown} == {("A", "@"), ("CNAME", "www")}  # the zone's SOA and NS are not shown
    rid = perform(owner, account, "add_dns_record", "acme.com", DnsRecord("", "MX", "@", "mail.acme.com", 3600, 10))
    perform(owner, account, "add_dns_record", "acme.com", DnsRecord("", "TXT", "@", 'v=spf1 "quoted" \\ ok', 3600))
    records = {(r.type, r.name): r for r in perform(owner, account, "list_dns_records", "acme.com")}
    assert records[("MX", "@")].value == "mail.acme.com" and records[("MX", "@")].priority == 10
    assert records[("TXT", "@")].value == 'v=spf1 "quoted" \\ ok'  # quotes survive the round trip
    perform(owner, account, "update_dns_record", "acme.com", DnsRecord(rid, "MX", "@", "mail2.acme.com", 3600, 20))
    assert {r.value for r in perform(owner, account, "list_dns_records", "acme.com") if r.type == "MX"} == {"mail2.acme.com"}
    gone = next(r for r in perform(owner, account, "list_dns_records", "acme.com") if r.type == "MX")
    perform(owner, account, "delete_dns_record", "acme.com", gone.record_id)
    assert not [r for r in perform(owner, account, "list_dns_records", "acme.com") if r.type == "MX"]


def test_dns_rules_are_the_same_as_everywhere_else(ssl_world):
    manager, c, account, ref, owner = ssl_world
    bad = [DnsRecord("", "A", "@", "not-an-ip"), DnsRecord("", "CNAME", "@", "x.com"), DnsRecord("", "A", "bad name", "1.2.3.4"), DnsRecord("", "A", "@", "1.2.3.4", 5)]
    for record in bad:
        with pytest.raises(Exception):
            perform(owner, account, "add_dns_record", "acme.com", record)
    perform(owner, account, "add_dns_record", "acme.com", DnsRecord("", "A", "blog", "198.51.100.7"))
    with pytest.raises(Exception, match="already exists"):
        perform(owner, account, "add_dns_record", "acme.com", DnsRecord("", "A", "blog", "198.51.100.7"))
    with pytest.raises(Exception, match="CNAME"):
        perform(owner, account, "add_dns_record", "acme.com", DnsRecord("", "CNAME", "blog", "x.example.org"))


def test_a_bad_change_keeps_the_old_record(ssl_world):
    manager, c, account, ref, owner = ssl_world
    old = next(r for r in perform(owner, account, "list_dns_records", "acme.com") if r.type == "A")
    with pytest.raises(Exception):
        perform(owner, account, "update_dns_record", "acme.com", DnsRecord(old.record_id, "A", "@", "nonsense"))
    assert any(r.type == "A" and r.value == NODE_IP for r in perform(owner, account, "list_dns_records", "acme.com"))


def test_only_the_accounts_own_domain_can_be_edited_and_zone_records_cannot_be_removed(ssl_world, dns):
    manager, c, account, ref, owner = ssl_world
    dns.create_zone("other.com", [])
    for call in (lambda: perform(owner, account, "list_dns_records", "other.com"), lambda: perform(owner, account, "add_dns_record", "other.com", DnsRecord("", "A", "@", "1.2.3.4")),
                 lambda: perform(owner, account, "delete_dns_record", "other.com", "A|@|abc")):
        with pytest.raises(Exception, match="does not belong"):
            call()
    with pytest.raises(Exception, match="managed by the hosting service"):
        perform(owner, account, "delete_dns_record", "acme.com", "NS|@|abc")
    with pytest.raises(Exception, match="no longer exists"):
        perform(owner, account, "delete_dns_record", "acme.com", "garbage")


def test_dns_is_offered_only_when_nameservers_are_configured(live, monkeypatch, settings):  # noqa: F811
    manager, c, account, ref = live
    adapter = control.get_adapter(account.server)
    settings.POWERDNS_URL = ""
    assert Capability.DNS not in supported_capabilities(adapter) and Capability.SSL in supported_capabilities(adapter)
    settings.POWERDNS_URL, settings.POWERDNS_API_KEY, settings.POWERDNS_NAMESERVERS = "https://dns.example.net:8081", "k", "ns1.example.net,ns2.example.net"
    assert Capability.DNS in supported_capabilities(control.get_adapter(account.server))
    assert isinstance(native_dns.get_dns(), PowerDnsClient) and native_dns.get_dns().nameservers == ["ns1.example.net", "ns2.example.net"]


def test_an_unreachable_dns_server_gives_a_plain_message(ssl_world, dns):
    manager, c, account, ref, owner = ssl_world
    dns.down = True
    with pytest.raises(Exception, match="could not be reached"):
        perform(owner, account, "list_dns_records", "acme.com")


class FakeSession:
    def __init__(self, replies):
        self.replies, self.sent = list(replies), []

    def request(self, method, url, json=None, headers=None, timeout=None):
        self.sent.append((method, url, json, headers))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class Reply:
    def __init__(self, status, body=None):
        self.status_code, self._body, self.content = status, body, b"x" if body is not None else b""

    def json(self):
        return self._body


def test_the_real_client_speaks_powerdns_s_api():
    session = FakeSession([Reply(404), Reply(201, {}), Reply(200, {"rrsets": [{"name": "acme.com.", "type": "A", "ttl": 300, "records": [{"content": "1.2.3.4", "disabled": False}]}]}),
                           Reply(204), Reply(204), Reply(422, {"error": "Conflicting"}), requests.ConnectionError("down")])
    client = PowerDnsClient("https://dns.example.net:8081/", "secret", ["ns1.example.net"], session=session)
    assert client.zone_exists("acme.com") is False
    client.create_zone("acme.com", [("acme.com.", "A", 3600, ["1.2.3.4"])])
    assert client.rrsets("acme.com") == [("acme.com.", "A", 300, ["1.2.3.4"])]
    client.set_rrset("acme.com", "www.acme.com.", "CNAME", 3600, ["acme.com."])
    client.set_rrset("acme.com", "www.acme.com.", "CNAME", 3600, [])
    method, url, body, headers = session.sent[0]
    assert (method, url, headers) == ("GET", "https://dns.example.net:8081/api/v1/servers/localhost/zones/acme.com.", {"X-API-Key": "secret"})
    assert session.sent[1][2]["nameservers"] == ["ns1.example.net."] and session.sent[1][2]["kind"] == "Native"
    assert session.sent[3][2]["rrsets"][0]["changetype"] == "REPLACE" and session.sent[4][2]["rrsets"][0]["changetype"] == "DELETE"
    with pytest.raises(HostingError, match="Conflicting"):
        client.set_rrset("acme.com", "x.acme.com.", "A", 60, ["1.1.1.1"])
    with pytest.raises(HostingError, match="could not be reached"):
        client.zone_exists("acme.com")


# --- SSL ---------------------------------------------------------------------------------------------------------------------------------

def test_a_certificate_is_issued_installed_and_shown(ssl_world):
    manager, c, account, ref, owner = ssl_world
    assert perform(owner, account, "get_ssl_status", "acme.com", cap="ssl").status == "none"
    perform(owner, account, "request_ssl", "acme.com", cap="ssl")
    status = perform(owner, account, "get_ssl_status", "acme.com", cap="ssl")
    assert status.active and status.status == "active" and status.issuer == "Fake ACME CA" and status.days_left in (89, 90) and status.auto_renew and not status.https_redirect
    system = ref.machine.system
    assert ref.machine.acme.calls == [(["acme.com", "www.acme.com"], f"/home/{account.username}/sites/acme.com/public_html", "http-01")]
    assert ["write", f"/etc/whp/tls/{account.username}/acme.com/privkey.pem", "0o600", "root"] in system.commands  # the key is for root only
    vhost = system.files[f"/etc/httpd/conf.d/whp/{account.username}-acme.com.conf"]
    assert "<VirtualHost *:443>" in vhost and "SSLCertificateFile" in vhost and "TLSv1.2" in vhost and "R=301" not in vhost
    assert [s.domain for s in perform(owner, account, "list_ssl_status", cap="ssl")] == ["acme.com"]


def test_it_refuses_to_ask_the_authority_when_the_domain_does_not_point_here(ssl_world, monkeypatch):
    manager, c, account, ref, owner = ssl_world
    monkeypatch.setattr(native_ssl, "resolve", lambda host: ["198.51.100.99"] if host == "acme.com" else [])
    with pytest.raises(Exception, match="does not point at this server yet.*203.0.113.10"):
        perform(owner, account, "request_ssl", "acme.com", cap="ssl")
    assert ref.machine.acme.calls == [] and not AgentJob.objects.filter(command="ISSUE_SSL").exists()
    monkeypatch.setattr(native_ssl, "resolve", lambda host: [NODE_IP] if host == "acme.com" else [])
    with pytest.raises(Exception, match="www.acme.com does not point"):
        perform(owner, account, "request_ssl", "acme.com", cap="ssl")


def test_a_refusal_from_the_certificate_authority_reaches_the_customer_and_changes_nothing(ssl_world):
    manager, c, account, ref, owner = ssl_world
    ref.machine.acme.fail_with = ("acme_failed", "Rate limit reached for this domain")
    with pytest.raises(Exception, match="Rate limit"):
        perform(owner, account, "request_ssl", "acme.com", cap="ssl")
    assert perform(owner, account, "get_ssl_status", "acme.com", cap="ssl").status == "none"
    assert "<VirtualHost *:443>" not in ref.machine.system.files[f"/etc/httpd/conf.d/whp/{account.username}-acme.com.conf"]


def test_the_https_redirect_and_renewal_switches(ssl_world):
    manager, c, account, ref, owner = ssl_world
    with pytest.raises(Exception, match="certificate for this domain first"):
        perform(owner, account, "set_https_redirect", "acme.com", True, cap="ssl")
    perform(owner, account, "request_ssl", "acme.com", cap="ssl")
    path = f"/etc/httpd/conf.d/whp/{account.username}-acme.com.conf"
    perform(owner, account, "set_https_redirect", "acme.com", True, cap="ssl")
    vhost = ref.machine.system.files[path]
    assert "R=301" in vhost and "acme-challenge" in vhost  # the redirect leaves the validation path open, or renewal would break
    assert perform(owner, account, "get_ssl_status", "acme.com", cap="ssl").https_redirect
    perform(owner, account, "set_ssl_auto_renew", "acme.com", False, cap="ssl")
    status = perform(owner, account, "get_ssl_status", "acme.com", cap="ssl")
    assert not status.auto_renew and status.https_redirect  # one switch does not undo the other
    perform(owner, account, "set_https_redirect", "acme.com", False, cap="ssl")
    assert "R=301" not in ref.machine.system.files[path]


def test_removing_the_certificate_removes_https(ssl_world):
    manager, c, account, ref, owner = ssl_world
    perform(owner, account, "request_ssl", "acme.com", cap="ssl")
    perform(owner, account, "remove_ssl", "acme.com", cap="ssl")
    system = ref.machine.system
    assert perform(owner, account, "get_ssl_status", "acme.com", cap="ssl").status == "none"
    assert not [p for p in system.files if "/etc/whp/tls/" in p] and "<VirtualHost *:443>" not in system.files[f"/etc/httpd/conf.d/whp/{account.username}-acme.com.conf"]
    perform(owner, account, "remove_ssl", "acme.com", cap="ssl")  # twice is fine


def test_https_survives_a_suspension_and_reactivation(ssl_world):
    manager, c, account, ref, owner = ssl_world
    perform(owner, account, "request_ssl", "acme.com", cap="ssl")
    perform(owner, account, "set_https_redirect", "acme.com", True, cap="ssl")
    hosting.suspend_account(manager, account)
    ref.poll_once()
    account.refresh_from_db()
    hosting.unsuspend_account(manager, account)
    ref.poll_once()
    vhost = ref.machine.system.files[f"/etc/httpd/conf.d/whp/{account.username}-acme.com.conf"]
    assert "<VirtualHost *:443>" in vhost and "R=301" in vhost


def test_an_old_certificate_is_shown_as_expiring_or_expired(ssl_world):
    manager, c, account, ref, owner = ssl_world
    now = datetime.datetime.now(datetime.timezone.utc)
    ref.machine.acme.days, ref.machine.acme.issued_at = 90, now - datetime.timedelta(days=80)
    perform(owner, account, "request_ssl", "acme.com", cap="ssl")
    assert perform(owner, account, "get_ssl_status", "acme.com", cap="ssl").status == "expiring"
    ref.machine.acme.issued_at = now - datetime.timedelta(days=100)
    perform(owner, account, "request_ssl", "acme.com", cap="ssl")
    status = perform(owner, account, "get_ssl_status", "acme.com", cap="ssl")
    assert status.status == "expired" and not status.active and status.days_left < 0


def test_automatic_renewal_renews_only_what_is_due_and_wanted(ssl_world):
    manager, c, account, ref, owner = ssl_world
    machine, now = ref.machine, datetime.datetime.now(datetime.timezone.utc)
    machine.acme.issued_at = now - datetime.timedelta(days=70)  # 20 days left
    perform(owner, account, "request_ssl", "acme.com", cap="ssl")
    machine.acme.issued_at = None
    reloads_before = machine.system.commands.count(["systemctl", "reload", "httpd"])
    assert machine.ssl.renew_due([account.username]) == [(account.username, "acme.com", "renewed")]
    assert machine.system.commands.count(["systemctl", "reload", "httpd"]) == reloads_before + 1
    assert perform(owner, account, "get_ssl_status", "acme.com", cap="ssl").days_left >= 89
    assert machine.ssl.renew_due([account.username]) == []  # fresh: nothing to do


def test_renewal_respects_the_switch_suspension_and_isolates_failures(ssl_world):
    manager, c, account, ref, owner = ssl_world
    machine, now = ref.machine, datetime.datetime.now(datetime.timezone.utc)
    machine.acme.issued_at = now - datetime.timedelta(days=80)
    perform(owner, account, "request_ssl", "acme.com", cap="ssl")
    perform(owner, account, "set_ssl_auto_renew", "acme.com", False, cap="ssl")
    assert machine.ssl.renew_due([account.username]) == []  # switched off
    perform(owner, account, "set_ssl_auto_renew", "acme.com", True, cap="ssl")
    machine.acme.fail_with, machine.acme.issued_at = ("acme_failed", "boom"), None
    assert machine.ssl.renew_due(["nobody123", account.username]) == [(account.username, "acme.com", "acme_failed")]  # other accounts are not blocked
    machine.acme.fail_with = None
    hosting.suspend_account(manager, account)
    ref.poll_once()
    assert machine.ssl.renew_due([account.username]) == []  # a suspended site is left alone


def test_another_accounts_domain_cannot_be_used(ssl_world):
    manager, c, account, ref, owner = ssl_world
    for op in ("request_ssl", "get_ssl_status", "remove_ssl"):
        with pytest.raises(Exception, match="does not belong"):
            perform(owner, account, op, "other.com", cap="ssl")


def test_dns_01_is_not_available_yet_and_says_so(ssl_world):
    manager, c, account, ref, owner = ssl_world
    from apps.agent.node.acme import RealAcme

    with pytest.raises(AcmeError) as caught:
        RealAcme("a@b.test").obtain(["acme.com"], "/home/x", "dns-01")
    assert caught.value.code == "not_implemented"


def test_the_customer_ssl_page_renders_through_the_native_provider(ssl_world, client):
    manager, c, account, ref, owner = ssl_world
    perform(owner, account, "request_ssl", "acme.com", cap="ssl")
    client.force_login(owner)
    html = client.get(reverse("hosting_customer:ssl", args=[account.pk])).content.decode()
    assert "acme.com" in html and "Fake ACME CA" in html


def test_the_new_commands_are_typed():
    for name in ("REMOVE_SSL", "SSL_STATUS", "SET_SSL_OPTIONS"):
        assert name in catalog.COMMANDS
    with pytest.raises(catalog.InvalidCommand):
        catalog.validate("SET_SSL_OPTIONS", {"account": "abcd1234", "domain": "a.com", "auto_renew": "maybe", "https_redirect": "yes"})
    assert render.vhost("abcd1234", "a.com", ssl={"https_redirect": False}).count("<VirtualHost") == 2
    _ = (json, agent, FakeAcme)
