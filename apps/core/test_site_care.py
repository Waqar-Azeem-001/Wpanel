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

    def reach(acct, path, token, purpose=""):
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

    def unreachable(acct, path, token, purpose=""):
        raise ServiceError(f"WHPanel could not reach your website {purpose} (ConnectTimeout).", code="unreachable")

    monkeypatch.setattr(site_apps, "reach", unreachable)
    scan = care.start_scan(owner, account)
    assert scan.state == "failed" and "to scan it" in scan.error
    assert scan.script_ref and scan.script_ref not in {e.name for e in control.perform(owner, account, Capability.FILES, "list_files", "public_html")}


def test_a_scan_that_never_reports_back_is_called_failed(world, owner, script_server, monkeypatch):  # noqa: F811
    account = script_server
    monkeypatch.setattr(site_apps, "reach", lambda a, p, t, purpose="": None)
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
    assert 'class="site-card-link"' in page and reverse("hosting_customer:detail", args=[account.pk]) in page and "Choose a website to open its dashboard" in page
