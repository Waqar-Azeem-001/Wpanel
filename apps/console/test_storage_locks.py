"""Back up now and Restore from the Storage page when they fail, are clicked twice, or run while another is running."""
import pytest
from django.core.cache import cache
from django.urls import reverse

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.console import storage
from apps.core.exceptions import ServiceError
from apps.core.locks import single_flight
from apps.core.management.commands import backup_panel

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def folders(settings, tmp_path):
    settings.PANEL_BACKUP_DIR = str(tmp_path / "panel")
    settings.HOST_BACKUP_DIR = str(tmp_path / "host")
    for d in ("panel", "host"):
        (tmp_path / d).mkdir()
    return tmp_path


def test_a_failing_backup_is_reported_as_one_clear_message_and_nothing_is_recorded(staff, monkeypatch):
    def broken(*args, **kwargs):
        raise FileNotFoundError("pg_dump is missing")

    monkeypatch.setattr(backup_panel, "run_backup", broken)
    with pytest.raises(ServiceError) as failed:
        storage.backup_now(staff(Role.ADMIN))
    assert failed.value.code == "backup_failed" and "pg_dump is missing" in failed.value.message
    assert not AuditEvent.objects.filter(action="storage.backup_made").exists()


def test_back_up_now_is_refused_while_a_backup_is_already_running(staff, monkeypatch):
    ran = []
    monkeypatch.setattr(backup_panel, "run_backup", lambda: ran.append(1))
    with single_flight("panel-backup"):  # the nightly job (or a first click) holds it
        with pytest.raises(ServiceError) as busy:
            storage.backup_now(staff(Role.ADMIN))
    assert busy.value.code == "in_progress" and ran == []


def test_a_restore_that_is_already_queued_is_not_queued_again(client, staff, folders, monkeypatch):
    name = "panel-postgresql-20261101T013000Z-full.bak.enc"
    (folders / "panel" / name).write_bytes(b"x")
    queued = []
    monkeypatch.setattr("apps.core.tasks.restore_panel_task.delay", lambda backup, actor: queued.append(backup))
    cache.delete("panel-restore-queued")
    client.force_login(staff(Role.SUPER_ADMIN))
    first = {"name": name, "confirm": "RESTORE"}
    client.post(reverse("console:storage_restore"), first)
    client.post(reverse("console:storage_restore"), first)  # the double click
    assert queued == [name]


def test_the_restore_job_lets_the_page_start_another_afterwards_and_refuses_to_overlap(staff, folders, monkeypatch):
    from apps.core import tasks

    (folders / "panel").mkdir(exist_ok=True)
    cache.set("panel-restore-queued", 1, 300)
    monkeypatch.setattr(backup_panel, "restore_backup", lambda path: "restored")
    staff(Role.SUPER_ADMIN)
    assert tasks.restore_panel_task("panel-postgresql-20261101T013000Z-full.bak.enc", None) == "restored"
    assert cache.get("panel-restore-queued") is None  # free for the next one
    with single_flight("panel-restore", wait=0):
        with pytest.raises(ServiceError):
            tasks.restore_panel_task("panel-postgresql-20261101T013000Z-full.bak.enc", None)
    assert cache.get("panel-restore-queued") is None  # a failed run frees it too
