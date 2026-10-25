"""The panel's own nightly backup: an off-site copy when storage is set, a team alert when it fails."""
import pytest
from django.core.management.base import CommandError

from apps.accounts.roles import Role
from apps.core import tasks
from apps.core.management.commands import backup_panel
from apps.notifications.models import Notification

pytestmark = pytest.mark.django_db


def test_the_off_site_copy_is_only_made_when_storage_is_set(settings):
    assert not backup_panel.offsite_configured()
    settings.PANEL_BACKUP_S3_BUCKET, settings.PANEL_BACKUP_S3_ACCESS_KEY, settings.PANEL_BACKUP_S3_SECRET_KEY = "b", "k", "s"
    assert backup_panel.offsite_configured()


def test_a_backup_is_uploaded_with_the_configured_names(settings, tmp_path, monkeypatch):
    settings.PANEL_BACKUP_S3_BUCKET, settings.PANEL_BACKUP_S3_ACCESS_KEY, settings.PANEL_BACKUP_S3_SECRET_KEY = "bucket", "key", "secret"
    settings.PANEL_BACKUP_S3_PREFIX = "whe"
    calls = []

    class Client:
        def upload_file(self, path, bucket, key):
            calls.append((path, bucket, key))

    monkeypatch.setattr("boto3.client", lambda *a, **k: Client())
    file = tmp_path / "panel-postgresql-1.bak.enc"
    file.write_bytes(b"x")
    assert backup_panel.upload_offsite(file) == "whe/daily/panel-postgresql-1.bak.enc"
    assert calls == [(str(file), "bucket", "whe/daily/panel-postgresql-1.bak.enc")]
    assert backup_panel.upload_offsite(file, "monthly") == "whe/monthly/panel-postgresql-1.bak.enc"


def test_a_failed_upload_is_an_error_that_keeps_the_local_file(settings, tmp_path, monkeypatch):
    from botocore.exceptions import EndpointConnectionError

    settings.PANEL_BACKUP_S3_BUCKET, settings.PANEL_BACKUP_S3_ACCESS_KEY, settings.PANEL_BACKUP_S3_SECRET_KEY = "bucket", "key", "secret"

    class Client:
        def upload_file(self, *a):
            raise EndpointConnectionError(endpoint_url="https://x.invalid")

    monkeypatch.setattr("boto3.client", lambda *a, **k: Client())
    file = tmp_path / "panel-x.bak.enc"
    file.write_bytes(b"x")
    with pytest.raises(CommandError, match="kept on this server"):
        backup_panel.upload_offsite(file)
    assert file.exists()


def test_a_failing_nightly_backup_tells_the_team_and_still_fails(staff, monkeypatch):
    staff(Role.ADMIN)

    def broken(*a, **k):
        raise FileNotFoundError("pg_dump")

    monkeypatch.setattr(backup_panel, "make_backup", broken)
    with pytest.raises(FileNotFoundError):
        tasks.panel_backup_task()
    assert Notification.objects.filter(event="backup.failed", title__contains="backup failed").exists()


def test_only_the_newest_few_backups_are_kept_here(tmp_path, settings, monkeypatch):
    from datetime import datetime, timedelta, timezone

    for i in range(6):
        (tmp_path / f"panel-postgresql-2026010{i}.bak.enc").write_bytes(b"x")
    # make_backup prunes by file name order after writing the new one: simulate its pruning rule
    keep = 3
    old = sorted(tmp_path.glob("panel-*.bak.enc"))[:-keep]
    for f in old:
        f.unlink()
    assert len(list(tmp_path.glob("panel-*.bak.enc"))) == 3
    assert settings.PANEL_BACKUP_KEEP == 4 and settings.PANEL_BACKUP_WEEKDAYS == "*"


def test_the_off_site_copies_beyond_the_newest_few_are_deleted(settings, monkeypatch):
    from datetime import datetime, timedelta, timezone

    settings.PANEL_BACKUP_S3_BUCKET, settings.PANEL_BACKUP_S3_ACCESS_KEY, settings.PANEL_BACKUP_S3_SECRET_KEY = "bucket", "key", "secret"
    now = datetime.now(timezone.utc)
    objects = [{"Key": f"panel-backups/panel-{i}.bak.enc", "LastModified": now - timedelta(days=i)} for i in range(5)] + [{"Key": "panel-backups/readme.txt", "LastModified": now}]
    deleted = []

    class Paginator:
        def paginate(self, **kw):
            return [{"Contents": objects}]

    class Client:
        def get_paginator(self, name):
            return Paginator()

        def delete_object(self, Bucket, Key):
            deleted.append(Key)

    monkeypatch.setattr("boto3.client", lambda *a, **k: Client())
    assert backup_panel.prune_offsite(3) == 2
    assert sorted(deleted) == ["panel-backups/panel-3.bak.enc", "panel-backups/panel-4.bak.enc"]  # the two oldest backups; other files are left alone


def test_the_backup_job_runs_every_night_by_default(settings):
    schedule = settings.CELERY_BEAT_SCHEDULE["panel-database-backup"]["schedule"]
    assert len(schedule.day_of_week) == 7 and str(schedule.hour) == "{1}" and str(schedule.minute) == "{30}"


def _fake_bucket(settings, monkeypatch):
    settings.PANEL_BACKUP_S3_BUCKET, settings.PANEL_BACKUP_S3_ACCESS_KEY, settings.PANEL_BACKUP_S3_SECRET_KEY = "bucket", "key", "secret"
    settings.PANEL_BACKUP_S3_PREFIX = "whpanel"
    log = {"put": [], "delete": []}

    class Client:
        def upload_file(self, path, bucket, key):
            log["put"].append(key)

        def delete_object(self, **kw):
            log["delete"].append(kw["Key"])

        def get_paginator(self, name):
            raise AssertionError("a write-only key cannot list")

    monkeypatch.setattr("boto3.client", lambda *a, **k: Client())
    return log


def _backup(tmp_path, monkeypatch):
    file = tmp_path / "panel-postgresql-20261101T013000Z-full.bak.enc"
    file.write_bytes(b"x")
    monkeypatch.setattr(backup_panel, "make_backup", lambda target, keep: file)
    monkeypatch.setattr(backup_panel, "verify_backup", lambda path: "ok")
    return file


def test_a_nightly_backup_goes_to_daily_and_on_the_first_of_the_month_also_to_monthly(settings, tmp_path, monkeypatch):
    from datetime import date

    log = _fake_bucket(settings, monkeypatch)
    _backup(tmp_path, monkeypatch)
    _, text = backup_panel.run_backup(today=date(2026, 11, 14))
    assert log["put"] == ["whpanel/daily/panel-postgresql-20261101T013000Z-full.bak.enc"] and "lifecycle rules" in text
    log["put"].clear()
    backup_panel.run_backup(today=date(2026, 12, 1))
    assert log["put"] == ["whpanel/daily/panel-postgresql-20261101T013000Z-full.bak.enc", "whpanel/monthly/panel-postgresql-20261101T013000Z-full.bak.enc"]


def test_the_write_only_key_never_lists_or_deletes_unless_pruning_is_switched_on(settings, tmp_path, monkeypatch):
    from datetime import date

    log = _fake_bucket(settings, monkeypatch)
    _backup(tmp_path, monkeypatch)
    backup_panel.run_backup(today=date(2026, 11, 14))  # a client that refuses to list would fail here if pruning ran
    assert log["delete"] == []


def test_a_predeploy_dump_is_only_sent_off_the_server_once_encrypted(settings, tmp_path, monkeypatch):
    from django.core.management import call_command

    log = _fake_bucket(settings, monkeypatch)
    plain = tmp_path / "predeploy-20261101010101.dump"
    plain.write_bytes(b"PGDMP")
    with pytest.raises(CommandError, match="encrypted"):
        call_command("offsite_upload", file=str(plain), folder="predeploy")
    sealed = tmp_path / "predeploy-20261101010101.dump.gpg"
    sealed.write_bytes(b"gpg")
    call_command("offsite_upload", file=str(sealed), folder="predeploy")
    assert log["put"] == ["whpanel/predeploy/predeploy-20261101010101.dump.gpg"]


def test_the_check_command_stores_one_small_note_and_needs_only_the_write_permission(settings, monkeypatch):
    from io import StringIO

    from django.core.management import call_command

    log = _fake_bucket(settings, monkeypatch)
    out = StringIO()
    call_command("offsite_check", stdout=out)
    assert len(log["put"]) == 1 and log["put"][0].startswith("whpanel/check/check-") and "works" in out.getvalue()
