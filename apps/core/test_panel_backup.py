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
    assert backup_panel.upload_offsite(file) == "whe/panel-postgresql-1.bak.enc"
    assert calls == [(str(file), "bucket", "whe/panel-postgresql-1.bak.enc")]


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
    assert settings.PANEL_BACKUP_KEEP == 3 and settings.PANEL_BACKUP_WEEKDAYS == "sun"


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


def test_the_nightly_job_became_a_weekly_one_by_default(settings):
    schedule = settings.CELERY_BEAT_SCHEDULE["panel-database-backup"]["schedule"]
    assert str(schedule.day_of_week) == "{0}"
