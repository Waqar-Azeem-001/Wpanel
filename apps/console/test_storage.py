"""Storage and backups: sizes, backups (make, download, delete, restore), customer documents."""
import pytest
from django.core.files.base import ContentFile
from django.urls import reverse

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.console import storage

pytestmark = pytest.mark.django_db

NAME = "panel-sqlite-20261017T010000Z.bak.enc"


@pytest.fixture(autouse=True)
def folders(settings, tmp_path):
    settings.PANEL_BACKUP_DIR = str(tmp_path / "panel")
    settings.HOST_BACKUP_DIR = str(tmp_path / "host")
    settings.PRIVATE_MEDIA_ROOT = str(tmp_path / "private")
    for d in ("panel", "host", "private"):
        (tmp_path / d).mkdir()
    return tmp_path


def browser(client, user):
    client.force_login(user)
    return client


def close_download(response):
    """Release the file a download holds open (Windows will not delete it otherwise). Closing a response outside the request fires ``request_finished``, which would close the database connection the test is still using on PostgreSQL."""
    from django.core.signals import request_finished
    from django.db import close_old_connections

    request_finished.disconnect(close_old_connections)
    try:
        response.close()
    finally:
        request_finished.connect(close_old_connections)


def test_the_page_shows_sizes_backups_and_documents_to_a_manager_but_dumps_only_to_a_super_admin(client, staff, folders):
    (folders / "panel" / NAME).write_bytes(b"x" * 2048)
    (folders / "host" / "whpanel-before-test-20261017-1000.sql.gz").write_bytes(b"y" * 100)
    page = browser(client, staff(Role.ADMIN)).get(reverse("console:storage")).content.decode()
    assert "Storage and backups" in page and NAME in page and "whpanel-before-test" not in page and "Restore" not in page
    admin = staff(Role.SUPER_ADMIN)
    page = browser(client, admin).get(reverse("console:storage")).content.decode()
    assert "whpanel-before-test" in page and "Restore" in page


def test_a_person_without_the_settings_permission_cannot_open_it(client, staff):
    assert browser(client, staff(Role.SUPPORT_AGENT)).get(reverse("console:storage")).status_code == 403


def test_a_backup_can_be_downloaded_and_deleted_and_only_names_in_the_pattern_are_touched(client, staff, folders):
    (folders / "panel" / NAME).write_bytes(b"data")
    (folders / "panel" / "notes.txt").write_text("keep me")
    admin = browser(client, staff(Role.ADMIN))
    got = admin.get(reverse("console:storage_backup_download", args=["panel", NAME]))
    assert got.status_code == 200 and b"".join(got.streaming_content) == b"data" and "attachment" in got["Content-Disposition"]
    close_download(got)
    assert admin.get(reverse("console:storage_backup_download", args=["panel", "notes.txt"])).status_code == 404
    assert admin.get(reverse("console:storage_backup_download", args=["panel", "..%2f..%2fetc%2fpasswd"])).status_code == 404
    admin.post(reverse("console:storage_backups_delete"), {"backups": [f"panel:{NAME}", "panel:notes.txt", "panel:../x"]})
    assert not (folders / "panel" / NAME).exists() and (folders / "panel" / "notes.txt").exists()
    assert AuditEvent.objects.filter(action="storage.backup_deleted").count() == 1


def test_the_plain_dumps_are_for_a_super_admin_only(client, staff, folders):
    dump = folders / "host" / "whpanel-before-x-20261017-1000.sql.gz"
    dump.write_bytes(b"sql")
    admin = browser(client, staff(Role.ADMIN))
    assert admin.get(reverse("console:storage_backup_download", args=["host", dump.name])).status_code == 404
    admin.post(reverse("console:storage_backups_delete"), {"backups": [f"host:{dump.name}"]})
    assert dump.exists()
    boss = browser(client, staff(Role.SUPER_ADMIN))
    response = boss.get(reverse("console:storage_backup_download", args=["host", dump.name]))
    assert response.status_code == 200
    close_download(response)  # release the file (Windows will not delete an open one)
    boss.post(reverse("console:storage_backups_delete"), {"backups": [f"host:{dump.name}"]})
    assert not dump.exists()


PNG = b"\x89PNG\r\n\x1a\n" + bytes(40)


def make_documents(staff_user, owner_client):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.support import services as support
    from apps.support.models import Department, TicketAttachment

    ticket = support.open_ticket(owner_client.contacts.get().user, owner_client, department=Department.objects.first(), subject="Help", body="See files",
                                 files=[SimpleUploadedFile("a.png", PNG), SimpleUploadedFile("b.png", PNG)])
    return list(TicketAttachment.objects.filter(message__ticket=ticket))


def test_documents_are_deleted_single_or_bulk_and_their_records_stay(client, staff, folders):
    from apps.clients import services as client_services

    admin = staff(Role.ADMIN)
    owner_client = client_services.create_client(admin, {"first_name": "Ada", "email": "ada@acme.test"})
    first, second = make_documents(admin, owner_client)
    assert first.file and second.file and (folders / "private").exists()
    page = browser(client, admin).get(reverse("console:storage")).content.decode()
    assert "a.png" in page and "b.png" in page
    client.post(reverse("console:storage_documents_delete"), {"documents": [f"attachment:{first.pk}"]})
    first.refresh_from_db(); second.refresh_from_db()
    assert not first.file and first.original_name == "a.png" and second.file  # single: the file is gone, the record stays
    client.post(reverse("console:storage_documents_delete"), {"documents": [f"attachment:{second.pk}", "attachment:99999", "garbage", "proof:x"]})
    second.refresh_from_db()
    assert not second.file  # bulk, and nonsense in the list is ignored
    assert AuditEvent.objects.filter(action="storage.document_deleted").count() == 2


def test_files_nothing_points_to_are_listed_and_only_those_can_be_deleted(client, staff, folders):
    (folders / "private" / "support").mkdir()
    stray = folders / "private" / "support" / "stray.bin"
    stray.write_bytes(b"1234")
    (folders / "outside.txt").write_text("not ours")
    admin = browser(client, staff(Role.ADMIN))
    assert "support/stray.bin" in admin.get(reverse("console:storage")).content.decode()
    admin.post(reverse("console:storage_unreferenced_delete"), {"files": ["support/stray.bin", "../outside.txt", "/etc/passwd"]})
    assert not stray.exists() and (folders / "outside.txt").exists()


def test_a_backup_is_made_from_the_page(client, staff, folders, monkeypatch):
    from apps.core.management.commands import backup_panel

    made = folders / "panel" / NAME
    monkeypatch.setattr(backup_panel, "run_backup", lambda keep=None: (made, "no off-site storage set"))
    made.write_bytes(b"x")
    page = browser(client, staff(Role.ADMIN)).post(reverse("console:storage_backup_now"), follow=True).content.decode()
    assert "Backup made" in page and AuditEvent.objects.filter(action="storage.backup_made").exists()


def test_restore_needs_a_super_admin_the_typed_word_and_a_real_backup(client, staff, folders, monkeypatch):
    started = []
    monkeypatch.setattr("apps.core.tasks.restore_panel_task.delay", lambda name, actor: started.append((name, actor)))
    (folders / "panel" / NAME.replace("sqlite", "postgresql")).write_bytes(b"x")
    pg = NAME.replace("sqlite", "postgresql")
    admin = browser(client, staff(Role.ADMIN))
    admin.post(reverse("console:storage_restore"), {"name": pg, "confirm": "RESTORE"})
    assert started == []  # an admin may not
    boss = browser(client, staff(Role.SUPER_ADMIN))
    boss.post(reverse("console:storage_restore"), {"name": pg, "confirm": "restore please"})
    boss.post(reverse("console:storage_restore"), {"name": "../../etc/passwd", "confirm": "RESTORE"})
    boss.post(reverse("console:storage_restore"), {"name": "panel-postgresql-20200101T000000Z.bak.enc", "confirm": "RESTORE"})
    assert started == []  # wrong word, a bad name, a file that does not exist
    boss.post(reverse("console:storage_restore"), {"name": pg, "confirm": "RESTORE"})
    assert started == [(pg, boss.session["_auth_user_id"] and started[0][1])] and started[0][0] == pg
    assert AuditEvent.objects.filter(action="storage.restore_requested").exists()


def test_the_restore_command_makes_a_safety_copy_runs_one_transaction_and_migrates(folders, monkeypatch):
    import subprocess
    from types import SimpleNamespace

    from apps.core import crypto
    from apps.core.management.commands import backup_panel

    backup = folders / "panel" / NAME.replace("sqlite", "postgresql")
    backup.write_bytes(crypto.encrypt_bytes(b"PGDMP-fake"))
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="x", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(backup_panel, "connection", SimpleNamespace(vendor="postgresql", settings_dict={"HOST": "db", "PORT": "5432", "USER": "u", "NAME": "n", "PASSWORD": "p"}, close=lambda: None))
    made = []
    monkeypatch.setattr(backup_panel, "make_backup", lambda target, keep: made.append(keep) or (folders / "panel" / "panel-postgresql-20261017T020000Z.bak.enc"))
    migrated = []
    monkeypatch.setattr("django.core.management.call_command", lambda *a, **k: migrated.append(a))
    text = backup_panel.restore_backup(backup)
    restore = [c for c in calls if c[0] == "pg_restore" and "--single-transaction" in c][0]
    assert "--clean" in restore and "--if-exists" in restore and restore[-2:][0] != "" and made == [0] and migrated == [("migrate",)]
    assert "saved as panel-postgresql-20261017T020000Z.bak.enc" in text


def test_a_failed_restore_changes_nothing_and_says_so(folders, monkeypatch):
    import subprocess
    from types import SimpleNamespace

    from django.core.management.base import CommandError

    from apps.core import crypto
    from apps.core.management.commands import backup_panel

    backup = folders / "panel" / NAME.replace("sqlite", "postgresql")
    backup.write_bytes(crypto.encrypt_bytes(b"PGDMP-fake"))

    def fake_run(args, **kw):
        return SimpleNamespace(returncode=1 if "--single-transaction" in args else 0, stdout="x", stderr="boom: relation exists")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(backup_panel, "connection", SimpleNamespace(vendor="postgresql", settings_dict={"HOST": "db", "USER": "u", "NAME": "n", "PASSWORD": "p"}, close=lambda: None))
    monkeypatch.setattr(backup_panel, "make_backup", lambda target, keep: folders / "panel" / "safety.bak.enc")
    with pytest.raises(CommandError, match="changed nothing"):
        backup_panel.restore_backup(backup)
