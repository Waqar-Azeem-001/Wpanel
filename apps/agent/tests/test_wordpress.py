"""Installing WordPress on a node: everything automatic (database, user, checked downloads, wp-config, administrator), safe on failure, and never leaking a password."""
import hashlib
import re

import pytest

from apps.agent import catalog, wordpress_fixture as fixture
from apps.agent.node.website import HandlerError
from apps.agent.reference_agent import NativeMachine

SITE = {"account": "abcd1234", "domain": "acme.com", "php_version": "8.3", "databases": 3}
P = {"account": "abcd1234", "domain": "acme.com", "site_title": "Acme Shop", "admin_user": "siteadmin", "admin_email": "owner@gmail.example.com", "admin_password": "Kq7vRt2mXzLp-91"}


def made(tmp_path, **fetch_options):
    machine = NativeMachine(files_dir=tmp_path, capabilities=("web", "database"), wordpress_fetch=fixture.make_fetch(**fetch_options) if fetch_options else None)
    machine.run("CREATE_WEBSITE", SITE)
    docroot = tmp_path / "abcd1234" / "sites" / "acme.com" / "public_html"
    docroot.mkdir(parents=True, exist_ok=True)
    (docroot / "index.html").write_text("placeholder")
    return machine, docroot


def test_everything_is_done_for_the_customer(tmp_path):
    machine, docroot = made(tmp_path)
    code, data = machine.run("INSTALL_WORDPRESS", P)
    assert code == "installed" and data["version"] == fixture.VERSION and data["admin_url"] == "http://acme.com/wp-admin/" and data["database"] == "abcd1234_wp"
    assert (docroot / "wp-includes" / "version.php").exists() and (docroot / "index.php").exists() and not (docroot / "index.html").exists()
    config = (docroot / "wp-config.php").read_text()
    assert "define( 'DB_NAME', 'abcd1234_wp' );" in config and "define( 'DB_USER', 'abcd1234_wp' );" in config
    assert len(re.findall(r"define\( '[A-Z_]+(KEY|SALT)', '\w{64}' \);", config)) == 8
    engine = machine.engine
    assert "abcd1234_wp" in engine.dbs and "abcd1234_wp" in engine.users and ("abcd1234_wp", "abcd1234_wp") in engine.grants


def test_the_downloads_are_checked_before_anything_is_changed(tmp_path):
    machine, docroot = made(tmp_path, tamper=True)
    with pytest.raises(HandlerError) as err:
        machine.run("INSTALL_WORDPRESS", P)
    assert err.value.code == "checksum_mismatch"
    assert not machine.engine.dbs and [e.name for e in docroot.iterdir()] == ["index.html"]  # not a database, not a file


def test_a_network_failure_leaves_the_account_as_it_was(tmp_path):
    machine, docroot = made(tmp_path, down=True)
    with pytest.raises(HandlerError) as err:
        machine.run("INSTALL_WORDPRESS", P)
    assert err.value.code == "download_failed" and not machine.engine.dbs and [e.name for e in docroot.iterdir()] == ["index.html"]


def test_a_failure_after_the_database_was_made_removes_it_and_the_files(tmp_path):
    machine, docroot = made(tmp_path)
    machine.system.fail_on = None
    original = machine.system.run

    def failing(argv, input=None):
        if argv[:1] == ["systemd-run"]:
            from apps.agent.node.system import SystemError_

            raise SystemError_("wp-cli failed")
        return original(argv, input)

    machine.system.run = failing
    with pytest.raises(HandlerError) as err:
        machine.run("INSTALL_WORDPRESS", P)
    assert err.value.code == "system_error"
    assert not machine.engine.dbs and not machine.engine.users  # the database and the user are gone again
    assert sorted(e.name for e in docroot.iterdir()) == ["index.html"]  # and so is everything that was unpacked


def test_it_never_installs_over_someone_elses_files(tmp_path):
    machine, docroot = made(tmp_path)
    (docroot / "my-site.php").write_text("<?php // mine")
    with pytest.raises(HandlerError) as err:
        machine.run("INSTALL_WORDPRESS", P)
    assert err.value.code == "not_empty" and (docroot / "my-site.php").read_text() == "<?php // mine" and not machine.engine.dbs


def test_a_second_run_is_recognised_and_changes_nothing(tmp_path):
    machine, docroot = made(tmp_path)
    machine.run("INSTALL_WORDPRESS", P)
    before = (docroot / "wp-config.php").read_text()
    code, data = machine.run("INSTALL_WORDPRESS", P)
    assert code == "exists" and data["version"] == fixture.VERSION and (docroot / "wp-config.php").read_text() == before
    assert list(machine.engine.dbs) == ["abcd1234_wp"]


def test_the_installer_is_started_as_the_account_and_the_password_is_not_in_any_command(tmp_path):
    machine, _ = made(tmp_path)
    machine.run("INSTALL_WORDPRESS", P)
    run = next(c for c in machine.system.commands if c[:1] == ["systemd-run"])
    assert run[run.index("--uid") + 1] == "abcd1234" and "whp-abcd1234.slice" in run and "--prompt=admin_password" in run and "--skip-email" in run
    everything = repr(machine.system.commands)
    assert P["admin_password"] not in everything  # sent on standard input only
    config = (tmp_path / "abcd1234" / "sites" / "acme.com" / "public_html" / "wp-config.php").read_text()
    password = re.search(r"DB_PASSWORD', '(\w+)'", config).group(1)
    assert password not in everything  # the database password is only in the config file


def test_a_hostile_archive_cannot_write_outside_the_website(tmp_path):
    machine = NativeMachine(files_dir=tmp_path, wordpress_fetch=fixture.make_fetch(archive=fixture.archive_bytes(evil=True)))
    machine.run("CREATE_WEBSITE", SITE)
    (tmp_path / "abcd1234" / "sites" / "acme.com" / "public_html").mkdir(parents=True, exist_ok=True)
    with pytest.raises(HandlerError) as err:
        machine.run("INSTALL_WORDPRESS", P)
    assert err.value.code == "unsafe_archive" and not machine.engine.dbs
    assert not list(tmp_path.rglob("escape.php"))


def test_the_database_limit_of_the_plan_applies(tmp_path):
    machine = NativeMachine(files_dir=tmp_path)
    machine.run("CREATE_WEBSITE", {**SITE, "databases": 0})
    (tmp_path / "abcd1234" / "sites" / "acme.com" / "public_html").mkdir(parents=True, exist_ok=True)
    with pytest.raises(HandlerError) as err:
        machine.run("INSTALL_WORDPRESS", P)
    assert err.value.code == "limit_reached"


def test_the_inputs_are_validated_and_the_password_is_hidden_when_stored():
    for bad in ({"site_title": "<script>"}, {"admin_user": "a b"}, {"admin_email": "not-an-email"}, {"admin_password": "short"}, {"domain": "a.com;id"}):
        with pytest.raises(catalog.InvalidCommand):
            catalog.validate("INSTALL_WORDPRESS", {**P, **bad})
    assert catalog.scrub("INSTALL_WORDPRESS", P)["admin_password"] == "***"
    assert not catalog.COMMANDS["INSTALL_WORDPRESS"].idempotent and hashlib.sha1(b"").hexdigest()
