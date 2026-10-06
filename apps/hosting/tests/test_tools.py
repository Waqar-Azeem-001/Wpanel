"""Logs, FTP accounts and IP blocking (H8b): the demo provider's rules, and the customer screens end to end."""
import pytest
from django.core.management import call_command

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.core.exceptions import ServiceError
from apps.hosting import control
from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.demo_tools import MAX_FTP, MAX_RULES, DemoAccess, DemoFtp, DemoLogs
from apps.hosting.adapters.sandbox import Sandbox
from apps.hosting.models import HostingAccount
from apps.products import services as product_services
from apps.products.models import ProductType

pytestmark = pytest.mark.django_db

GOOD = "Strong-Pass-2026"


# --- Logs ----------------------------------------------------------------------------------------------------------------

@pytest.fixture
def logs(tmp_path):
    return DemoLogs(Sandbox(tmp_path / "files"), "shop.example.com")


def test_logs_are_real_files_with_sample_lines_and_show_only_the_tail(logs, tmp_path):
    view = logs.read("access", 50, "")
    assert view.total == 60 and len(view.lines) == 50 and view.size_bytes > 0
    assert (tmp_path / "files" / "logs" / "access.log").exists()
    assert view.lines == [line for line in (tmp_path / "files" / "logs" / "access.log").read_text().splitlines()][-50:]


def test_logs_can_be_filtered_and_cleared(logs):
    only = logs.read("access", 500, "404")
    assert only.total > 0 and all("404" in line for line in only.lines)
    assert logs.read("error", 200, "NO-SUCH-WORD").lines == []
    logs.clear("error")
    assert logs.read("error", 200, "").total == 0


def test_only_the_two_logs_and_the_listed_sizes_are_accepted(logs):
    for kind in ("../../etc/passwd", "system", ""):
        with pytest.raises(HostingError):
            logs.read(kind, 50, "")
    with pytest.raises(HostingError):
        logs.read("error", 99999, "")


# --- FTP -----------------------------------------------------------------------------------------------------------------

@pytest.fixture
def ftp(tmp_path):
    return DemoFtp(tmp_path / "state", "shop.example.com", lambda rel: rel in ("public_html", "public_html/blog"))


def test_an_ftp_account_is_created_with_a_full_login_and_a_folder(ftp):
    assert ftp.create(" Developer ", GOOD, "/public_html/") == "developer@shop.example.com"
    [row] = ftp.accounts()
    assert (row.login, row.directory) == ("developer@shop.example.com", "public_html")
    assert GOOD not in ftp.store.file.read_text()  # only a hash is kept


@pytest.mark.parametrize("name", ["", "a", "UPPER space", "x" * 40, "-lead", "bad/name", "a@b"])
def test_bad_login_names_are_refused(ftp, name):
    with pytest.raises(HostingError):
        ftp.create(name, GOOD, "")


@pytest.mark.parametrize("password", ["", "short1A", "alllowercase123", "ALLUPPERCASE123", "NoNumbersHere", "aaaaaAAAAA1111"])
def test_weak_passwords_are_refused(ftp, password):
    with pytest.raises(HostingError):
        ftp.create("dev", password, "")


def test_the_folder_must_exist_and_logins_are_unique_and_limited(ftp):
    with pytest.raises(HostingError, match="folder does not exist"):
        ftp.create("dev", GOOD, "elsewhere")
    ftp.create("dev", GOOD, "")
    with pytest.raises(HostingError, match="already exists"):
        ftp.create("dev", GOOD, "")
    for n in range(MAX_FTP - 1):
        ftp.create(f"user{n}", GOOD, "public_html")
    with pytest.raises(HostingError, match="allows"):
        ftp.create("one-more", GOOD, "")


def test_change_password_and_delete(ftp):
    login = ftp.create("dev", GOOD, "")
    before = ftp.store.file.read_text()
    ftp.set_password(login, "Another-Pass-77")
    assert ftp.store.file.read_text() != before
    with pytest.raises(HostingError):
        ftp.set_password(login, "weak")
    ftp.delete(login)
    assert ftp.accounts() == []
    with pytest.raises(HostingError, match="no longer exists"):
        ftp.delete(login)


# --- IP blocking ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def access(tmp_path):
    return DemoAccess(tmp_path / "state")


def test_addresses_and_ranges_are_tidied_and_listed(access):
    access.add(" 203.0.113.7 ", "scraper")
    access.add("203.0.113.9/24", "")
    access.add("2001:db8::1", "")
    assert [r.network for r in access.rules()] == ["203.0.113.7", "203.0.113.0/24", "2001:db8::1"]


@pytest.mark.parametrize("value", ["", "not an ip", "999.1.1.1", "0.0.0.0/0", "10.0.0.0/8", "::/0", "127.0.0.1", "224.0.0.1", "2001:db8::/32"])
def test_nonsense_and_too_broad_blocks_are_refused(access, value):
    with pytest.raises(HostingError):
        access.add(value, "")


def test_duplicates_and_the_limit(access):
    access.add("203.0.113.7", "")
    with pytest.raises(HostingError, match="already blocked"):
        access.add("203.0.113.7", "")
    for n in range(MAX_RULES - 1):
        access.add(f"198.51.100.{n}", "")
    with pytest.raises(HostingError, match="up to"):
        access.add("192.0.2.1", "")
    rule = access.rules()[0].rule_id
    access.delete(rule)
    with pytest.raises(HostingError, match="no longer exists"):
        access.delete(rule)


# --- The screens ---------------------------------------------------------------------------------------------------------

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


def u(account, section, tail=""):
    return f"/account/hosting/{account.pk}/manage/{section}/{tail}"


def test_the_logs_page_shows_filters_and_clears(client, owner, account):
    client.force_login(owner)
    page = client.get(u(account, "logs")).content.decode()
    assert "Error log" in page and "Access log" in page and "matching lines" in page and "Undefined variable" in page
    access = client.get(u(account, "logs"), {"kind": "access", "q": "404", "lines": "50"}).content.decode()
    assert "missing-page" in access and "GET / HTTP" not in access
    client.post(u(account, "logs", "do/"), {"action": "clear", "kind": "error"})
    assert "No lines to show" in client.get(u(account, "logs"), {"kind": "error"}).content.decode()
    assert "Choose the error log or the access log" in client.post(
        u(account, "logs", "do/"), {"action": "clear", "kind": "system"}, follow=True).content.decode()


def test_the_ftp_page_shows_details_and_manages_accounts(client, owner, account):
    client.force_login(owner)
    page = client.get(u(account, "ftp")).content.decode()
    assert "developer@demo-shop.example.com" in page and "ftp.demo-shop.example.com" in page and "1 / 5" in page
    client.post(u(account, "ftp", "do/"), {"action": "add", "name": "designer", "password": GOOD, "directory": "public_html"})
    client.post(u(account, "ftp", "do/"), {"action": "password", "login": "designer@demo-shop.example.com", "password": "Another-Pass-77"})
    assert "designer@demo-shop.example.com" in client.get(u(account, "ftp")).content.decode()
    client.post(u(account, "ftp", "do/"), {"action": "delete", "login": "designer@demo-shop.example.com"})
    assert "designer@" not in client.get(u(account, "ftp")).content.decode()
    weak = client.post(u(account, "ftp", "do/"), {"action": "add", "name": "x1", "password": "weak", "directory": ""}, follow=True).content.decode()
    assert "10 to 128 characters" in weak


def test_the_ip_page_lists_adds_refuses_self_blocks_and_removes(client, owner, account):
    client.force_login(owner)
    page = client.get(u(account, "access")).content.decode()
    assert "198.51.100.23" in page and "192.0.2.0/24" in page and "Scraper network" in page
    client.post(u(account, "access", "do/"), {"action": "add", "network": "203.0.113.50", "note": "test"})
    rules = control.perform(owner, account, "access", "list_ip_rules")
    assert "203.0.113.50" in [r.network for r in rules]
    mine = client.post(u(account, "access", "do/"), {"action": "add", "network": "127.0.0.0/24"}, follow=True).content.decode()
    assert "block your own connection" in mine
    broad = client.post(u(account, "access", "do/"), {"action": "add", "network": "10.0.0.0/8"}, follow=True).content.decode()
    assert "too wide" in broad
    rule = next(r.rule_id for r in rules if r.network == "203.0.113.50")
    client.post(u(account, "access", "do/"), {"action": "delete", "rule": rule})
    assert "203.0.113.50" not in client.get(u(account, "access")).content.decode()


def test_audit_never_holds_passwords_or_log_text(client, owner, account):
    client.force_login(owner)
    client.post(u(account, "ftp", "do/"), {"action": "add", "name": "designer", "password": GOOD, "directory": "public_html"})
    client.post(u(account, "logs", "do/"), {"action": "clear", "kind": "access"})
    events = list(AuditEvent.objects.filter(action="hosting.control").values_list("metadata", flat=True))
    assert any(e.get("operation") == "create_ftp_account" and e.get("name") == "designer" for e in events)
    assert any(e.get("operation") == "clear_log" and e.get("kind") == "access" for e in events)
    assert GOOD not in str(events)


def test_strangers_get_nothing_and_a_provider_without_these_tools_shows_the_not_available_page(client, customer, owner, account):
    client.force_login(customer)
    for section in ("logs", "ftp", "access"):
        assert client.get(u(account, section)).status_code == 404
        assert client.post(u(account, section, "do/"), {"action": "delete"}).status_code == 404
    from apps.products.models import Server

    Server.objects.filter(kind="demo").update(kind="manual")
    client.force_login(owner)
    for section in ("logs", "ftp", "access"):
        assert "not available for your hosting server yet" in client.get(u(account, section)).content.decode()
    with pytest.raises(ServiceError):
        control.perform(owner, account, "ftp", "list_ftp_accounts")
