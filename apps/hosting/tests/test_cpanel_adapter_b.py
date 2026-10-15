"""The cPanel adapter's customer tools, part b (H14b): files, domains, SSL, cron, backups - against the same fake WHM as test_cpanel_adapter.py."""
import time

import pytest

from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.capabilities import CapabilityNotSupported
from apps.hosting.tests.test_cpanel_adapter import USER, adapter

HOME = {("uapi_cpanel", "Variables::get_user_information"): {"home": "/home/acme"}}
FILES = {**HOME, ("uapi_cpanel", "Fileman::list_files"): [
    {"file": "public_html", "type": "dir", "mtime": 1_700_000_000, "nicemode": "755"},
    {"file": ".trash", "type": "dir"}, {"file": "notes.txt", "type": "file", "size": "120", "mtime": "1700000100", "nicemode": "0644"}]}


def test_files_are_listed_relative_to_the_home_folder_and_the_servers_own_folders_are_hidden():
    real, fake = adapter(FILES)
    entries = real.list_files(USER, "")
    assert [(e.name, e.is_dir, e.permissions) for e in entries] == [("public_html", True, "0755"), ("notes.txt", False, "0644")]
    assert entries[1].path == "notes.txt" and entries[1].size_bytes == 120 and entries[1].modified_at.startswith("2023-")
    assert fake.uapi_calls("Fileman", "list_files")[0][0]["dir"] == "/home/acme"
    real.list_files(USER, "public_html/css")
    assert fake.uapi_calls("Fileman", "list_files")[-1][0]["dir"] == "/home/acme/public_html/css"


@pytest.mark.parametrize("bad", ["../etc", "/etc/passwd", "a/../../b", "a\\b", "x\x00y"])
def test_a_path_outside_the_home_folder_is_refused_before_anything_is_sent(bad):
    real, fake = adapter(FILES)
    for call in (lambda: real.list_files(USER, bad), lambda: real.read_file(USER, bad, max_bytes=100), lambda: real.make_directory(USER, bad),
                 lambda: real.rename_path(USER, bad, "x"), lambda: real.copy_path(USER, "a", bad), lambda: real.set_permissions(USER, bad, "644"),
                 lambda: real.delete_path(USER, bad)):
        with pytest.raises(HostingError):
            call()
    assert not fake.uapi_calls("Fileman") and not fake.api2_calls()


def test_reading_and_saving_text_files():
    real, fake = adapter({**FILES, ("uapi_cpanel", "Fileman::get_file_content"): {"content": "hello"}})
    assert real.read_file(USER, "public_html/a.txt", max_bytes=10) == b"hello"
    with pytest.raises(HostingError, match="too large"):
        real.read_file(USER, "public_html/a.txt", max_bytes=3)
    real.write_file(USER, "public_html/new.txt", "café".encode("utf-8"))
    params, secret = fake.uapi_calls("Fileman", "save_file_content")[0]
    assert (params["dir"], params["file"], params["content"], secret) == ("/home/acme/public_html", "new.txt", "café", True)
    with pytest.raises(HostingError, match="Only text files"):
        real.write_file(USER, "public_html/pic.png", b"\xff\xd8\xff")
    with pytest.raises(HostingError, match="already exists"):
        real.write_file(USER, "notes.txt", b"x")
    real.write_file(USER, "notes.txt", b"x", overwrite=True)


def test_file_changes_use_the_documented_operations():
    real, fake = adapter(FILES)
    real.make_directory(USER, "public_html/img")
    made = fake.api2_calls("Fileman", "mkdir")[0][0]
    assert made["path"] == "/home/acme/public_html" and made["name"] == "img"
    real.rename_path(USER, "public_html/a.txt", "public_html/b.txt")
    rename = fake.api2_calls("Fileman", "fileop")[-1][0]
    assert (rename["op"], rename["sourcefiles"], rename["destfiles"]) == ("rename", "/home/acme/public_html/a.txt", "b.txt")
    real.rename_path(USER, "public_html/a.txt", "archive/a.txt")
    move = fake.api2_calls("Fileman", "fileop")[-1][0]
    assert (move["op"], move["destfiles"]) == ("move", "/home/acme/archive")
    real.copy_path(USER, "public_html/a.txt", "backup/a-copy.txt")
    assert [p["op"] for p, _ in fake.api2_calls("Fileman", "fileop")[-2:]] == ["copy", "rename"]
    real.delete_path(USER, "public_html/a.txt")
    real.trash_path(USER, "public_html/b.txt")
    assert [p["op"] for p, _ in fake.api2_calls("Fileman", "fileop")[-2:]] == ["unlink", "trash"]


def test_cpanel_file_permissions_follow_the_same_safety_rules():
    real, fake = adapter(FILES)
    real.set_permissions(USER, "notes.txt", "600")
    chmod = fake.api2_calls("Fileman", "fileop")[-1][0]
    assert chmod["op"] == "chmod" and chmod["metadata"] == "600"
    for mode in ("777", "666", "4755", "abc"):
        with pytest.raises(HostingError):
            real.set_permissions(USER, "notes.txt", mode)
    with pytest.raises(HostingError):
        real.set_permissions(USER, "", "644")


def test_zip_and_extract_and_the_unsupported_file_features():
    real, fake = adapter(FILES)
    assert real.archive_paths(USER, ["public_html", "notes.txt"], "site.zip") == 2
    zipped = fake.api2_calls("Fileman", "fileop")[-1][0]
    assert zipped["op"] == "compress" and zipped["sourcefiles"] == "/home/acme/public_html,/home/acme/notes.txt" and zipped["metadata"] == "zip"
    real.extract_archive(USER, "site.zip", "restore")
    assert fake.api2_calls("Fileman", "fileop")[-1][0]["op"] == "extract"
    with pytest.raises(HostingError, match="Only .zip"):
        real.extract_archive(USER, "site.tar", "")
    for call in (lambda: real.list_trash(USER), lambda: real.restore_trash(USER, "x"), lambda: real.search_files(USER, "", "q")):
        with pytest.raises(CapabilityNotSupported):
            call()


DOMAIN_ANSWERS = {("uapi_cpanel", "DomainInfo::domains_data"): {
    "main_domain": {"domain": "acme.com", "documentroot": "/home/acme/public_html"},
    "addon_domains": [{"domain": "acme-shop.com", "documentroot": "/home/acme/public_html/acme-shop.com"}],
    "sub_domains": [{"domain": "blog.acme.com", "documentroot": "/home/acme/public_html/blog"}], "parked_domains": ["acme.net"]},
    **HOME, "accountsummary": {"acct": [{"maxaddons": "5", "maxsub": "unlimited"}]},
    ("uapi_cpanel", "Mime::list_redirects"): [{"domain": "acme-shop.com", "destination": "https://shop.example.org/", "type": "permanent"}]}


def test_domains_are_listed_with_kind_folder_and_redirect_and_limits_are_read():
    real, _ = adapter(DOMAIN_ANSWERS)
    rows = {d.name: d for d in real.list_domains(USER)}
    assert (rows["acme.com"].kind, rows["acme.com"].document_root) == ("primary", "public_html")
    shop = rows["acme-shop.com"]
    assert (shop.kind, shop.document_root, shop.redirect_to) == ("addon", "public_html/acme-shop.com", "https://shop.example.org/")
    assert rows["blog.acme.com"].kind == "subdomain" and rows["acme.net"].kind == "alias"
    limits = real.domain_limits(USER)
    assert limits.max_addon_domains == 5 and limits.max_subdomains is None


def test_adding_and_removing_each_kind_of_domain_uses_its_own_cpanel_function():
    real, fake = adapter(DOMAIN_ANSWERS)
    real.add_domain(USER, "new-shop.com", "addon", "")
    add = fake.api2_calls("AddonDomain", "addaddondomain")[0][0]
    assert (add["newdomain"], add["dir"], add["subdomain"]) == ("new-shop.com", "public_html/new-shop.com", "new-shop")
    real.add_domain(USER, "shop.acme.com", "subdomain", "public_html/shop")
    sub = fake.api2_calls("SubDomain", "addsubdomain")[0][0]
    assert (sub["domain"], sub["rootdomain"], sub["dir"]) == ("shop", "acme.com", "public_html/shop")
    real.add_domain(USER, "acme.org", "alias", "")
    park = fake.api2_calls("Park", "park")[0][0]
    assert park["domain"] == "acme.org" and "topdomain" not in park
    real.remove_domain(USER, "acme-shop.com")
    assert fake.api2_calls("AddonDomain", "deladdondomain")[0][0]["subdomain"] == "acme-shop_com.acme.com"
    real.remove_domain(USER, "blog.acme.com")
    real.remove_domain(USER, "acme.net")
    assert fake.api2_calls("SubDomain", "delsubdomain") and fake.api2_calls("Park", "unpark")


@pytest.mark.parametrize("call", [lambda r: r.add_domain(USER, "acme.com", "addon", ""), lambda r: r.add_domain(USER, "bad_domain", "addon", ""),
                                  lambda r: r.add_domain(USER, "x.victim.com", "subdomain", ""), lambda r: r.add_domain(USER, "x.com", "primary", ""),
                                  lambda r: r.add_domain(USER, "x.com", "addon", "../../etc"), lambda r: r.remove_domain(USER, "acme.com"),
                                  lambda r: r.remove_domain(USER, "victim.com")])
def test_bad_domain_changes_are_refused_before_any_cpanel_call(call):
    real, fake = adapter(DOMAIN_ANSWERS)
    with pytest.raises(HostingError):
        call(real)
    assert not fake.api2_calls()


def test_redirects_are_set_and_removed_and_only_web_addresses_are_accepted():
    real, fake = adapter(DOMAIN_ANSWERS)
    real.set_redirect(USER, "acme.com", "https://www.example.org/", False)
    add = fake.uapi_calls("Mime", "add_redirect")[0][0]
    assert add["domain"] == "acme.com" and add["type"] == "temp" and add["redirect"] == "https://www.example.org/"
    real.set_redirect(USER, "acme-shop.com", "", True)
    assert fake.uapi_calls("Mime", "delete_redirect")
    for bad in ("javascript:alert(1)", "ftp://x.test/", "//x.test/", "www.example.org"):
        with pytest.raises(HostingError):
            real.set_redirect(USER, "acme.com", bad, True)
    with pytest.raises(HostingError, match="not on this account"):
        real.set_redirect(USER, "victim.com", "https://x.test/", True)


def test_ssl_status_comes_from_the_installed_certificates_and_unsupported_switches_say_so():
    soon, later, gone = (int(time.time()) + 5 * 86400, int(time.time()) + 60 * 86400, int(time.time()) - 86400)
    real, fake = adapter({("uapi_cpanel", "SSL::installed_hosts"): [
        {"servername": "acme.com", "certificate": {"not_after": later, "not_before": later - 90 * 86400, "issuer": {"organizationName": "Free CA"}}},
        {"servername": "acme-shop.com", "certificate": {"not_after": soon}}, {"servername": "blog.acme.com", "certificate": {"not_after": gone}}]})
    rows = {s.domain: s for s in real.list_ssl_status(USER)}
    assert (rows["acme.com"].status, rows["acme.com"].issuer, rows["acme.com"].active) == ("active", "Free CA", True)
    assert rows["acme-shop.com"].status == "expiring" and rows["blog.acme.com"].status == "expired" and rows["acme.net"].status == "none"
    assert real.get_ssl_status(USER, "acme.com").days_left in (59, 60)
    real.request_ssl(USER, "acme.com")
    assert fake.uapi_calls("SSL", "start_autossl_check")
    real.remove_ssl(USER, "acme.com")
    assert fake.uapi_calls("SSL", "delete_ssl")[0][0]["host"] == "acme.com"
    with pytest.raises(HostingError, match="no certificate"):
        real.remove_ssl(USER, "acme.net")
    for call in (lambda: real.set_ssl_auto_renew(USER, "acme.com", False), lambda: real.set_https_redirect(USER, "acme.com", True)):
        with pytest.raises(CapabilityNotSupported):
            call()
    with pytest.raises(HostingError, match="not on this account"):
        real.get_ssl_status(USER, "victim.com")


CRON = {("cpanel", "Cron::listcron"): [
    {"count": 0, "linekey": "k1", "minute": "0", "hour": "2", "day": "*", "month": "*", "weekday": "*", "command": "php public_html/cleanup.php"},
    {"count": 1, "linekey": "k2", "minute": "*/15", "hour": "*", "day": "*", "month": "*", "weekday": "*", "command": "#WHP-DISABLED# php public_html/sync.php"}]}


def test_cron_jobs_are_read_including_switched_off_ones():
    real, _ = adapter(CRON)
    jobs = {j.job_id: j for j in real.list_cron_jobs(USER)}
    assert (jobs["k1"].schedule, jobs["k1"].command, jobs["k1"].enabled) == ("0 2 * * *", "php public_html/cleanup.php", True)
    assert (jobs["k2"].schedule, jobs["k2"].command, jobs["k2"].enabled) == ("*/15 * * * *", "php public_html/sync.php", False)
    assert real.cron_options(USER).max_jobs == 10


def test_cron_jobs_obey_the_same_rules_as_everywhere():
    real, fake = adapter(CRON)
    real.create_cron_job(USER, "30 4 * * *", "php public_html/new.php")
    add = fake.api2_calls("Cron", "add_line")[0][0]
    assert (add["minute"], add["hour"], add["command"]) == ("30", "4", "php public_html/new.php")
    for schedule, command in (("* * * * *", "php a.php"), ("0 2 * * *", "rm -rf /"), ("0 2 * * *", "php ../a.php"), ("0 2 * * *", "php a.php; ls"),
                              ("x", "php a.php")):
        with pytest.raises(HostingError):
            real.create_cron_job(USER, schedule, command)
    with pytest.raises(HostingError, match="already exists"):
        real.create_cron_job(USER, "0 2 * * *", "php public_html/cleanup.php")
    assert len(fake.api2_calls("Cron", "add_line")) == 1


def test_editing_switching_off_and_deleting_a_job():
    real, fake = adapter(CRON)
    real.update_cron_job(USER, "k1", enabled=False)
    assert fake.api2_calls("Cron", "remove_line")[0][0]["line"] == "0"
    assert fake.api2_calls("Cron", "add_line")[0][0]["command"] == "#WHP-DISABLED# php public_html/cleanup.php"
    real.update_cron_job(USER, "k2", schedule="0 5 * * *")
    assert fake.api2_calls("Cron", "add_line")[-1][0]["command"].startswith("#WHP-DISABLED# ")  # still off after the edit
    real.delete_cron_job(USER, "k1")
    with pytest.raises(HostingError, match="no longer exists"):
        real.delete_cron_job(USER, "nope")
    with pytest.raises(CapabilityNotSupported):
        real.run_cron_job(USER, "k1")


def test_backups_list_and_make_a_full_one_and_honestly_cannot_test_or_restore():
    real, fake = adapter({("uapi_cpanel", "Backup::list_backups"): [
        {"file": "backup-10.5.2026_03-00-01_acme.tar.gz", "size": 5242880}, "backup-10.1.2026_03-00-01_acme.tar.gz", "not-a-backup.txt"]})
    backups = real.list_backups(USER)
    assert [(b.kind, b.size_mb) for b in backups] == [("full", 5.0), ("full", None)] and backups[0].created_at.startswith("2026-10-05")
    real.create_backup(USER, "full")
    assert fake.uapi_calls("Backup", "fullbackup_to_homedir")
    with pytest.raises(HostingError, match="Only a full"):
        real.create_backup(USER, "files")
    for call in (lambda: real.verify_backup(USER, "x"), lambda: real.restore_backup(USER, "x"), lambda: real.delete_backup(USER, "x"),
                 lambda: real.set_backup_schedule(USER, "daily"), lambda: real.set_backup_offsite(USER, True)):
        with pytest.raises(CapabilityNotSupported):
            call()
    assert real.backup_options(USER).kinds[0][0] == "full"


def test_an_api2_failure_is_a_plain_error():
    real, _ = adapter({("cpanel", "Fileman::mkdir"): [{"result": 0, "reason": "Permission denied"}]})
    with pytest.raises(HostingError, match="Permission denied"):
        real.make_directory(USER, "x")
    real._call = lambda *a, **k: {"unexpected": True}
    with pytest.raises(HostingError, match="do not understand"):
        real.make_directory(USER, "x")


def test_copying_into_the_same_folder_goes_through_a_temporary_folder_and_cleans_it_up():
    real, fake = adapter({**FILES, ("cpanel", "Fileman::fileop"): [{"result": 1}]})
    real.copy_path(USER, "public_html/a.txt", "public_html/a-copy.txt")
    operations = [(p["op"], p["sourcefiles"].rsplit("/", 1)[-1], p.get("destfiles", "").rsplit("/", 1)[-1]) for p, _ in fake.api2_calls("Fileman", "fileop")]
    assert [o[0] for o in operations] == ["copy", "rename", "move", "unlink"]
    assert operations[1][2] == "a-copy.txt" and fake.api2_calls("Fileman", "mkdir")[0][0]["name"].startswith("whpanel-copy-")
    assert fake.api2_calls("Fileman", "fileop")[-1][0]["sourcefiles"].startswith("/home/acme/tmp/whpanel-copy-")


def test_a_service_check_that_timed_out_does_not_make_a_service_look_degraded():
    from apps.hosting.tests.test_infrastructure import GOOD, whm

    responses = {**GOOD, "servicestatus:dovecot": HostingError("timed out")}
    assert {s.key: s.state for s in whm(responses).node_status().services}["mail"] == "running"
