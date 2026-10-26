"""The e-mail deliverability checks (SPF, DKIM, DMARC) against made-up DNS: every verdict, every piece of advice, and a DNS that cannot be reached."""
import socket
from types import SimpleNamespace

import pytest

from apps.notifications import deliverability as dv


def dns(monkeypatch, records):
    monkeypatch.setattr(dv, "_query", lambda name, rtype: records.get((name, rtype), []))


# --- SPF verdicts ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("record,ip,expected", [
    ("v=spf1 ip4:203.0.113.0/24 -all", "203.0.113.9", "pass"),
    ("v=spf1 ip4:203.0.113.0/24 -all", "198.51.100.1", "fail"),
    ("v=spf1 ip4:203.0.113.0/24 ~all", "198.51.100.1", "softfail"),
    ("v=spf1 ip4:203.0.113.0/24 ?all", "198.51.100.1", "neutral"),
    ("v=spf1 ip4:203.0.113.0/24", "198.51.100.1", "neutral"),  # no "all": nothing decided
    ("v=spf1 ip6:2001:db8::/32 -all", "2001:db8::5", "pass"),
    ("v=spf1 ip4:not-a-network -all", "203.0.113.9", "fail"),  # a broken term never matches
    ("v=spf1 +all", "198.51.100.1", "pass"),
])
def test_the_verdict_follows_the_record(monkeypatch, record, ip, expected):
    dns(monkeypatch, {("shop.test", "TXT"): [record]})
    assert dv.spf_result("shop.test", ip) == expected


def test_a_domain_without_an_spf_record_has_none(monkeypatch):
    dns(monkeypatch, {("shop.test", "TXT"): ["google-site-verification=abc"]})
    assert dv.spf_result("shop.test", "203.0.113.9") == "none"
    dns(monkeypatch, {})
    assert dv.spf_result("shop.test", "203.0.113.9") == "none"


def test_a_and_mx_terms_look_up_the_servers_addresses(monkeypatch):
    dns(monkeypatch, {("shop.test", "TXT"): ["v=spf1 a mx:mail.shop.test -all"], ("shop.test", "A"): ["203.0.113.1"],
                      ("mail.shop.test", "MX"): [], ("shop.test", "MX"): [], ("mail.shop.test", "A"): ["203.0.113.2"]})
    assert dv.spf_result("shop.test", "203.0.113.1") == "pass"  # the domain's own address
    dns(monkeypatch, {("shop.test", "TXT"): ["v=spf1 mx -all"], ("shop.test", "MX"): ["mail.shop.test"], ("mail.shop.test", "A"): ["203.0.113.2"]})
    assert dv.spf_result("shop.test", "203.0.113.2") == "pass" and dv.spf_result("shop.test", "203.0.113.99") == "fail"


def test_include_and_redirect_are_followed_but_not_forever(monkeypatch):
    dns(monkeypatch, {("shop.test", "TXT"): ["v=spf1 include:relay.test -all"], ("relay.test", "TXT"): ["v=spf1 ip4:198.51.100.0/24 -all"]})
    assert dv.spf_result("shop.test", "198.51.100.7") == "pass" and dv.spf_result("shop.test", "203.0.113.9") == "fail"
    dns(monkeypatch, {("shop.test", "TXT"): ["v=spf1 redirect=other.test"], ("other.test", "TXT"): ["v=spf1 ip4:198.51.100.0/24 -all"]})
    assert dv.spf_result("shop.test", "198.51.100.7") == "pass"
    dns(monkeypatch, {("loop.test", "TXT"): ["v=spf1 include:loop.test -all"]})
    assert dv.spf_result("loop.test", "198.51.100.7") == "fail"  # a record that includes itself ends after a few steps


def test_an_unreachable_dns_is_an_error_not_a_verdict(monkeypatch):
    def down(name, rtype):
        raise dv.LookupFailed(name)

    monkeypatch.setattr(dv, "_query", down)
    with pytest.raises(dv.LookupFailed):
        dv.spf_result("shop.test", "203.0.113.9")
    assert dv._addresses("shop.test") == [] and dv._mx_hosts("shop.test") == []


# --- the report ---------------------------------------------------------------------------------------------------------------------------

def provider(**kw):
    base = {"host": "smtp.shop.test", "from_email": "WHpanel <no-reply@shop.test>", "envelope_from": "", "username": "no-reply@shop.test"}
    return SimpleNamespace(**{**base, **kw})


def server_is(monkeypatch, ip="203.0.113.9"):
    monkeypatch.setattr(socket, "gethostbyname", lambda host: ip)


def levels(checks):
    return [level for level, _ in checks]


def test_a_mail_server_that_does_not_resolve_is_the_only_finding(monkeypatch):
    def fails(host):
        raise OSError("no such host")

    monkeypatch.setattr(socket, "gethostbyname", fails)
    checks = dv.report(provider())
    assert levels(checks) == ["bad"] and "does not resolve" in checks[0][1]


def test_everything_in_order_reads_ok_all_the_way(monkeypatch):
    server_is(monkeypatch)
    dns(monkeypatch, {("shop.test", "TXT"): ["v=spf1 ip4:203.0.113.9 -all"], ("default._domainkey.shop.test", "TXT"): ["v=DKIM1; k=rsa; p=abc"],
                      ("_dmarc.shop.test", "TXT"): ["v=DMARC1; p=quarantine; rua=mailto:a@shop.test"]})
    checks = dv.report(provider())
    assert levels(checks) == ["ok", "ok", "ok", "ok"]
    assert "SPF: shop.test allows 203.0.113.9" in checks[1][1] and "DKIM" in checks[2][1] and "p=quarantine" in checks[3][1]


def test_a_server_the_domain_does_not_list_is_called_out_with_the_fix(monkeypatch):
    server_is(monkeypatch, "198.51.100.5")
    dns(monkeypatch, {("shop.test", "TXT"): ["v=spf1 ip4:203.0.113.9 -all"]})
    checks = dv.report(provider())
    bad = [text for level, text in checks if level == "bad"]
    assert len(bad) == 1 and "does NOT list 198.51.100.5" in bad[0] and "Add ip4:198.51.100.5" in bad[0]


def test_no_spf_record_no_dkim_and_no_dmarc_are_each_a_warning(monkeypatch):
    server_is(monkeypatch)
    dns(monkeypatch, {})
    texts = [text for level, text in dv.report(provider()) if level == "warn"]
    assert any("no SPF record" in t for t in texts) and any("no common DKIM key" in t for t in texts) and any("no DMARC record" in t for t in texts)


def test_logging_in_as_one_domain_and_sending_from_another_is_pointed_out(monkeypatch):
    server_is(monkeypatch)
    dns(monkeypatch, {("shop.test", "TXT"): ["v=spf1 ip4:203.0.113.9 -all"]})
    mismatch = dv.report(provider(username="me@gmail.test"))
    assert any(level == "warn" and "log in as @gmail.test but send From @shop.test" in text for level, text in mismatch)
    # with an envelope sender named, the check is made on that domain and the warning goes away
    named = dv.report(provider(username="me@gmail.test", envelope_from="bounce@shop.test"))
    assert not any("log in as" in text for _, text in named)


def test_when_dns_cannot_be_reached_the_report_says_so_and_stops(monkeypatch):
    server_is(monkeypatch)

    def down(name, rtype):
        raise dv.LookupFailed(name)

    monkeypatch.setattr(dv, "_query", down)
    checks = dv.report(provider())
    assert levels(checks) == ["ok", "warn"] and "DNS could not be reached" in checks[1][1]


def test_an_address_without_a_domain_gives_no_domain():
    assert dv._domain("WHpanel <no-reply@Shop.Test>") == "shop.test" and dv._domain("") == "" and dv._domain(None) == ""


# --- the real DNS lookup (with a made-up resolver and a made-up DNS-over-HTTPS answer) -------------------------------------------

def test_the_lookup_reads_txt_mx_and_address_answers_from_the_resolver(monkeypatch):
    import dns.resolver

    class Answers(list):
        pass

    class Resolver:
        lifetime = 0

        def resolve(self, name, rtype):
            if rtype == "TXT":
                return Answers([SimpleNamespace(strings=[b"v=spf1 ", b"-all"])])
            if rtype == "MX":
                return Answers([SimpleNamespace(exchange="mail.shop.test.")])
            return Answers([SimpleNamespace(address="203.0.113.9")])

    monkeypatch.setattr(dns.resolver, "Resolver", Resolver)
    assert dv._query("shop.test", "TXT") == ["v=spf1 -all"] and dv._query("shop.test", "MX") == ["mail.shop.test"] and dv._query("shop.test", "A") == ["203.0.113.9"]


def test_a_missing_record_is_an_empty_answer_and_a_blocked_dns_falls_back_to_https(monkeypatch, settings):
    import dns.exception
    import dns.resolver
    import requests

    class Missing:
        lifetime = 0

        def resolve(self, name, rtype):
            raise dns.resolver.NXDOMAIN()

    monkeypatch.setattr(dns.resolver, "Resolver", Missing)
    assert dv._query("nothing.test", "TXT") == []

    class Blocked:
        lifetime = 0

        def resolve(self, name, rtype):
            raise dns.exception.Timeout()

    monkeypatch.setattr(dns.resolver, "Resolver", Blocked)
    settings.DNS_OVER_HTTPS_URL = "https://doh.invalid/resolve"
    answers = {"Answer": [{"type": 16, "data": '"v=spf1 ip4:203.0.113.9 -all"'}, {"type": 15, "data": "10 mail.shop.test."}, {"type": 1, "data": "203.0.113.9"}]}
    monkeypatch.setattr(requests, "get", lambda url, params=None, timeout=None: SimpleNamespace(json=lambda: answers))
    assert dv._query("shop.test", "TXT") == ["v=spf1 ip4:203.0.113.9 -all"]
    assert dv._query("shop.test", "MX") == ["mail.shop.test"] and dv._query("shop.test", "A") == ["203.0.113.9"]

    def unreachable(*args, **kwargs):
        raise requests.ConnectionError("blocked")

    monkeypatch.setattr(requests, "get", unreachable)
    with pytest.raises(dv.LookupFailed):
        dv._query("shop.test", "TXT")
