"""Storage page, second round: full backups (database and uploaded files), restore from a file, bulk public media, and what a restore does with files."""
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import CommandError
from django.urls import reverse

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.core import crypto
from apps.core.management.commands import backup_panel

pytestmark = pytest.mark.django_db

OLD = "panel-sqlite-20261017T010000Z.bak.enc"


@pytest.fixture(autouse=True)
def folders(settings, tmp_path):
    settings.PANEL_BACKUP_DIR = str(tmp_path / "panel")
    settings.HOST_BACKUP_DIR = str(tmp_path / "host")
    settings.PRIVATE_MEDIA_ROOT = str(tmp_path / "private")
    settings.MEDIA_ROOT = str(tmp_path / "media")
    for d in ("panel", "host", "private", "media"):
        (tmp_path / d).mkdir()
    return tmp_path


def browser(client, user):
    client.force_login(user)
    return client


def test_a_backup_of_another_kind_of_database_is_refused_before_anything_changes(folders, monkeypatch):
    backup = folders / "panel" / OLD.replace("sqlite", "postgresql")
    backup.write_bytes(crypto.encrypt_bytes(b"PGDMP-fake"))
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: SimpleNamespace(returncode=0, stdout="x", stderr=""))
    monkeypatch.setattr(backup_panel, "make_backup", lambda target, keep: folders / "panel" / "safety.bak.enc")
    with pytest.raises(CommandError, match="uses sqlite"):
        backup_panel.restore_backup(backup)


def test_a_full_backup_holds_the_database_and_the_uploaded_files_and_a_restore_puts_both_back(folders, monkeypatch):
    live = folders / "live.sqlite3"
    db = sqlite3.connect(live)
    db.execute("create table notes (text)")
    db.execute("insert into notes values ('before')")
    db.commit()
    db.close()
    monkeypatch.setattr(backup_panel, "connection", SimpleNamespace(vendor="sqlite", settings_dict={"NAME": str(live)}, close=lambda: None))
    monkeypatch.setattr("django.core.management.call_command", lambda *a, **k: None)
    (folders / "private" / "support").mkdir()
    (folders / "private" / "support" / "doc.txt").write_text("original document")
    (folders / "media" / "logo.png").write_bytes(b"logo-v1")
    made = backup_panel.make_backup(folders / "panel", keep=4)
    assert made.name.endswith("-full.bak.enc") and made.read_bytes().startswith(b"WHPB1")  # encrypted, in pieces
    assert b"original document" not in made.read_bytes() and b"before" not in made.read_bytes()
    described = backup_panel.verify_backup(made)
    assert "2 uploaded file(s)" in described and "integrity ok" in described
    # the world moves on
    db = sqlite3.connect(live)
    db.execute("delete from notes")
    db.execute("insert into notes values ('after')")
    db.commit()
    db.close()
    (folders / "private" / "support" / "doc.txt").write_text("changed")
    (folders / "private" / "support" / "newer.txt").write_text("added after the backup")
    text = backup_panel.restore_backup(made)
    check = sqlite3.connect(live)
    assert [row[0] for row in check.execute("select text from notes")] == ["before"]
    check.close()
    assert (folders / "private" / "support" / "doc.txt").read_text() == "original document"
    assert (folders / "private" / "support" / "newer.txt").exists()  # a file newer than the backup is not deleted
    assert "2 file(s) put back" in text and "saved as panel-sqlite-" in text
    assert len(list((folders / "panel").glob("panel-sqlite-*-full.bak.enc"))) >= 1


def test_the_oldest_full_backups_beyond_the_number_to_keep_are_deleted(folders, monkeypatch):
    live = folders / "live.sqlite3"
    sqlite3.connect(live).close()
    monkeypatch.setattr(backup_panel, "connection", SimpleNamespace(vendor="sqlite", settings_dict={"NAME": str(live)}, close=lambda: None))
    from datetime import datetime, timedelta, timezone

    class Clock:
        n = 0

    def tick():
        Clock.n += 1
        return datetime(2026, 10, 17, 1, 0, Clock.n, tzinfo=timezone.utc)

    monkeypatch.setattr(backup_panel.timezone, "now", tick)
    made = [backup_panel.make_backup(folders / "panel", keep=4) for _ in range(6)]
    left = sorted(p.name for p in (folders / "panel").glob("panel-*.bak.enc"))
    assert len(left) == 4 and made[-1].name in left and made[0].name not in left


def test_big_file_encryption_round_trips_reads_older_single_block_files_and_refuses_a_wrong_key(tmp_path, settings):
    src = tmp_path / "src.bin"
    src.write_bytes(b"abc" * 1000)
    enc, out = tmp_path / "enc", tmp_path / "out"
    crypto.encrypt_file(src, enc)
    crypto.decrypt_file(enc, out)
    assert out.read_bytes() == src.read_bytes()
    old = tmp_path / "old"
    old.write_bytes(crypto.encrypt_bytes(b"older backup"))
    crypto.decrypt_file(old, out)
    assert out.read_bytes() == b"older backup"
    settings.CREDENTIALS_ENCRYPTION_KEY = "Zm9yLXRlc3Rpbmctb25seS1mb3ItdGVzdGluZy1vbmx5LTMy"
    with pytest.raises(ValueError):
        crypto.decrypt_file(enc, out)


def test_public_media_files_are_listed_and_deleted_single_or_bulk_and_nothing_else_can_be(client, staff, folders):
    (folders / "media" / "sub").mkdir()
    for name in ("a.png", "b.png", "sub/c.png"):
        (folders / "media" / name).write_bytes(b"x")
    (folders / "secret.txt").write_text("outside")
    admin = browser(client, staff(Role.ADMIN))
    page = admin.get(reverse("console:storage")).content.decode()
    assert "a.png" in page and "sub/c.png" in page and 'name="media"' in page and "data-select-all" in page
    admin.post(reverse("console:storage_media_delete"), {"media": ["a.png"]})
    assert not (folders / "media" / "a.png").exists() and (folders / "media" / "b.png").exists()
    admin.post(reverse("console:storage_media_delete"), {"media": ["b.png", "sub/c.png", "../secret.txt", "/etc/passwd"]})
    assert not (folders / "media" / "b.png").exists() and not (folders / "media" / "sub" / "c.png").exists() and (folders / "secret.txt").exists()
    assert AuditEvent.objects.filter(action="storage.media_deleted").count() == 3


def test_a_backup_file_can_be_uploaded_and_restored_by_a_super_admin_only(client, staff, folders, monkeypatch):
    started = []
    monkeypatch.setattr("apps.core.tasks.restore_panel_task.delay", lambda name, actor: started.append(name))
    monkeypatch.setattr(backup_panel, "verify_backup", lambda path: "ok")
    good = "panel-postgresql-20260101T000000Z-full.bak.enc"
    admin = browser(client, staff(Role.ADMIN))
    admin.post(reverse("console:storage_restore_upload"), {"backup": SimpleUploadedFile(good, b"x"), "confirm": "RESTORE"})
    assert started == [] and not (folders / "panel" / good).exists()
    boss = browser(client, staff(Role.SUPER_ADMIN))
    boss.post(reverse("console:storage_restore_upload"), {"backup": SimpleUploadedFile("evil.sh", b"x"), "confirm": "RESTORE"})
    boss.post(reverse("console:storage_restore_upload"), {"backup": SimpleUploadedFile(good, b"x"), "confirm": "no"})
    assert started == []  # a wrong name, a missing confirmation
    boss.post(reverse("console:storage_restore_upload"), {"backup": SimpleUploadedFile(good, b"x"), "confirm": "RESTORE"})
    assert started == [good] and (folders / "panel" / good).exists()


def test_an_uploaded_file_that_cannot_be_read_is_removed_again(client, staff, folders):
    bad = "panel-postgresql-20260101T000000Z-full.bak.enc"
    boss = browser(client, staff(Role.SUPER_ADMIN))
    boss.post(reverse("console:storage_restore_upload"), {"backup": SimpleUploadedFile(bad, b"not encrypted at all"), "confirm": "RESTORE"})
    assert not (folders / "panel" / bad).exists()


def test_the_page_says_what_each_backup_holds_and_offers_select_all_and_restore(client, staff, folders):
    (folders / "panel" / "panel-sqlite-20261017T010000Z-full.bak.enc").write_bytes(b"x")
    (folders / "panel" / OLD).write_bytes(b"x")
    page = browser(client, staff(Role.SUPER_ADMIN)).get(reverse("console:storage")).content.decode()
    assert "Database and uploaded files" in page and "Database only (an older backup)" in page and 'form="bk-del"' in page
    assert "Upload and restore" in page and page.count("Restore…") == 2
    manager = browser(client, staff(Role.ADMIN)).get(reverse("console:storage")).content.decode()
    assert "Restoring a backup needs a Super Admin" in manager


def test_the_sign_in_page_has_no_help_articles_block(client):
    page = client.get(reverse("accounts:login")).content.decode()
    assert "From our help centre" not in page and "auth-news" not in page
