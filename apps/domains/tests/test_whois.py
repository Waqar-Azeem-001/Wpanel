"""Phase 17: a raw WHOIS lookup for staff - no registrar involved, so no ``RegistrarProvider`` fixture is needed."""
import pytest

from apps.accounts.roles import Role
from apps.domains import whois

pytestmark = pytest.mark.django_db


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


# --- The lookup itself, no network (``query`` is swapped for a fake) --------------------------------------------

def test_looks_up_the_authoritative_server_then_the_domain():
    calls = []

    def fake_query(server, question, **kwargs):
        calls.append((server, question))
        if server == whois.IANA_WHOIS:
            return "refer:      whois.example-registry.test\nwhois:      whois.example-registry.test\n"
        return "Domain Name: EXAMPLE.COM\nRegistrar: Test Registrar\n"

    result = whois.lookup("example.com", query=fake_query)
    assert calls == [(whois.IANA_WHOIS, "com"), ("whois.example-registry.test", "example.com")]
    assert "EXAMPLE.COM" in result


def test_an_empty_or_dotless_name_is_refused_before_any_network_call():
    calls = []
    with pytest.raises(whois.WhoisError):
        whois.lookup("", query=lambda *a, **k: calls.append(a) or "")
    with pytest.raises(whois.WhoisError):
        whois.lookup("notadomain", query=lambda *a, **k: calls.append(a) or "")
    assert calls == []  # neither ever reached the network


def test_a_tld_iana_does_not_know_raises_a_clear_error():
    with pytest.raises(whois.WhoisError, match="No WHOIS server is known"):
        whois.lookup("example.nosuchtld", query=lambda server, q, **k: "% Not found\n")


def test_a_network_failure_raises_whois_error_not_a_raw_socket_exception():
    def broken(server, question, **kwargs):
        raise OSError("Connection refused")

    with pytest.raises(whois.WhoisError):
        whois.lookup("example.com", query=broken)


def test_the_real_query_function_also_turns_a_connection_failure_into_a_whois_error():
    with pytest.raises(whois.WhoisError, match="Could not reach"):
        whois._query("127.0.0.1", "example.com", timeout=1)


def test_the_name_is_case_and_trailing_dot_insensitive():
    seen = []

    def fake_query(server, question, **kwargs):
        seen.append(question)
        return "whois: whois.example-registry.test\n" if server == whois.IANA_WHOIS else "ok"

    whois.lookup("EXAMPLE.COM.", query=fake_query)
    assert seen == ["com", "example.com"]


# --- The staff page --------------------------------------------------------------------------------------------

def test_the_page_needs_view_domains(client):
    assert client.get("/staff/domains/whois/").status_code == 302  # anonymous: sent to sign in


def test_a_customer_cannot_open_the_staff_page(client, make_user):
    client.force_login(make_user("shopper@example.com", role=Role.CUSTOMER))
    assert client.get("/staff/domains/whois/").status_code == 403


def test_every_staff_role_that_manages_domains_can_open_the_page(client, staff):
    client.force_login(staff(Role.MANAGER))
    assert client.get("/staff/domains/whois/").status_code == 200


def test_a_lookup_renders_the_result_and_a_bad_one_renders_the_error(client, manager, monkeypatch):
    client.force_login(manager)
    monkeypatch.setattr(whois, "lookup", lambda name: f"Domain Name: {name.upper()}")
    page = client.get("/staff/domains/whois/", {"q": "example.com"})
    assert b"EXAMPLE.COM" in page.content

    def boom(name):
        raise whois.WhoisError("No WHOIS server is known for .nosuchtld.")

    monkeypatch.setattr(whois, "lookup", boom)
    page = client.get("/staff/domains/whois/", {"q": "example.nosuchtld"})
    assert b"No WHOIS server is known" in page.content


def test_visiting_with_no_query_shows_the_form_and_nothing_else(client, manager):
    client.force_login(manager)
    page = client.get("/staff/domains/whois/")
    assert page.status_code == 200 and b"No record came back" not in page.content
