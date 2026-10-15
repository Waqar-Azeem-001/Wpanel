"""Website care: the speed test, the malware scan (the provider's own, and the one-file scanner for servers without our agent), WordPress update rules, scheduled backups and scans,
and the pages. The scanner is PHP, so its logic is run for real with PHP when it is installed; the call to the customer's website is faked here."""
import json
import shutil
import subprocess
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.core.exceptions import ServiceError
from apps.core.test_client_area import browser, owner, stranger  # noqa: F401
from apps.hosting import care, care_scanner, control, scan_rules, site_apps
from apps.hosting.adapters.capabilities import Capability
from apps.hosting.models import SiteCare, SiteScan, SpeedTest, WebsiteApp

pytestmark = pytest.mark.django_db


@pytest.fixture
def account(world):
    from apps.products.models import Server, ServerKind

    account = world.objects["accounts"]["active"]
    Server.objects.filter(pk=account.server_id).update(kind=ServerKind.DEMO)
    return account


@pytest.fixture
def script_server(account, monkeypatch):
    """A server that cannot scan by itself: the one-file scanner is used."""
    real = control.capabilities_for
    monkeypatch.setattr(care._Io, "supports", lambda self, cap: cap != Capability.SECURITY and real(self.account) is not None and cap.value in real(self.account))
    return account


def _wordpress(account):
    return WebsiteApp.objects.create(account=account, kind="wordpress", state="ready", title="Shop", admin_user="a", admin_email="a@gmail.example.com", admin_url="https://x/wp-admin/")


# --- speed ------------------------------------------------------------------------------------------------------------

GOOD = {"final_url": "https://acme.com/", "status": 200, "first_byte_ms": 120, "total_ms": 400, "size": 60_000, "redirects": 0, "https": True, "compressed": True, "cached": True}


def test_a_fast_secure_compressed_page_scores_full_marks_and_each_fault_costs_something():
    assert care.score(GOOD) == (100, [])
    points, advice = care.score({**GOOD, "first_byte_ms": 2500, "https": False, "compressed": False, "size": 3_000_000, "cached": False})
    assert points < 20 and len(advice) >= 5 and any("HTTPS" in a for a in advice)
    assert care.score({**GOOD, "first_byte_ms": 700})[0] < care.score({**GOOD, "first_byte_ms": 300})[0] < 100


def test_a_speed_test_is_stored_and_not_repeated_within_seconds(account, monkeypatch):
    calls = []
    monkeypatch.setattr(care, "_fetch", lambda url: calls.append(url) or dict(GOOD, first_byte_ms=300))
    first = care.speed_test(account)
    assert first.ok and first.score == 92 and first.first_byte_ms == 300 and first.https and calls == ["https://" + account.domain.lower() + "/"]
    assert care.speed_test(account).pk == first.pk and len(calls) == 1  # asked again straight away: the earlier result
    SpeedTest.objects.filter(pk=first.pk).update(created_at=timezone.now() - timedelta(minutes=2))
    assert care.speed_test(account).pk != first.pk


def test_an_unreachable_website_is_a_result_with_a_reason_not_a_crash(account, monkeypatch):
    def down(url):
        raise ServiceError("Your website did not answer (ConnectTimeout).", code="unreachable")

    monkeypatch.setattr(care, "_fetch", down)
    result = care.speed_test(account)
    assert not result.ok and "did not answer" in result.error and result.score == 0


def test_an_error_page_scores_zero(account, monkeypatch):
    monkeypatch.setattr(care, "_fetch", lambda url: dict(GOOD, status=503))
    result = care.speed_test(account)
    assert not result.ok and result.score == 0 and "503" in result.advice[0]


def test_only_the_newest_results_are_kept(account, monkeypatch):
    monkeypatch.setattr(care, "_fetch", lambda url: dict(GOOD))
    for i in range(care.KEEP_RESULTS + 5):
        SpeedTest.objects.create(account=account, url="x", ok=True, score=50)
    SpeedTest.objects.filter(account=account).update(created_at=timezone.now() - timedelta(days=1))
    care.speed_test(account)
    assert SpeedTest.objects.filter(account=account).count() == care.KEEP_RESULTS


def test_the_test_never_follows_a_redirect_into_a_private_network(monkeypatch):
    class Reply:
        is_redirect, status_code, headers = True, 302, {"Location": "http://10.0.0.5/admin"}
        elapsed = timedelta(milliseconds=5)

        def close(self):
            pass

    monkeypatch.setattr(site_apps, "_public", lambda host: host != "10.0.0.5")
    monkeypatch.setattr("requests.get", lambda *a, **k: Reply())
    with pytest.raises(ServiceError) as err:
        care._fetch("https://acme.com/")
    assert err.value.code == "not_public"


# --- scan -------------------------------------------------------------------------------------------------------------

def _put(owner, account, path, data):
    control.perform(owner, account, Capability.FILES, "write_file", f"public_html/{path}", data, overwrite=True)


def test_the_provider_scans_when_it_can(world, owner, account):  # noqa: F811
    _put(owner, account, "evil.php", b"<?php eval(base64_decode($x)); ?>")
    scan = care.start_scan(owner, account)
    assert scan.state == "done" and scan.files_scanned >= 1 and any(f["path"].endswith("evil.php") and f["severity"] == "high" for f in scan.findings)
    assert care.last_scan(account).pk == scan.pk


def test_the_one_file_scanner_is_written_called_and_its_result_read_and_removed(world, owner, script_server, monkeypatch):  # noqa: F811
    account = script_server
    seen = {}

    def reach(acct, path, token, purpose="", domain=""):
        seen.update(path=path, token=token)
        return None

    monkeypatch.setattr(site_apps, "reach", reach)
    scan = care.start_scan(owner, account)
    assert scan.state == "running" and scan.script_ref == seen["path"] and scan.script_ref.startswith("whp-scan-")
    names = {e.name for e in control.perform(owner, account, Capability.FILES, "list_files", "public_html")}
    assert scan.script_ref in names
    source = control.perform(owner, account, Capability.FILES, "read_file", f"public_html/{scan.script_ref}", max_bytes=200_000).decode()
    assert seen["token"] not in source  # only the hash of the token is in the file
    _put(owner, account, scan.result_ref, json.dumps({"ok": True, "files_scanned": 42, "wp_version": "6.8.1", "wp_checked": 30, "findings": [
        {"path": "wp-config.php", "rule": "core-file-modified", "severity": "high", "detail": "x"}]}).encode())
    done = care.poll_scan(owner, account)
    assert done.state == "done" and done.files_scanned == 42 and done.wp_checked == 30 and done.findings[0]["rule"] == "core-file-modified"
    assert scan.result_ref not in {e.name for e in control.perform(owner, account, Capability.FILES, "list_files", "public_html")}


def test_a_website_that_cannot_be_reached_fails_the_scan_and_removes_the_script(world, owner, script_server, monkeypatch):  # noqa: F811
    account = script_server

    def unreachable(acct, path, token, purpose="", domain=""):
        raise ServiceError(f"WHPanel could not reach your website {purpose} (ConnectTimeout).", code="unreachable")

    monkeypatch.setattr(site_apps, "reach", unreachable)
    scan = care.start_scan(owner, account)
    assert scan.state == "failed" and "to scan it" in scan.error
    assert scan.script_ref and scan.script_ref not in {e.name for e in control.perform(owner, account, Capability.FILES, "list_files", "public_html")}


def test_a_scan_that_never_reports_back_is_called_failed(world, owner, script_server, monkeypatch):  # noqa: F811
    account = script_server
    monkeypatch.setattr(site_apps, "reach", lambda a, p, t, purpose="", domain="": None)
    scan = care.start_scan(owner, account)
    SiteScan.objects.filter(pk=scan.pk).update(started_at=timezone.now() - timedelta(minutes=20))
    assert care.poll_scan(owner, account).state == "failed"


@pytest.mark.skipif(shutil.which("php") is None, reason="PHP is not installed here")
def test_the_scanner_php_finds_what_the_rules_describe_and_removes_itself(tmp_path):
    (tmp_path / "wp-content" / "uploads").mkdir(parents=True)
    (tmp_path / "index.php").write_text("<?php echo 'hello';")
    (tmp_path / "bad.php").write_text("<?php eval(base64_decode($x)); ?>")
    (tmp_path / "run.php").write_text("<?php system($_GET['c']);")
    (tmp_path / "c99.php").write_text("<?php // harmless but named like a hacker tool")
    (tmp_path / "pic.php.jpg").write_text("not a picture")
    (tmp_path / "wp-content" / "uploads" / "up.php").write_text("<?php echo 1;")
    (tmp_path / "ad.js").write_text("document.write(unescape('%3Cscript'));")
    name, result, token, source = care_scanner.build()
    (tmp_path / name).write_text(source)
    lint = subprocess.run(["php", "-l", str(tmp_path / name)], capture_output=True, text=True)
    assert "No syntax errors" in lint.stdout, lint.stdout + lint.stderr
    wrong = subprocess.run(["php", "-r", '$_GET["t"]="nope"; include $argv[1];', str(tmp_path / name)], capture_output=True, text=True)
    assert '"ok":false' in wrong.stdout and (tmp_path / name).exists()  # a wrong token does nothing and does not use the file up
    run = subprocess.run(["php", "-r", '$_GET["t"]=$argv[2]; include $argv[1];', str(tmp_path / name), token], capture_output=True, text=True)
    data = json.loads(run.stdout)
    assert data["ok"] is True and not (tmp_path / name).exists()  # it removed itself
    found = {(f["path"], f["rule"]) for f in data["findings"]}
    assert {("bad.php", "obfuscated-code"), ("run.php", "runs-visitor-input"), ("c99.php", "known-shell-name"), ("pic.php.jpg", "double-extension"),
            ("wp-content/uploads/up.php", "script-in-uploads"), ("ad.js", "hidden-script")} <= found
    assert not any(path == "index.php" for path, _ in found)
    assert json.loads((tmp_path / result).read_text())["files_scanned"] == data["files_scanned"]
    assert data["findings"][0]["severity"] == "high"  # most severe first


def test_the_demo_scanner_and_the_one_file_scanner_share_their_rules():
    from apps.hosting.adapters import demo_security

    assert demo_security.PATTERNS is scan_rules.PATTERNS
    assert {r[0] for r in scan_rules.RULES} == {"obfuscated-code", "runs-visitor-input", "hidden-script"}
    assert all("~" not in r[2] for r in scan_rules.RULES)  # the PHP scanner's delimiter


# --- WordPress updates -------------------------------------------------------------------------------------------------

def test_the_rules_file_says_exactly_what_was_chosen():
    minor = care.rules_file("minor", False, False)
    assert "allow_minor_auto_core_updates', '__return_true'" in minor and "allow_major_auto_core_updates', '__return_false'" in minor
    assert "auto_update_plugin', '__return_false'" in minor and "auto_update_theme', '__return_false'" in minor
    everything = care.rules_file("all", True, True)
    assert "allow_major_auto_core_updates', '__return_true'" in everything and "auto_update_plugin', '__return_true'" in everything and "auto_update_theme', '__return_true'" in everything
    off = care.rules_file("off", False, False)
    assert "allow_minor_auto_core_updates', '__return_false'" in off


@pytest.mark.skipif(shutil.which("php") is None, reason="PHP is not installed here")
def test_the_rules_file_is_valid_php(tmp_path):
    for core in ("off", "minor", "all"):
        path = tmp_path / f"{core}.php"
        path.write_text(care.rules_file(core, True, False))
        assert "No syntax errors" in subprocess.run(["php", "-l", str(path)], capture_output=True, text=True).stdout


def test_saving_the_choice_puts_the_file_in_mu_plugins_and_remembers_it(world, owner, account):  # noqa: F811
    _wordpress(account)
    for entry in control.perform(owner, account, Capability.FILES, "list_files", "public_html"):
        control.perform(owner, account, Capability.FILES, "delete_path", entry.path)
    control.perform(owner, account, Capability.FILES, "make_directory", "public_html/wp-content")
    row = care.save_updates(owner, account, "all", True, False)
    assert row.wp_core == "all" and row.wp_plugins and not row.wp_themes and row.updates_written_at
    written = control.perform(owner, account, Capability.FILES, "read_file", "public_html/wp-content/mu-plugins/whp-auto-updates.php", max_bytes=10_000).decode()
    assert "auto_update_plugin', '__return_true'" in written
    care.save_updates(owner, account, "off", False, False)  # again: replaced, no error
    assert "allow_minor_auto_core_updates', '__return_false'" in control.perform(
        owner, account, Capability.FILES, "read_file", "public_html/wp-content/mu-plugins/whp-auto-updates.php", max_bytes=10_000).decode()


def test_updates_are_only_for_a_wordpress_website_and_a_known_choice(world, owner, account):  # noqa: F811
    with pytest.raises(ServiceError) as err:
        care.save_updates(owner, account, "minor", False, False)
    assert err.value.code == "not_wordpress"
    _wordpress(account)
    with pytest.raises(ServiceError) as err:
        care.save_updates(owner, account, "weird", False, False)
    assert err.value.code == "bad_choice"


# --- backups and the daily job ----------------------------------------------------------------------------------------

def test_the_backup_choice_goes_to_the_provider_when_it_schedules_itself(world, owner, account):  # noqa: F811
    state = care.backup_state(owner, account)
    assert state["mode"] == "provider" and "weekly" in dict(state["choices"]) or state["mode"] == "provider"
    value = next(v for v, _ in state["choices"] if v != "off")
    care.save_backup_schedule(owner, account, value)
    assert care.backup_state(owner, account)["current"] == value
    with pytest.raises(ServiceError):
        care.save_backup_schedule(owner, account, "every-second")


def test_where_the_server_cannot_schedule_the_panel_takes_the_backups_and_keeps_the_newest_few(world, owner, account, monkeypatch):  # noqa: F811
    from apps.hosting.adapters.capabilities import BackupOptions

    adapter = care.get_adapter(account.server)
    cls = type(adapter)
    monkeypatch.setattr(cls, "backup_options", lambda self, username: BackupOptions(kinds=[("full", "Full", "x")], schedules=[("off", "Off")], schedule="off"), raising=False)
    state = care.backup_state(owner, account)
    assert state["mode"] == "panel" and dict(state["choices"])["weekly"]
    care.save_backup_schedule(owner, account, "weekly")
    assert SiteCare.objects.get(account=account).backup_schedule == "weekly"
    before = len(control.perform(owner, account, Capability.BACKUPS, "list_backups"))
    for _ in range(care.KEEP_PANEL_BACKUPS + 2):
        assert care._panel_backup(account, timezone.now()) == "ok"
    assert len(control.perform(owner, account, Capability.BACKUPS, "list_backups")) <= max(before, care.KEEP_PANEL_BACKUPS) + 1


def test_the_daily_job_runs_only_what_is_due(world, owner, account, monkeypatch):  # noqa: F811
    started = []
    monkeypatch.setattr(care, "start_scan", lambda user, acct, trigger="manual": started.append((acct.pk, trigger)))
    row = care.care_for(account)
    row.scan_schedule = "weekly"
    row.save()
    assert care.run_due()["scans"] == 1 and started == [(account.pk, "scheduled")]
    SiteScan.objects.create(account=account, state="done", finished_at=timezone.now() - timedelta(days=1))
    assert care.run_due()["scans"] == 0  # scanned yesterday, weekly: not due
    SiteScan.objects.all().update(finished_at=timezone.now() - timedelta(days=8))
    assert care.run_due()["scans"] == 1


# --- the pages --------------------------------------------------------------------------------------------------------

def test_the_care_page_and_overview_show_the_four_things(world, owner, account, monkeypatch):  # noqa: F811
    _wordpress(account)
    client = browser(owner)
    page = client.get(reverse("hosting_customer:care", args=[account.pk]))
    html = page.content.decode()
    assert page.status_code == 200 and "Speed" in html and "Malware scan" in html and "WordPress updates" in html and "Automatic backups" in html
    overview = client.get(reverse("hosting_customer:detail", args=[account.pk])).content.decode()
    assert "Website care" in overview and "Not tested" in overview and "Not scanned" in overview
    assert "Website care" in client.get(reverse("hosting_customer:care", args=[account.pk])).content.decode()  # in the menu too


def test_the_actions_work_and_say_what_happened(world, owner, account, monkeypatch):  # noqa: F811
    monkeypatch.setattr(care, "_fetch", lambda url: dict(GOOD))
    client = browser(owner)
    url = reverse("hosting_customer:care_action", args=[account.pk])
    reply = client.post(url, {"action": "speed"}, follow=True)
    assert "Speed test done: 100 out of 100" in reply.content.decode()
    reply = client.post(url, {"action": "scan"}, follow=True)
    assert "No problems were found" in reply.content.decode() or "to look at" in reply.content.decode()
    reply = client.post(url, {"action": "scan_schedule", "scan_schedule": "weekly"}, follow=True)
    assert SiteCare.objects.get(account=account).scan_schedule == "weekly"
    reply = client.post(url, {"action": "scan_schedule", "scan_schedule": "hourly"}, follow=True)
    assert SiteCare.objects.get(account=account).scan_schedule == "weekly" and "Choose one of the listed options" in reply.content.decode()
    assert client.post(url, {"action": "nope"}, follow=True).status_code == 200


def test_someone_else_cannot_use_the_care_page_of_this_website(world, account, stranger):  # noqa: F811
    client = browser(stranger)
    assert client.get(reverse("hosting_customer:care", args=[account.pk])).status_code == 404
    assert client.post(reverse("hosting_customer:care_action", args=[account.pk]), {"action": "speed"}).status_code == 404


def test_the_websites_list_links_each_card_to_its_dashboard(world, owner, account):  # noqa: F811
    page = browser(owner).get(reverse("hosting_customer:list")).content.decode()
    assert 'class="site-card-link"' in page and reverse("hosting_customer:detail", args=[account.pk]) in page and "Open a hosting plan to manage it" in page


# --- the domain of a new website -----------------------------------------------------------------------------------------

def _server_ip(account, ip="203.0.113.10"):
    from apps.products.models import Server

    Server.objects.filter(pk=account.server_id).update(ip_address=ip)
    account.refresh_from_db()
    account.server.refresh_from_db()


def test_a_domain_that_is_not_with_us_is_flagged_and_given_a_preview_line(world, owner, account, monkeypatch):  # noqa: F811
    from apps.hosting import domain_check

    _server_ip(account)
    monkeypatch.setattr(domain_check, "_addresses", lambda name: {"198.51.100.7"})
    state = domain_check.check(account)
    assert state["state"] == "not_ours" and not state["ours"] and not state["points_here"]
    assert state["hosts_line"] == f"203.0.113.10 {account.domain.lower()} www.{account.domain.lower()}"
    _wordpress(account)
    page = browser(owner).get(reverse("hosting_customer:detail", args=[account.pk])).content.decode()
    assert "not registered with us" in page and "Transfer domain to us" in page and state["hosts_line"] in page and "203.0.113.10" in page


def test_a_domain_that_points_here_and_is_ours_shows_no_note(world, owner, account, monkeypatch):  # noqa: F811
    from apps.domains.models import Domain, DomainStatus
    from apps.hosting import domain_check

    _server_ip(account)
    Domain.objects.create(client=account.client, name=account.domain.lower(), status=DomainStatus.ACTIVE)
    monkeypatch.setattr(domain_check, "_addresses", lambda name: {"203.0.113.10"})
    assert domain_check.check(account)["state"] == "ready"
    _wordpress(account)
    page = browser(owner).get(reverse("hosting_customer:detail", args=[account.pk])).content.decode()
    assert "not registered with us" not in page and "domain-note" not in page


def test_our_domain_that_does_not_point_here_yet_is_told_so(world, account, monkeypatch):  # noqa: F811
    from apps.domains.models import Domain, DomainStatus
    from apps.hosting import domain_check

    _server_ip(account)
    Domain.objects.create(client=account.client, name=account.domain.lower(), status=DomainStatus.ACTIVE)
    monkeypatch.setattr(domain_check, "_addresses", lambda name: set())
    assert domain_check.check(account)["state"] == "ours_not_pointing"
    Domain.objects.update(status=DomainStatus.PENDING_TRANSFER_IN)
    assert domain_check.check(account)["state"] == "transferring"


# --- the rest of the care tools ------------------------------------------------------------------------------------------

def _wp_folder(owner, account):
    for entry in control.perform(owner, account, Capability.FILES, "list_files", "public_html"):
        control.perform(owner, account, Capability.FILES, "delete_path", entry.path)
    control.perform(owner, account, Capability.FILES, "make_directory", "public_html/wp-content")


def test_site_options_write_a_must_use_plugin_that_says_what_was_chosen(world, owner, account):  # noqa: F811
    from apps.hosting import care_tools

    _wordpress(account)
    _wp_folder(owner, account)
    row = care_tools.save_site_options(owner, account, True, True)
    assert row.maintenance and row.discourage_search
    path = "public_html/wp-content/mu-plugins/whp-site-options.php"
    text = control.perform(owner, account, Capability.FILES, "read_file", path, max_bytes=10_000).decode()
    assert "status_header( 503 )" in text and "pre_option_blog_public" in text and "is_user_logged_in()" in text
    care_tools.save_site_options(owner, account, False, False)
    text = control.perform(owner, account, Capability.FILES, "read_file", path, max_bytes=10_000).decode()
    assert "503" not in text and "blog_public" not in text


@pytest.mark.skipif(shutil.which("php") is None, reason="PHP is not installed here")
def test_the_site_options_file_is_valid_php(tmp_path):
    from apps.hosting import care_tools

    path = tmp_path / "o.php"
    path.write_text(care_tools.options_file(True, True))
    assert "No syntax errors" in subprocess.run(["php", "-l", str(path)], capture_output=True, text=True).stdout


def test_the_performance_block_is_added_and_removed_without_touching_other_rules(world, owner, account):  # noqa: F811
    from apps.hosting import care_tools

    mine = "RewriteEngine On\nRewriteRule ^old$ /new [R=301,L]\n"
    on = care_tools.merge_htaccess(mine, True)
    assert "mod_deflate" in on and "RewriteRule ^old$ /new" in on and on.count(care_tools.BEGIN) == 1
    assert care_tools.merge_htaccess(on, True).count(care_tools.BEGIN) == 1  # again: not doubled
    assert care_tools.merge_htaccess(on, False) == mine and care_tools.merge_htaccess("", False) == ""
    _put(owner, account, ".htaccess", mine.encode())
    care_tools.save_performance(owner, account, True)
    assert "mod_expires" in control.perform(owner, account, Capability.FILES, "read_file", "public_html/.htaccess", max_bytes=20_000).decode()
    assert care.care_for(account).performance
    care_tools.save_performance(owner, account, False)
    assert control.perform(owner, account, Capability.FILES, "read_file", "public_html/.htaccess", max_bytes=20_000).decode() == mine


def test_a_flagged_file_can_be_moved_aside_and_put_back_but_only_a_flagged_one(world, owner, account):  # noqa: F811
    from apps.hosting import care_tools

    _put(owner, account, "extra-evil.php", b"<?php eval(base64_decode($x)); ?>")
    scan = care.start_scan(owner, account)
    assert any(f["path"] == "extra-evil.php" for f in scan.findings)
    with pytest.raises(ServiceError) as err:
        care_tools.quarantine_file(owner, account, "index.html")
    assert err.value.code == "not_flagged"
    with pytest.raises(ServiceError):
        care_tools.quarantine_file(owner, account, "../etc/passwd")
    row = care_tools.quarantine_file(owner, account, "extra-evil.php")
    names = {e.name for e in control.perform(owner, account, Capability.FILES, "list_files", "public_html")}
    assert "extra-evil.php" not in names and ".whp-quarantine" in names and len(row.quarantine) == 1
    assert not any(f["path"] == "extra-evil.php" for f in care.last_scan(account).findings)
    care_tools.restore_file(owner, account, row.quarantine[0]["stored"])
    assert "extra-evil.php" in {e.name for e in control.perform(owner, account, Capability.FILES, "list_files", "public_html")}
    assert care.care_for(account).quarantine == []


def test_a_wordpress_system_file_is_never_moved_aside(world, owner, account):  # noqa: F811
    from apps.hosting import care_tools

    SiteScan.objects.create(account=account, state="done", finished_at=timezone.now(), findings=[
        {"path": "wp-includes/version.php", "rule": "core-file-modified", "severity": "high", "detail": "x"}])
    with pytest.raises(ServiceError) as err:
        care_tools.quarantine_file(owner, account, "wp-includes/version.php")
    assert err.value.code == "critical"


def test_the_access_log_is_turned_into_honest_figures():
    from apps.hosting import care_tools

    lines = [
        '203.0.113.5 - - [13/Oct/2026:10:00:00 +0000] "GET / HTTP/1.1" 200 5120 "-" "Mozilla/5.0"',
        '203.0.113.5 - - [13/Oct/2026:10:00:01 +0000] "GET /style.css HTTP/1.1" 200 800 "-" "Mozilla/5.0"',
        '203.0.113.6 - - [13/Oct/2026:10:01:00 +0000] "GET /about HTTP/1.1" 200 4000 "-" "Mozilla/5.0"',
        '198.51.100.9 - - [13/Oct/2026:10:02:00 +0000] "GET / HTTP/1.1" 200 5120 "-" "Googlebot/2.1"',
        '203.0.113.7 - - [13/Oct/2026:10:03:00 +0000] "GET /missing HTTP/1.1" 404 300 "-" "Mozilla/5.0"',
        "not a log line",
    ]
    result = care_tools.parse_access(lines)
    assert result["requests"] == 5 and result["bots"] == 1 and result["visitors"] == 3 and result["page_views"] == 2 and result["unparsed"] == 1
    assert dict(result["top_pages"]) == {"/": 1, "/about": 1}
    assert dict(result["statuses"]) == {"2xx": 4, "4xx": 1}


def test_a_pagespeed_report_is_stored_with_its_figures(world, account, monkeypatch):  # noqa: F811
    from apps.hosting import care_tools

    class Reply:
        status_code = 200

        def json(self):
            return {"lighthouseResult": {"categories": {"performance": {"score": 0.87}}, "audits": {
                "first-contentful-paint": {"displayValue": "1.2 s"}, "largest-contentful-paint": {"displayValue": "2.4 s"}}}}

    seen = {}
    monkeypatch.setattr("requests.get", lambda url, params=None, timeout=None: seen.update(params=params) or Reply())
    row = care_tools.pagespeed(account, "desktop")
    assert row.kind == "pagespeed" and row.score == 87 and row.report["figures"]["First contentful paint"] == "1.2 s" and seen["params"]["strategy"] == "desktop"
    assert SpeedTest.objects.filter(account=account, kind="server").count() == 0  # the server test keeps its own history


def test_pagespeed_failures_are_said_plainly(world, account, monkeypatch):  # noqa: F811
    from apps.hosting import care_tools

    class Limited:
        status_code = 429

    monkeypatch.setattr("requests.get", lambda *a, **k: Limited())
    with pytest.raises(ServiceError) as err:
        care_tools.pagespeed(account)
    assert err.value.code == "rate_limited"


@pytest.mark.skipif(shutil.which("php") is None, reason="PHP is not installed here")
def test_the_wordpress_tool_php_is_valid_refuses_a_wrong_token_and_removes_itself(tmp_path):
    from apps.hosting import wp_tool

    name, result, token, source = wp_tool.build("inventory")
    (tmp_path / name).write_text(source)
    assert "No syntax errors" in subprocess.run(["php", "-l", str(tmp_path / name)], capture_output=True, text=True).stdout
    wrong = subprocess.run(["php", "-r", '$_GET["t"]="nope"; include $argv[1];', str(tmp_path / name)], capture_output=True, text=True)
    assert '"ok":false' in wrong.stdout and (tmp_path / name).exists()
    right = subprocess.run(["php", "-r", '$_GET["t"]=$argv[2]; include $argv[1];', str(tmp_path / name), token], capture_output=True, text=True)
    assert "WordPress was not found" in right.stdout and not (tmp_path / name).exists()  # no WordPress here: it says so, and it was used up
    with pytest.raises(ValueError):
        wp_tool.build("rm -rf")


def test_the_wordpress_manager_runs_one_action_and_returns_what_is_installed(world, owner, account, monkeypatch):  # noqa: F811
    from apps.hosting import care_tools

    _wordpress(account)
    answer = {"ok": True, "core": {"version": "6.8.1", "update": "6.9"}, "plugins": [{"file": "a/a.php", "name": "A", "version": "1", "active": True, "update": ""}],
              "themes": [{"slug": "twentytwentyfive", "name": "Twenty", "version": "1", "active": True, "update": ""}]}
    seen = {}

    def reach(acct, path, token, purpose="", domain=""):
        seen.update(path=path)
        return answer

    monkeypatch.setattr(site_apps, "reach", reach)
    data = care_tools.wordpress_action(owner, account, "deactivate_plugin", "a/a.php")
    assert data["core"]["update"] == "6.9" and data["plugins"][0]["name"] == "A" and seen["path"].startswith("whp-wp-")
    with pytest.raises(ServiceError):
        care_tools.wordpress_action(owner, account, "format_disk")
    monkeypatch.setattr(site_apps, "reach", lambda a, p, t, purpose="", domain="": {"ok": False, "error": "That plugin is not installed."})
    with pytest.raises(ServiceError) as err:
        care_tools.wordpress_action(owner, account, "activate_plugin", "x/x.php")
    assert "not installed" in err.value.message


def test_the_new_pages_render_and_the_actions_answer(world, owner, account, monkeypatch):  # noqa: F811
    _wordpress(account)
    client = browser(owner)
    html = client.get(reverse("hosting_customer:care", args=[account.pk])).content.decode()
    assert "Website options" in html and "Traffic" in html and "Run report" in html and "Faster pages" in html
    assert client.get(reverse("hosting_customer:care_traffic", args=[account.pk])).status_code == 200
    page = client.get(reverse("hosting_customer:care_wordpress", args=[account.pk])).content.decode()
    assert "Load plugins and themes" in page
    url = reverse("hosting_customer:care_action", args=[account.pk])
    assert client.post(url, {"action": "performance", "enabled": "1"}, follow=True).status_code == 200


# --- all websites, delete, staging ----------------------------------------------------------------------------------------

def test_the_websites_page_lists_every_website_with_dashboard_and_menu(world, owner, account):  # noqa: F811
    _wordpress(account)
    page = browser(owner).get(reverse("hosting_customer:websites")).content.decode()
    assert account.domain in page and "Dashboard" in page and "Add website" in page and "Delete website" in page and "Staging website" in page
    assert reverse("hosting_customer:site_delete", args=[account.pk]) in page and reverse("hosting_customer:staging", args=[account.pk]) in page
    assert "Websites" in browser(owner).get(reverse("hosting_customer:detail", args=[account.pk])).content.decode()


def test_deleting_a_website_removes_its_files_and_database_but_not_the_plan(world, owner, account):  # noqa: F811
    from apps.hosting.models import AppState

    _wp_folder(owner, account)
    _put(owner, account, "index.php", b"<?php // site")
    app = _wordpress(account)
    control.perform(owner, account, Capability.DATABASES, "create_database", "wpx")
    name = next(d.name for d in control.perform(owner, account, Capability.DATABASES, "list_databases") if d.name.endswith("wpx") or d.name == "wpx")
    WebsiteApp.objects.filter(pk=app.pk).update(db_name=name)
    with pytest.raises(ServiceError) as err:
        site_apps.delete_website(owner, account, "wrong.example.com")
    assert err.value.code == "confirm_mismatch" and WebsiteApp.objects.filter(account=account).exists()
    report = site_apps.delete_website(owner, account, account.domain.upper())
    assert report["files"] >= 1 and report["database"] is True
    assert not WebsiteApp.objects.filter(account=account).exists()
    assert not [e for e in control.perform(owner, account, Capability.FILES, "list_files", "public_html") if e.name == "index.php"]
    assert name not in {d.name for d in control.perform(owner, account, Capability.DATABASES, "list_databases")}
    account.refresh_from_db()
    assert account.status == "active" and site_apps.needs_setup(account)  # the plan is still there, ready for a new website
    assert AppState  # imported for clarity


def test_a_website_that_is_still_installing_cannot_be_deleted(world, owner, account):  # noqa: F811
    app = _wordpress(account)
    WebsiteApp.objects.filter(pk=app.pk).update(state="installing")
    with pytest.raises(ServiceError) as err:
        site_apps.delete_website(owner, account, account.domain)
    assert err.value.code == "busy"


def test_the_delete_page_needs_the_address_typed_and_then_goes_back_to_the_list(world, owner, account):  # noqa: F811
    _wordpress(account)
    client = browser(owner)
    url = reverse("hosting_customer:site_delete", args=[account.pk])
    assert "Delete website" in client.get(url).content.decode()
    client.post(url, {"confirm": "nope"})
    assert WebsiteApp.objects.filter(account=account).exists()
    reply = client.post(url, {"confirm": account.domain})
    assert reply.status_code == 302 and reply["Location"] == reverse("hosting_customer:websites")
    assert not WebsiteApp.objects.filter(account=account).exists()


def test_staging_is_only_for_a_ready_wordpress_website(world, owner, account):  # noqa: F811
    from apps.hosting import staging

    assert "WordPress" in staging.unavailable_reason(account)
    _wordpress(account)
    assert staging.unavailable_reason(account) == ""
    with pytest.raises(ServiceError):
        WebsiteApp.objects.all().delete()
        staging.create(owner, account)


def test_staging_is_created_published_and_deleted(world, owner, account, monkeypatch):  # noqa: F811
    from apps.hosting import staging
    from apps.hosting.models import StagingSite

    _wordpress(account)
    seen = []

    def reach(acct, path, token, purpose="", domain=""):
        seen.append(purpose)
        return {"ok": True, "files": 12, "tables": 12, "rows": 340}

    monkeypatch.setattr(site_apps, "reach", reach)
    stage = staging.create(owner, account)
    assert stage.state == "ready" and stage.domain == "staging." + account.domain.lower() and stage.db_name and stage.made_domain
    assert stage.domain in {d.name.lower() for d in control.perform(owner, account, Capability.DOMAINS, "list_domains")}
    assert stage.db_name in {d.name for d in control.perform(owner, account, Capability.DATABASES, "list_databases")}
    assert not [e.name for e in control.perform(owner, account, Capability.FILES, "list_files", "public_html") if e.name.startswith("whp-clone-")]
    with pytest.raises(ServiceError) as err:
        staging.create(owner, account)
    assert err.value.code == "exists"
    pushed = staging.push(owner, account, acknowledged=True)
    assert pushed.state == "ready" and pushed.pushed_at is not None and seen == ["to create the staging website", "to publish the staging website"]
    db = stage.db_name
    staging.delete(owner, account)
    assert not StagingSite.objects.filter(account=account).exists()
    assert stage.domain not in {d.name.lower() for d in control.perform(owner, account, Capability.DOMAINS, "list_domains")}
    assert db not in {d.name for d in control.perform(owner, account, Capability.DATABASES, "list_databases")}


def test_a_failed_staging_copy_leaves_nothing_behind(world, owner, account, monkeypatch):  # noqa: F811
    from apps.hosting import staging

    _wordpress(account)
    before_dbs = {d.name for d in control.perform(owner, account, Capability.DATABASES, "list_databases")}
    monkeypatch.setattr(site_apps, "reach", lambda a, p, t, purpose="", domain="": {"ok": False, "error": "Could not connect to the database."})
    stage = staging.create(owner, account)
    assert stage.state == "failed" and "connect" in stage.error
    assert {d.name for d in control.perform(owner, account, Capability.DATABASES, "list_databases")} == before_dbs
    assert stage.domain not in {d.name.lower() for d in control.perform(owner, account, Capability.DOMAINS, "list_domains")}

    def unreachable(a, p, t, purpose="", domain=""):
        raise ServiceError("WHPanel could not reach your website " + purpose + " (ConnectTimeout).", code="unreachable")

    monkeypatch.setattr(site_apps, "reach", unreachable)
    with pytest.raises(ServiceError):
        staging.create(owner, account)  # replaces the failed one, fails again, cleans up again
    assert {d.name for d in control.perform(owner, account, Capability.DATABASES, "list_databases")} == before_dbs
    assert not [e.name for e in control.perform(owner, account, Capability.FILES, "list_files", "public_html") if e.name.startswith("whp-clone-")]


def test_publishing_needs_a_backup_or_the_customers_own(world, owner, account, monkeypatch):  # noqa: F811
    from apps.hosting import staging

    _wordpress(account)
    monkeypatch.setattr(site_apps, "reach", lambda a, p, t, purpose="", domain="": {"ok": True})
    staging.create(owner, account)
    monkeypatch.setattr(staging, "backup_before_push", lambda user, acct: "unsupported")
    with pytest.raises(ServiceError) as err:
        staging.push(owner, account)
    assert err.value.code == "needs_acknowledgement"
    monkeypatch.setattr(staging, "backup_before_push", lambda user, acct: (_ for _ in ()).throw(ServiceError("disk full", code="x")))
    with pytest.raises(ServiceError) as err:
        staging.push(owner, account, acknowledged=True)
    assert err.value.code == "backup_failed"
    staging.delete(owner, account)


def test_the_staging_page_and_its_actions(world, owner, account, monkeypatch):  # noqa: F811
    _wordpress(account)
    monkeypatch.setattr(site_apps, "reach", lambda a, p, t, purpose="", domain="": {"ok": True})
    client = browser(owner)
    url = reverse("hosting_customer:staging", args=[account.pk])
    assert "Create staging website" in client.get(url).content.decode()
    client.post(reverse("hosting_customer:staging_action", args=[account.pk]), {"action": "create"})
    page = client.get(url).content.decode()
    assert "staging." + account.domain.lower() in page and "Publish to live" in page
    reply = client.post(reverse("hosting_customer:staging_action", args=[account.pk]), {"action": "push", "confirm": "nope"}, follow=True)
    assert "Type PUBLISH" in reply.content.decode()
    client.post(reverse("hosting_customer:staging_action", args=[account.pk]), {"action": "delete"})
    assert "Create staging website" in client.get(url).content.decode()


def test_a_website_that_was_not_made_by_whpanel_can_be_deleted_too(world, owner, account):  # noqa: F811
    _wp_folder(owner, account)
    _put(owner, account, "index.php", b"<?php // hand made")
    assert not WebsiteApp.objects.filter(account=account).exists()
    page = browser(owner).get(reverse("hosting_customer:site_delete", args=[account.pk])).content.decode()
    assert "index.php" in page and "wp-content" in page  # the customer sees what will go
    report = site_apps.delete_website(owner, account, account.domain)
    assert report["files"] >= 2
    assert not [e for e in control.perform(owner, account, Capability.FILES, "list_files", "public_html") if e.name not in ("cgi-bin", ".well-known")]
    with pytest.raises(ServiceError) as err:
        site_apps.delete_website(owner, account, account.domain)
    assert err.value.code == "nothing"


def test_a_website_whose_installation_failed_can_be_deleted(world, owner, account):  # noqa: F811
    app = _wordpress(account)
    WebsiteApp.objects.filter(pk=app.pk).update(state="failed", error="The website folder already holds files")
    page = browser(owner).get(reverse("hosting_customer:websites")).content.decode()
    assert reverse("hosting_customer:site_delete", args=[account.pk]) in page
    site_apps.delete_website(owner, account, account.domain)
    assert not WebsiteApp.objects.filter(account=account).exists()
