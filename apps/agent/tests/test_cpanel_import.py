"""Taking over a cPanel account on a node (M2): read the pkgacct archive, pre-flight report, import files / databases / mail, validate, discard. Against a pretend machine and an in-memory database."""
import hashlib

import pytest

from apps.agent import catalog
from apps.agent.node.cpanel_import import Archive, discover, parse_mysql_grants
from apps.agent.node.website import HandlerError
from apps.agent.reference_agent import NativeMachine

from .cpanel_archive import build

ARCHIVE = "cpmove-acmeuser.tar.gz"


@pytest.fixture
def node(tmp_path):
    machine = NativeMachine(files_dir=tmp_path / "node", capabilities=("web", "database", "mail"))
    machine.run("CREATE_WEBSITE", {"account": "acmeuser", "domain": "acme.com", "php_version": "8.1"})
    (tmp_path / "node" / "acmeuser" / "sites" / "acme.com" / "public_html").mkdir(parents=True, exist_ok=True)
    stage = tmp_path / "node" / "_stage" / "migrations"
    stage.mkdir(parents=True)
    build(stage / ARCHIVE)
    return machine, stage / ARCHIVE, tmp_path / "node"


def run(machine, command, **p):
    return machine.run(command, {"account": "acmeuser", "archive": ARCHIVE, **p})


def test_the_report_says_what_is_in_the_archive_and_what_will_not_move(node):
    machine, path, _ = node
    code, report = run(machine, "MIGRATE_ANALYZE")
    assert code == "analyzed" and report["user"] == "acmeuser" and report["main_domain"] == "acme.com"
    assert report["php_cpanel"] == "ea-php74" and report["php_target"] == "8.1"
    assert [d["name"] for d in report["databases"]] == ["acmeuser_shop"] and report["db_users"] == ["acmeuser_shop"]
    assert report["mail"][0]["domain"] == "acme.com" and report["mail"][0]["mailboxes"] == ["info"] and report["forwarders"] == 1
    assert report["files"] == 3 and "private/config.ini" not in str(report["blockers"])  # caches, logs and the mail folder are not website files
    assert report["blockers"] == [] and report["skipped_links"] == 1
    text = " ".join(report["warnings"])
    assert "cron" in text and "ea-php74" in text and "Certificates" not in text and "link" in text
    assert report["archive_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_an_addon_domain_blocks_automatic_migration(node, tmp_path):
    machine, path, _ = node
    build(path, addon=True)
    _, report = run(machine, "MIGRATE_ANALYZE")
    assert any("addon" in b for b in report["blockers"])


def test_a_wrong_checksum_is_refused_and_a_right_one_accepted(node):
    machine, path, _ = node
    with pytest.raises(HandlerError) as err:
        run(machine, "MIGRATE_ANALYZE", expected_sha256="0" * 64)
    assert err.value.code == "checksum_mismatch"
    assert run(machine, "MIGRATE_ANALYZE", expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest().upper())[0] == "analyzed"


def test_an_archive_that_leads_outside_itself_is_refused(node):
    machine, path, _ = node
    build(path, evil=True)
    with pytest.raises(HandlerError) as err:
        run(machine, "MIGRATE_ANALYZE")
    assert err.value.code == "unsafe_archive"


def test_a_missing_or_broken_archive_is_a_clear_error(node):
    machine, path, _ = node
    path.write_bytes(b"not a tarball")
    with pytest.raises(HandlerError) as err:
        run(machine, "MIGRATE_ANALYZE")
    assert err.value.code == "bad_archive"
    with pytest.raises(HandlerError) as err:
        run(machine, "MIGRATE_ANALYZE", archive="cpmove-nobody.tar.gz")
    assert err.value.code == "not_found"


def test_files_are_copied_links_and_junk_left_behind_and_existing_files_kept(node):
    machine, path, root = node
    docroot = root / "acmeuser" / "sites" / "acme.com" / "public_html"
    (docroot / "index.php").write_text("OLD")
    code, data = run(machine, "MIGRATE_IMPORT", part="files")
    assert code == "imported" and data["kept_existing"] == 1 and data["outside_public_html"] == 1
    assert (docroot / "index.php").read_text() == "OLD"  # not replaced
    assert (docroot / "wp-content" / "uploads" / "a.jpg").read_text() == "JPEGDATA"
    assert (root / "acmeuser" / "sites" / "acme.com" / "private" / "config.ini").exists()
    assert not (docroot / "passwd-link").exists() and not (root / "acmeuser" / "sites" / "acme.com" / ".cpanel").exists()
    run(machine, "MIGRATE_IMPORT", part="files", replace="yes")
    assert (docroot / "index.php").read_text() == "<?php echo 'hi';"


def test_databases_users_and_grants_are_imported_once(node):
    machine, _, _ = node
    code, data = run(machine, "MIGRATE_IMPORT", part="databases")
    assert code == "imported" and data["databases"] == ["acmeuser_shop"] and data["users"] == ["acmeuser_shop"]
    rows = machine.engine.dbs["acmeuser_shop"]["orders"]["rows"]
    assert rows == [[1, "Ann"], [2, "O'Neil"], [3, None]]
    assert machine.engine.users["acmeuser_shop"] == "*2470C0C06DEE42FD1618BB99005ADCA2EC9D1E19"  # the customer's password still works
    assert machine.engine.grants[("acmeuser_shop", "acmeuser_shop")] >= {"select", "insert"}
    again = run(machine, "MIGRATE_IMPORT", part="databases")[1]
    assert again["databases"] == [] and again["already_there"] == ["acmeuser_shop"]  # never replaced


def test_a_cpanel_name_is_mapped_to_the_native_account_prefix(node, tmp_path):
    machine, path, root = node
    machine.run("CREATE_WEBSITE", {"account": "newname", "domain": "other.com", "php_version": "8.1"})
    code, data = machine.run("MIGRATE_IMPORT", {"account": "newname", "archive": ARCHIVE, "part": "databases"})
    assert data["databases"] == ["newname_shop"] and "newname_shop" in machine.engine.users


def test_a_database_that_is_not_the_accounts_is_refused(node):
    machine, path, _ = node
    build(path, files={"mysql/otheruser_x.sql": "CREATE TABLE `t` (\n  `a` int\n);\n"})
    _, report = run(machine, "MIGRATE_ANALYZE")
    # it is renamed with the account prefix only when it starts with the cPanel user's name; this one does not, so the import refuses it
    with pytest.raises(HandlerError) as err:
        run(machine, "MIGRATE_IMPORT", part="databases")
    assert err.value.code in ("forbidden", "system_error")


def test_mail_is_imported_with_the_original_password_hashes(node):
    machine, _, root = node
    machine.run("MAIL_DOMAIN_ADD", {"account": "acmeuser", "domain": "acme.com"})
    code, data = run(machine, "MIGRATE_IMPORT", part="mail")
    assert code == "imported" and data["mailboxes"] == 1 and data["messages"] == 2 and data["forwarders"] == 1
    assert data["skipped"] == [] and machine.mail._state("acme.com")["mailboxes"]["info"]["hash"].startswith("$6$")
    assert any(f["source"] == "sales@acme.com" for f in machine.mail._state("acme.com")["forwarders"])
    assert run(machine, "MIGRATE_IMPORT", part="mail")[1]["mailboxes"] == 0  # repeating it adds nothing


def test_mail_for_a_domain_that_is_not_set_up_is_skipped_not_invented(node):
    machine, _, _ = node
    data = run(machine, "MIGRATE_IMPORT", part="mail")[1]
    assert data["mailboxes"] == 0 and "acme.com" in data["skipped"]


def test_validate_reports_what_is_missing_then_what_matches(node):
    machine, _, _ = node
    code, data = run(machine, "MIGRATE_VALIDATE")
    assert code == "differences" and data["files_missing"] == 3 and not data["databases"][0]["present"]
    run(machine, "MIGRATE_IMPORT", part="files")
    run(machine, "MIGRATE_IMPORT", part="databases")
    code, data = run(machine, "MIGRATE_VALIDATE")
    assert code == "validated" and data["ok"] and data["databases"][0]["tables"] == 1 and data["index"] == "index.php"


def test_discard_removes_the_archive_and_is_repeatable(node):
    machine, path, _ = node
    assert machine.run("MIGRATE_DISCARD", {"archive": ARCHIVE})[0] == "discarded" and not path.exists()
    assert machine.run("MIGRATE_DISCARD", {"archive": ARCHIVE})[0] == "absent"


def test_the_commands_validate_their_names():
    for bad in ("../x.tar.gz", "a/b.tar.gz", "a.zip", ".tar.gz"):
        with pytest.raises(catalog.InvalidCommand):
            catalog.validate("MIGRATE_DISCARD", {"archive": bad})
    with pytest.raises(catalog.InvalidCommand):
        catalog.validate("MIGRATE_IMPORT", {"account": "acmeuser", "archive": ARCHIVE, "part": "everything"})


def test_grant_parser_maps_privileges_and_reports_unknown_ones():
    hashes, grants, unsupported = parse_mysql_grants("GRANT USAGE ON *.* TO 'u'@'localhost' IDENTIFIED BY PASSWORD '*" + "A" * 40 + "';\n"
                                                      "GRANT SELECT, LOCK TABLES, EVENT ON `d\\_x`.* TO 'u'@'localhost';\n")
    assert hashes == {"u": "*" + "A" * 40} and grants == [("d_x", "u", ["select"])] and unsupported == ["EVENT", "LOCK TABLES"]


def test_archive_class_ignores_links_and_reads_small_files(node):
    _, path, _ = node
    archive = Archive(path)
    assert archive.skipped_links == 1 and "homedir/public_html/passwd-link" not in archive.files and archive.text("cp/acmeuser").startswith("DNS=")
    assert discover(archive)["main_domain"] == "acme.com"
