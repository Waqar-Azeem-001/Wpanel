"""The OnlineNIC adapter's remaining branches: bad input, a registrar that cannot be reached, odd answers, and the operations not covered elsewhere."""
from datetime import timezone
from types import SimpleNamespace

import pytest
import requests

from apps.domains.adapters import onlinenic
from apps.domains.adapters.base import RegistrarError
from apps.domains.adapters.onlinenic import OnlineNicAdapter, phone_for_registry

CREDS = {"user": "100", "password": "pw", "apikey": "KEY", "nameservers": ["ns1.x.pk", "ns2.x.pk"]}
GOOD = {"code": 1000, "message": "ok"}
OWNER = {"name": "Ada Khan", "organization": "", "street": "1 Mall Road", "city": "Lahore", "state": "Punjab", "postcode": "54000",
         "country": "PK", "phone": "+92 300 1234567", "email": "ada@acme.test"}


def adapter():
    return OnlineNicAdapter(CREDS, sandbox=False)


def answers(monkeypatch, table, seen=None):
    def post(url, data=None, timeout=None):
        command = url.rsplit("=", 1)[1]
        if seen is not None:
            seen.append((command, data))
        reply = table[command]
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(json=lambda: reply)

    monkeypatch.setattr(onlinenic.requests, "post", post)


@pytest.mark.parametrize("phone,country,message", [("", "PK", "no phone"), ("---", "PK", "no phone"), ("+999999", "PK", "not recognised"), ("5551234", "ZZ", "country code")])
def test_a_phone_number_the_registry_cannot_use_is_explained(phone, country, message):
    with pytest.raises(RegistrarError, match=message):
        phone_for_registry(phone, country)


def test_a_national_number_gets_its_country_code():
    assert phone_for_registry("0300-1234567", "pk") == "+92.3001234567"
    assert phone_for_registry("(415) 555 0100", "US") == "+1.4155550100"


@pytest.mark.parametrize("failure", [requests.ConnectionError("down"), requests.Timeout("slow"), ValueError("not json")])
def test_an_unreachable_or_garbled_registrar_is_one_clear_error(monkeypatch, failure):
    answers(monkeypatch, {"getAuthCode": failure})
    with pytest.raises(RegistrarError, match="Could not reach the registrar"):
        adapter().get_auth_code("a.com")


def test_a_refusal_carries_the_registrars_own_words_and_code(monkeypatch):
    answers(monkeypatch, {"renewDomain": {"code": 1023, "message": "Billing error, please check on your account balance."}})
    with pytest.raises(RegistrarError, match=r"Billing error.*\(code 1023\)"):
        adapter().renew_domain("a.com", "a.com", 1)


def test_an_answer_without_a_contact_or_a_code_is_refused(monkeypatch):
    answers(monkeypatch, {"createContact": {**GOOD, "data": {}}})
    with pytest.raises(RegistrarError, match="did not return a contact"):
        adapter().register_domain("a.com", 1, [], OWNER)
    answers(monkeypatch, {"getAuthCode": {**GOOD, "data": {}}})
    with pytest.raises(RegistrarError, match="did not return a transfer code"):
        adapter().get_auth_code("a.com")


def test_a_transfer_in_needs_the_code_and_is_sent_as_pending(monkeypatch):
    with pytest.raises(RegistrarError, match="authorization code is required"):
        adapter().transfer_domain("a.com", "", 1, OWNER)
    seen = []
    answers(monkeypatch, {"createContact": {**GOOD, "data": {"contactid": "C5"}}, "transferDomain": GOOD}, seen)
    result = adapter().transfer_domain("a.com", "EPP", 1, OWNER)
    assert result == {"provider_ref": "a.com", "pending": True}
    assert dict(seen)["transferDomain"]["password"] == "EPP" and dict(seen)["transferDomain"]["contactid"] == "C5"


def test_the_transfer_status_is_the_registrars_own_word(monkeypatch):
    answers(monkeypatch, {"queryTransferStatus": {**GOOD, "data": {"status": "pending at the old registrar"}}})
    assert adapter().transfer_status("a.com", "EPP") == "pending at the old registrar"


def test_renewing_returns_the_new_expiry_from_the_answer_or_asks_again(monkeypatch):
    answers(monkeypatch, {"renewDomain": {**GOOD, "data": {"expdate": "2028-01-15"}}})
    assert adapter().renew_domain("a.com", "a.com", 1)["expires_at"].year == 2028
    answers(monkeypatch, {"renewDomain": {**GOOD, "data": {}}, "infoDomain": {**GOOD, "data": {"expdate": "2029-03-01"}}})
    assert adapter().renew_domain("a.com", "a.com", 1)["expires_at"].year == 2029  # the answer had no date: read it from the domain's details


def test_the_domain_details_are_read_into_plain_values(monkeypatch):
    answers(monkeypatch, {"infoDomain": {**GOOD, "data": {"status": "ok, clientTransferProhibited", "expdate": "2027-10-19", "dns1": "ns1.x.pk", "dns2": "ns2.x.pk", "dns3": ""}}})
    info = adapter().get_domain("a.com", "a.com")
    assert info["nameservers"] == ["ns1.x.pk", "ns2.x.pk"] and info["is_locked"] is True
    assert info["expires_at"].tzinfo == timezone.utc and info["expires_at"].isoformat().startswith("2027-10-19")
    answers(monkeypatch, {"infoDomain": {**GOOD, "data": {"status": "ok", "expdate": "garbage"}}})
    info = adapter().get_domain("a.com", "a.com")
    assert info["expires_at"] is None and info["is_locked"] is False  # an odd date is "unknown", not a crash


def test_nameservers_are_sent_numbered_and_no_more_than_six(monkeypatch):
    seen = []
    answers(monkeypatch, {"updateDomainDns": GOOD}, seen)
    adapter().update_nameservers("a.com", "a.com", [f"ns{i}.x.pk" for i in range(1, 9)])
    sent = seen[0][1]
    assert sent["dns1"] == "ns1.x.pk" and sent["dns6"] == "ns6.x.pk" and "dns7" not in sent


def test_the_panel_keeps_dns_records_itself_so_the_adapter_has_nothing_to_push_or_fetch():
    assert adapter().get_dns("a.com", "a.com") == [] and adapter().update_dns("a.com", "a.com", [{"x": 1}]) is None


def test_an_available_name_is_available_and_a_taken_one_is_not(monkeypatch):
    answers(monkeypatch, {"checkDomain": {**GOOD, "data": {"avail": "1", "Premium": "false"}}})
    assert adapter().check_availability("free.com") is True
    answers(monkeypatch, {"checkDomain": {**GOOD, "data": {"avail": "0"}}})
    assert adapter().check_availability("taken.com") is False


def test_registering_and_transferring_in_need_the_owners_details_complete():
    with pytest.raises(RegistrarError, match="owner's details"):
        adapter().transfer_domain("a.com", "EPP", 1, None)
    with pytest.raises(RegistrarError, match="state or province, postcode"):
        adapter().register_domain("a.com", 1, [], {**OWNER, "state": "", "postcode": ""})
    with pytest.raises(RegistrarError, match="not all set"):
        OnlineNicAdapter({"user": "1"}, sandbox=False).check_availability("a.com")
