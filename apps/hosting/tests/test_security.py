"""Security Center (H10): the demo provider's rules (scan, quarantine, folder protection, SSH keys, integrity) and the screen."""
import base64
import hashlib
import struct

import pytest
from django.core.management import call_command

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.core.exceptions import ServiceError
from apps.hosting import control
from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.demo_security import MAX_KEYS, DemoSecurity
from apps.hosting.models import HostingAccount
from apps.products import services as product_services
from apps.products.models import ProductType

pytestmark = pytest.mark.django_db

GOOD = "Strong-Pass-2026"


def field(data):
    return struct.pack(">I", len(data)) + data


def ed25519(seed=b"a"):
    blob = field(b"ssh-ed25519") + field(hashlib.sha256(seed).digest())
    return "ssh-ed25519 " + base64.b64encode(blob).decode() + " me@laptop"


def rsa(bits):
    n = (1 << (bits - 1)) | 12345
    blob = field(b"ssh-rsa") + field(b"\x01\x00\x01") + field(b"\x00" + n.to_bytes((n.bit_length() + 7) // 8, "big"))
    return "ssh-rsa " + base64.b64encode(blob).decode()


@pytest.fixture
def sec(tmp_path):
    site, state = tmp_path / "site", tmp_path / "state"
    (site / "public_html" / "wp-content" / "uploads").mkdir(parents=True)
    (site / "public_html" / "private").mkdir()
    (site / "public_html" / "index.php").write_text("<?php echo 'hello'; ?>", encoding="utf-8")
    (site / ".trash").mkdir()
    (site / ".trash" / "old.php").write_text("<?php eval(base64_decode('x')); ?>", encoding="utf-8")
    return DemoSecurity(state, site, "shop.example.com")


def put(sec, rel, text):
    path = sec.site / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- Malware scan ---------------------------------------------------------------------------------------------------

def test_a_clean_site_has_no_findings_and_the_trash_is_not_scanned(sec):
    result = sec.scan()
    assert result.findings == [] and result.files_scanned == 1 and result.scanned_at


def test_known_bad_patterns_names_and_places_are_reported_most_severe_first(sec):
    put(sec, "public_html/a.php", "<?php eval(base64_decode($x)); ?>")
    put(sec, "public_html/b.php", "<?php system($_GET['c']); ?>")
    put(sec, "public_html/c99.php", "<?php // nothing ?>")
    put(sec, "public_html/wp-content/uploads/x.php", "<?php // nothing ?>")
    put(sec, "public_html/pic.php.jpg", "x")
    put(sec, "public_html/.hidden.php", "<?php // x ?>")
    put(sec, "public_html/s.js", "document.write(unescape('%41'))")
    found = {(f.path, f.rule, f.severity) for f in sec.scan().findings}
    assert ("public_html/a.php", "obfuscated-code", "high") in found and ("public_html/b.php", "runs-visitor-input", "high") in found
    assert ("public_html/c99.php", "known-shell-name", "high") in found
    assert ("public_html/wp-content/uploads/x.php", "script-in-uploads", "medium") in found
    assert ("public_html/pic.php.jpg", "double-extension", "medium") in found
    assert ("public_html/.hidden.php", "hidden-script-file", "medium") in found and ("public_html/s.js", "hidden-script", "medium") in found
    severities = [f.severity for f in sec.scan().findings]
    assert severities == sorted(severities, key=lambda s: s != "high")


def test_quarantine_moves_the_file_away_and_it_can_be_put_back(sec):
    put(sec, "public_html/a.php", "<?php eval(base64_decode($x)); ?>")
    sec.scan()
    item = sec.quarantine("public_html/a.php")
    assert not (sec.site / "public_html" / "a.php").exists() and sec.scan_result().findings == []
    [row] = sec.quarantine_list()
    assert row.path == "public_html/a.php" and row.rule == "obfuscated-code"
    sec.restore(item)
    assert (sec.site / "public_html" / "a.php").exists() and sec.quarantine_list() == []
    with pytest.raises(HostingError, match="not in quarantine"):
        sec.restore(item)


def test_quarantine_refuses_anything_outside_the_site_and_restore_never_overwrites(sec, tmp_path):
    (tmp_path / "secret.txt").write_text("x")
    for bad in ("", "../secret.txt", "/etc/passwd", "public_html/../../secret.txt", "public_html", "nothing.php", ".trash/old.php"):
        with pytest.raises(HostingError):
            sec.quarantine(bad)
    put(sec, "public_html/a.php", "one")
    item = sec.quarantine("public_html/a.php")
    put(sec, "public_html/a.php", "two")
    with pytest.raises(HostingError, match="exists again"):
        sec.restore(item)
    assert (sec.site / "public_html" / "a.php").read_text() == "two"


# --- Folder protection ----------------------------------------------------------------------------------------------

def test_a_folder_is_protected_with_a_hashed_password_and_can_be_opened_again(sec):
    sec.protect("public_html/private", "owner", GOOD)
    [row] = sec.protected()
    assert (row.path, row.login) == ("public_html/private", "owner") and GOOD not in (sec.state / "protected.json").read_text()
    sec.protect("public_html/private", "newlogin", GOOD)  # protecting again replaces the old login
    assert [r.login for r in sec.protected()] == ["newlogin"]
    sec.unprotect("public_html/private")
    assert sec.protected() == []
    with pytest.raises(HostingError, match="not protected"):
        sec.unprotect("public_html/private")


@pytest.mark.parametrize("path,login,password", [("public_html/missing", "owner", GOOD), ("../x", "owner", GOOD), ("public_html/index.php", "owner", GOOD),
                                                 ("public_html/private", "x", GOOD), ("public_html/private", "bad login", GOOD),
                                                 ("public_html/private", "owner", "weakpass"), ("public_html/private", "owner", "alllowercase123")])
def test_bad_folders_logins_and_passwords_are_refused(sec, path, login, password):
    with pytest.raises(HostingError):
        sec.protect(path, login, password)


# --- SSH ------------------------------------------------------------------------------------------------------------

def test_a_valid_key_is_listed_by_fingerprint_and_ssh_needs_one_to_be_switched_on(sec):
    with pytest.raises(HostingError, match="Add a public key first"):
        sec.set_ssh(True)
    fingerprint = sec.add_key("My laptop", ed25519())
    assert fingerprint.startswith("SHA256:") and "=" not in fingerprint
    sec.set_ssh(True)
    status = sec.ssh_status()
    assert status.enabled and status.host == "ssh.shop.example.com" and [(k.label, k.key_type) for k in status.keys] == [("My laptop", "ssh-ed25519")]
    sec.add_key("Office", rsa(2048))
    sec.delete_key(fingerprint)
    sec.delete_key(sec.ssh_status().keys[0].fingerprint)
    assert not sec.ssh_status().enabled  # SSH goes off with the last key


@pytest.mark.parametrize("key", ["", "ssh-ed25519", "ssh-dss AAAA", "ssh-ed25519 not-base64!!", "ssh-ed25519 AAAA", "-----BEGIN OPENSSH PRIVATE KEY-----\nabc",
                                 rsa(1024), "ssh-rsa " + base64.b64encode(field(b"ssh-ed25519") + field(b"x" * 32)).decode()])
def test_bad_or_private_keys_are_refused(sec, key):
    with pytest.raises(HostingError):
        sec.add_key("Key", key)


def test_keys_have_a_name_no_duplicates_and_a_limit(sec):
    with pytest.raises(HostingError, match="short name"):
        sec.add_key("<script>", ed25519())
    sec.add_key("One", ed25519(b"1"))
    with pytest.raises(HostingError, match="already added"):
        sec.add_key("Again", ed25519(b"1"))
    for n in range(MAX_KEYS - 1):
        sec.add_key(f"Key {n}", ed25519(bytes([n, 9])))
    with pytest.raises(HostingError, match="up to"):
        sec.add_key("Too many", ed25519(b"zzz"))
    with pytest.raises(HostingError, match="no longer exists"):
        sec.delete_key("SHA256:nope")


# --- Integrity ------------------------------------------------------------------------------------------------------

def test_integrity_reports_changed_new_and_removed_files_since_the_baseline(sec):
    with pytest.raises(HostingError, match="baseline first"):
        sec.check()
    assert sec.integrity_status().baseline_at is None
    base = sec.baseline()
    assert base.files_in_baseline == 1 and sec.check().changed == []
    put(sec, "public_html/index.php", "<?php echo 'hacked'; ?>")
    put(sec, "public_html/new.php", "x")
    result = sec.check()
    assert result.changed == ["public_html/index.php"] and result.added == ["public_html/new.php"] and result.removed == []
    (sec.site / "public_html" / "index.php").unlink()
    assert sec.check().removed == ["public_html/index.php"]
    assert sec.integrity_status().checked_at  # the last check is remembered


# --- The screen -----------------------------------------------------------------------------------------------------

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


def surl(account, tail=""):
    return f"/account/hosting/{account.pk}/manage/security/{tail}"


def test_the_page_shows_the_summary_findings_and_sample_state(client, owner, account):
    client.force_login(owner)
    page = client.get(surl(account)).content.decode()
    assert "Security" in page and "Malware scan" in page and "cache.php" in page and "hidden with an encoding trick" in page
    assert "Blocked IPs" in page and "Quarantine" in page and "Take a baseline" not in page and "New baseline" in page
    assert "Recent security events" in page and "Nothing yet" in page


def test_scan_quarantine_and_put_back_from_the_page(client, owner, account):
    client.force_login(owner)
    act = surl(account, "do/")
    page = client.post(act, {"action": "scan"}, follow=True).content.decode()
    assert "to look at" in page
    client.post(act, {"action": "quarantine", "path": "public_html/wp-content/uploads/cache.php"})
    page = client.get(surl(account)).content.decode()
    assert "In quarantine" in page and "Put back" in page
    item = control.perform(owner, account, "security", "list_quarantine")[0]
    client.post(act, {"action": "restore", "item": item.item_id})
    assert "In quarantine" not in client.get(surl(account)).content.decode()
    assert "Malware scan run" in client.get(surl(account)).content.decode()


def test_folder_ssh_and_integrity_actions_work_from_the_page(client, owner, account):
    client.force_login(owner)
    act = surl(account, "do/")
    client.post(act, {"action": "protect", "path": "public_html", "login": "owner", "password": GOOD})
    assert "owner" in client.get(surl(account)).content.decode()
    client.post(act, {"action": "unprotect", "path": "public_html"})
    assert control.perform(owner, account, "security", "list_protected_dirs") == []
    client.post(act, {"action": "add_key", "label": "Laptop", "public_key": ed25519()})
    client.post(act, {"action": "ssh", "enabled": "1"})
    status = control.perform(owner, account, "security", "ssh_status")
    assert status.enabled and status.keys[0].label == "Laptop"
    client.post(act, {"action": "delete_key", "fingerprint": status.keys[0].fingerprint})
    assert not control.perform(owner, account, "security", "ssh_status").keys
    client.post(act, {"action": "baseline"})
    control.perform(owner, account, "files", "write_file", "public_html/new.txt", b"new")
    page = client.post(act, {"action": "check"}, follow=True).content.decode()
    assert "difference(s) found" in page and "public_html/new.txt" in page


def test_refusals_are_shown_as_messages(client, owner, account):
    client.force_login(owner)
    act = surl(account, "do/")
    assert "private key" in client.post(act, {"action": "add_key", "label": "x", "public_key": "-----BEGIN OPENSSH PRIVATE KEY-----"}, follow=True).content.decode()
    assert "Add a public key first" in client.post(act, {"action": "ssh", "enabled": "1"}, follow=True).content.decode()
    assert "not inside your website" in client.post(act, {"action": "quarantine", "path": "../x"}, follow=True).content.decode()
    assert "That action is not available" in client.post(act, {"action": "format"}, follow=True).content.decode()


def test_events_and_audit_hold_paths_and_logins_but_never_passwords_or_keys(client, owner, account):
    client.force_login(owner)
    client.post(surl(account, "do/"), {"action": "protect", "path": "public_html", "login": "owner", "password": GOOD})
    client.post(surl(account, "do/"), {"action": "add_key", "label": "Laptop", "public_key": ed25519()})
    page = client.get(surl(account)).content.decode()
    assert "Folder protected" in page and "SSH key added" in page
    events = str(list(AuditEvent.objects.filter(action="hosting.control").values_list("metadata", flat=True)))
    assert GOOD not in events and "AAAA" not in events and ed25519().split()[1] not in events and page.count(GOOD) == 0


def test_strangers_get_nothing_and_a_provider_without_security_shows_the_not_available_page(client, customer, owner, account):
    client.force_login(customer)
    assert client.get(surl(account)).status_code == 404
    assert client.post(surl(account, "do/"), {"action": "scan"}).status_code == 404
    from apps.products.models import Server

    Server.objects.filter(kind="demo").update(kind="manual")
    client.force_login(owner)
    assert "not available for your hosting server yet" in client.get(surl(account)).content.decode()
    with pytest.raises(ServiceError):
        control.perform(owner, account, "security", "get_scan_result")
