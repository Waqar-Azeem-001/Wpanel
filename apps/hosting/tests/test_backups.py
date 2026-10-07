"""Backup and restore (H9): the demo provider's rules (real archives, integrity, restore, retention, schedule) and the screen."""
import json
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from django.core.management import call_command

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.core.exceptions import ServiceError
from apps.hosting import control
from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.demo_backups import MAX_BACKUPS, DemoBackups
from apps.hosting.models import HostingAccount
from apps.products import services as product_services
from apps.products.models import ProductType

pytestmark = pytest.mark.django_db


def _real_database():
    import sqlite3
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "x.sqlite3"
        connection = sqlite3.connect(path)
        connection.execute("create table t (id integer)")
        connection.commit()
        connection.close()
        return path.read_bytes()


DB = _real_database()  # a real SQLite file: the trial restore opens every database it finds


@pytest.fixture
def box(tmp_path):
    site, state = tmp_path / "site", tmp_path / "state"
    (site / "public_html").mkdir(parents=True)
    (site / "public_html" / "index.html").write_text("original", encoding="utf-8")
    (site / ".trash").mkdir()
    (site / ".trash" / "gone.txt").write_text("trash", encoding="utf-8")
    (state / "data").mkdir(parents=True)
    (state / "data" / "shop.sqlite3").write_bytes(DB)
    (state / "databases.json").write_text(json.dumps({"databases": ["shop"]}), encoding="utf-8")
    (state / "email.json").write_text(json.dumps({"boxes": ["a"]}), encoding="utf-8")
    (state / "ssl.json").write_text(json.dumps({"certs": {}}), encoding="utf-8")
    return DemoBackups(state, site)


def names(box, backup_id):
    with zipfile.ZipFile(box.dir / f"{backup_id}.zip") as archive:
        return set(archive.namelist())


def test_each_kind_holds_exactly_its_own_parts_and_never_the_trash(box):
    assert names(box, box.create("files")) == {"MANIFEST.json", "files/public_html/index.html"}
    assert names(box, box.create("database")) == {"MANIFEST.json", "state/databases.json", "state/data/shop.sqlite3"}
    assert names(box, box.create("email")) == {"MANIFEST.json", "state/email.json"}
    full = names(box, box.create("full"))
    assert {"files/public_html/index.html", "state/databases.json", "state/email.json", "state/ssl.json"} <= full
    assert not any(".trash" in n for n in full)


def test_a_new_backup_is_listed_first_as_manual_and_not_yet_checked(box):
    first = box.create("files")
    second = box.create("full")
    rows = box.list()
    assert [r.backup_id for r in rows] == [second, first] and rows[0].source == "manual" and rows[0].verified == ""
    assert rows[0].size_mb >= 0
    with pytest.raises(HostingError, match="Choose what to back up"):
        box.create("everything")


def test_verify_marks_a_sound_backup_ok_and_a_changed_one_failed(box):
    good = box.create("files")
    assert box.verify(good).verified == "ok"
    bad = box.create("files")
    path = box.dir / f"{bad}.zip"
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(HostingError, match="changed since it was made"):
        box.verify(bad)
    assert {r.backup_id: r.verified for r in box.list()} == {good: "ok", bad: "failed"}
    path.unlink()
    with pytest.raises(HostingError, match="missing"):
        box.verify(bad)


def test_restore_puts_files_back_and_keeps_a_safety_copy(box):
    backup = box.create("files")
    (box.site / "public_html" / "index.html").write_text("changed", encoding="utf-8")
    (box.site / "public_html" / "extra.txt").write_text("new", encoding="utf-8")
    box.restore(backup)
    assert (box.site / "public_html" / "index.html").read_text() == "original"
    assert not (box.site / "public_html" / "extra.txt").exists() and (box.site / ".trash" / "gone.txt").exists()
    safety = [r for r in box.list() if r.source == "before restore"]
    assert len(safety) == 1 and "files/public_html/extra.txt" in names(box, safety[0].backup_id)


def test_restore_brings_back_databases_email_and_settings_only_as_the_kind_says(box):
    full = box.create("full")
    (box.state / "data" / "shop.sqlite3").write_bytes(b"db-changed")
    (box.state / "data" / "extra.sqlite3").write_bytes(b"extra")
    (box.state / "email.json").write_text("{}", encoding="utf-8")
    (box.state / "ssl.json").unlink()
    box.restore(full)
    assert (box.state / "data" / "shop.sqlite3").read_bytes() == DB and not (box.state / "data" / "extra.sqlite3").exists()
    assert json.loads((box.state / "email.json").read_text()) == {"boxes": ["a"]} and (box.state / "ssl.json").exists()
    only_db = box.create("database")
    (box.state / "email.json").write_text("{}", encoding="utf-8")
    (box.state / "data" / "shop.sqlite3").write_bytes(b"db-changed")
    box.restore(only_db)
    assert (box.state / "data" / "shop.sqlite3").read_bytes() == DB and (box.state / "email.json").read_text() == "{}"


def test_a_damaged_backup_is_never_restored_and_nothing_changes(box):
    backup = box.create("files")
    (box.site / "public_html" / "index.html").write_text("changed", encoding="utf-8")
    path = box.dir / f"{backup}.zip"
    path.write_bytes(b"not a zip at all")
    with pytest.raises(HostingError, match="nothing was changed"):
        box.restore(backup)
    assert (box.site / "public_html" / "index.html").read_text() == "changed"
    assert [r.source for r in box.list()] == ["manual"]  # no safety copy was made either


def test_a_hostile_archive_cannot_write_outside_the_account(box, tmp_path):
    backup = box.create("files")
    path = box.dir / f"{backup}.zip"
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("files/../../escaped.txt", "pwned")
    rows = json.loads(box.index_file.read_text())
    import hashlib

    rows[0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()  # even with a "matching" checksum the names are refused
    box.index_file.write_text(json.dumps(rows))
    with pytest.raises(HostingError):
        box.restore(backup)
    assert not (tmp_path / "escaped.txt").exists() and not (box.site.parent / "escaped.txt").exists()


def test_only_the_newest_backups_are_kept_and_one_can_be_deleted(box):
    ids = [box.create("files") for _ in range(MAX_BACKUPS + 2)]
    kept = [r.backup_id for r in box.list()]
    assert kept == list(reversed(ids))[:MAX_BACKUPS] and not (box.dir / f"{ids[0]}.zip").exists()
    box.delete(kept[0])
    assert kept[0] not in [r.backup_id for r in box.list()] and not (box.dir / f"{kept[0]}.zip").exists()
    with pytest.raises(HostingError, match="no longer exists"):
        box.delete(kept[0])


def test_a_schedule_takes_a_full_backup_when_due(box):
    assert box.list() == []
    box.set_schedule("daily")
    assert box.list() == []  # the first one is one day from now
    data = json.loads(box.schedule_file.read_text())
    data["last"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    box.schedule_file.write_text(json.dumps(data))
    [made] = box.list()
    assert made.kind == "full" and made.source == "scheduled" and len(box.list()) == 1  # not taken twice
    box.set_schedule("off")
    assert box.options().schedule == "off"
    with pytest.raises(HostingError):
        box.set_schedule("hourly")


def forge(box, backup_id, name, data):
    """Rewrite one member of a stored archive and keep its checksum 'matching', as a careless or hostile change would."""
    import hashlib

    path = box.dir / f"{backup_id}.zip"
    with zipfile.ZipFile(path) as old:
        members = {n: old.read(n) for n in old.namelist()}
    members[name] = data
    with zipfile.ZipFile(path, "w") as new:
        for n, d in members.items():
            new.writestr(n, d)
    rows = json.loads(box.index_file.read_text())
    next(r for r in rows if r["id"] == backup_id)["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    box.index_file.write_text(json.dumps(rows))


def test_the_test_is_a_real_trial_restore_not_just_a_checksum(box):
    backup = box.create("database")
    forge(box, backup, "state/databases.json", b"{ not json")
    with pytest.raises(HostingError, match="does not match what was saved"):
        box.verify(backup)
    other = box.create("files")
    forge(box, other, "files/public_html/index.html", b"swapped")
    with pytest.raises(HostingError, match="does not match what was saved"):
        box.verify(other)
    third = box.create("database")
    forge(box, third, "state/data/shop.sqlite3", b"this is not a database at all, just text that is long enough to be read")
    # the manifest still lists the original checksum, so the swapped database is caught either way
    with pytest.raises(HostingError):
        box.restore(third)
    assert (box.state / "data" / "shop.sqlite3").read_bytes() == DB


def test_a_real_sqlite_database_passes_and_a_damaged_one_fails_the_trial_restore(tmp_path):
    import sqlite3

    site, state = tmp_path / "site", tmp_path / "state"
    site.mkdir()
    (state / "data").mkdir(parents=True)
    connection = sqlite3.connect(state / "data" / "shop.sqlite3")
    connection.execute("create table t (id integer)")
    connection.commit()
    connection.close()
    box = DemoBackups(state, site)
    assert box.verify(box.create("database")).verified == "ok"


@pytest.fixture
def off(tmp_path, box):
    box.offsite = tmp_path / "away"
    box.set_offsite(True)
    return box


def test_an_off_server_copy_is_kept_checked_and_removed_with_the_backup(off):
    backup = off.create("files")
    assert (off.offsite / f"{backup}.zip").exists() and off.list()[0].offsite == "ok" and off.options().offsite
    assert off.verify(backup).offsite == "ok"
    off.delete(backup)
    assert not (off.offsite / f"{backup}.zip").exists()


def test_a_lost_local_copy_is_rebuilt_from_the_off_server_one(off):
    backup = off.create("files")
    (off.dir / f"{backup}.zip").unlink()
    (off.site / "public_html" / "index.html").write_text("changed", encoding="utf-8")
    off.restore(backup)
    assert (off.site / "public_html" / "index.html").read_text() == "original" and (off.dir / f"{backup}.zip").exists()


def test_a_lost_off_server_copy_is_reported_but_the_backup_still_works(off):
    backup = off.create("files")
    (off.offsite / f"{backup}.zip").unlink()
    assert off.verify(backup).offsite == "missing"
    (off.dir / f"{backup}.zip").unlink()
    with pytest.raises(HostingError, match="missing"):
        off.restore(backup)  # both copies are gone: nothing is changed


def test_off_server_storage_can_be_refused_when_the_provider_has_none(box):
    with pytest.raises(HostingError, match="not available"):
        box.set_offsite(True)
    assert box.create("files") and box.list()[0].offsite == ""


# --- The screen -------------------------------------------------------------------------------------------------------

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


def burl(account, tail=""):
    return f"/account/hosting/{account.pk}/manage/backups/{tail}"


def test_the_page_shows_sample_backups_with_their_check_state(client, owner, account):
    client.force_login(owner)
    page = client.get(burl(account)).content.decode()
    assert "Backups" in page and "Full account" in page and "Databases" in page and "Checked" in page and "Not checked yet" in page
    assert "2 / 7" in " ".join(page.split()) and "Every week" in page and "Back up now" in page
    assert "Off-server copy" in page and "Kept" in page and "Test restore" in page


def test_create_check_restore_and_delete_from_the_page(client, owner, account):
    client.force_login(owner)
    act = burl(account, "do/")
    client.post(act, {"action": "create", "kind": "files"})
    rows = control.perform(owner, account, "backups", "list_backups")
    new = next(r for r in rows if r.kind == "files")
    assert new.verified == ""
    client.post(act, {"action": "verify", "backup": new.backup_id})
    assert next(r for r in control.perform(owner, account, "backups", "list_backups") if r.backup_id == new.backup_id).verified == "ok"
    page = client.post(act, {"action": "restore", "backup": new.backup_id}, follow=True).content.decode()
    assert "was restored" in page and "Saved before a restore" in page
    client.post(act, {"action": "delete", "backup": new.backup_id})
    assert all(r.backup_id != new.backup_id for r in control.perform(owner, account, "backups", "list_backups"))


def test_the_off_server_switch_works_from_the_page(client, owner, account):
    client.force_login(owner)
    client.post(burl(account, "do/"), {"action": "offsite", "enabled": "0"})
    assert control.perform(owner, account, "backups", "backup_options").offsite is False
    client.post(burl(account, "do/"), {"action": "create", "kind": "files"})
    assert control.perform(owner, account, "backups", "list_backups")[0].offsite == ""
    client.post(burl(account, "do/"), {"action": "offsite", "enabled": "1"})
    client.post(burl(account, "do/"), {"action": "create", "kind": "files"})
    assert control.perform(owner, account, "backups", "list_backups")[0].offsite == "ok"


def test_refusals_and_unknown_actions_are_shown_as_messages(client, owner, account):
    client.force_login(owner)
    act = burl(account, "do/")
    assert "Choose what to back up" in client.post(act, {"action": "create", "kind": "x"}, follow=True).content.decode()
    assert "no longer exists" in client.post(act, {"action": "restore", "backup": "nope"}, follow=True).content.decode()
    assert "Choose off, every day or every week" in client.post(act, {"action": "schedule", "schedule": "x"}, follow=True).content.decode()
    assert "That action is not available" in client.post(act, {"action": "wipe"}, follow=True).content.decode()


def test_restore_and_schedule_are_audited(client, owner, account):
    client.force_login(owner)
    backup = control.perform(owner, account, "backups", "list_backups")[0]
    client.post(burl(account, "do/"), {"action": "restore", "backup": backup.backup_id})
    client.post(burl(account, "do/"), {"action": "schedule", "schedule": "daily"})
    events = list(AuditEvent.objects.filter(action="hosting.control").values_list("metadata", flat=True))
    assert any(e.get("operation") == "restore_backup" and e.get("backup") == backup.backup_id for e in events)
    assert any(e.get("operation") == "set_backup_schedule" and e.get("schedule") == "daily" for e in events)


def test_strangers_get_nothing_and_a_provider_without_backups_shows_the_not_available_page(client, customer, owner, account):
    client.force_login(customer)
    assert client.get(burl(account)).status_code == 404
    assert client.post(burl(account, "do/"), {"action": "restore", "backup": "x"}).status_code == 404
    from apps.products.models import Server

    Server.objects.filter(kind="demo").update(kind="manual")
    client.force_login(owner)
    assert "not available for your hosting server yet" in client.get(burl(account)).content.decode()
    with pytest.raises(ServiceError):
        control.perform(owner, account, "backups", "list_backups")
