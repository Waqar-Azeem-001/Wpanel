"""The off-site commands (fetch, status refresh) and the backup command's own output."""
import sqlite3
from datetime import datetime, timedelta, timezone
from io import StringIO
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.core.management.commands import backup_panel

pytestmark = pytest.mark.django_db


def bucket(settings, monkeypatch, objects=()):
    settings.PANEL_BACKUP_S3_BUCKET, settings.PANEL_BACKUP_S3_ACCESS_KEY, settings.PANEL_BACKUP_S3_SECRET_KEY = "bucket", "readkey", "secret"
    settings.PANEL_BACKUP_S3_PREFIX = "whpanel"
    log = {"downloaded": [], "listed": [], "put": []}
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    contents = [{"Key": key, "LastModified": now + timedelta(days=age)} for key, age in objects]

    class Paginator:
        def paginate(self, Bucket, Prefix):
            log["listed"].append(Prefix)
            yield {"Contents": [o for o in contents if o["Key"].startswith(Prefix)]}

    class Client:
        def get_paginator(self, name):
            return Paginator()

        def download_file(self, bucket_name, key, destination):
            log["downloaded"].append(key)
            open(destination, "wb").write(b"encrypted bundle")

        def upload_file(self, path, bucket_name, key):
            log["put"].append(key)

    monkeypatch.setattr("boto3.client", lambda *a, **k: Client())
    return log


def test_fetch_gets_the_newest_file_of_a_folder(settings, monkeypatch, tmp_path):
    log = bucket(settings, monkeypatch, [("whpanel/daily/panel-1.bak.enc", 0), ("whpanel/daily/panel-3.bak.enc", 2), ("whpanel/daily/panel-2.bak.enc", 1),
                                         ("whpanel/monthly/panel-9.bak.enc", 9)])
    out = StringIO()
    call_command("offsite_fetch", folder="daily", latest=True, to=str(tmp_path / "drill"), stdout=out)
    assert log["listed"] == ["whpanel/daily/"] and log["downloaded"] == ["whpanel/daily/panel-3.bak.enc"]
    assert (tmp_path / "drill" / "panel-3.bak.enc").read_bytes() == b"encrypted bundle" and "Downloaded" in out.getvalue()


def test_fetch_can_name_a_file_and_says_so_when_there_is_none(settings, monkeypatch, tmp_path):
    log = bucket(settings, monkeypatch, [("whpanel/daily/panel-1.bak.enc", 0), ("whpanel/daily/panel-2.bak.enc", 1)])
    call_command("offsite_fetch", folder="daily", name="panel-1.bak.enc", to=str(tmp_path))
    assert log["downloaded"] == ["whpanel/daily/panel-1.bak.enc"]
    with pytest.raises(CommandError, match="Nothing found"):
        call_command("offsite_fetch", folder="predeploy", latest=True, to=str(tmp_path))
    with pytest.raises(CommandError, match="Say which one"):
        call_command("offsite_fetch", folder="daily", to=str(tmp_path))


def test_fetch_and_the_other_off_site_commands_refuse_when_no_storage_is_set(tmp_path):
    for name, kwargs in (("offsite_fetch", {"folder": "daily", "latest": True, "to": str(tmp_path)}), ("offsite_check", {}),
                         ("offsite_upload", {"file": str(tmp_path / "x"), "folder": "daily"})):
        with pytest.raises(CommandError, match="No off-site storage"):
            call_command(name, **kwargs)


def test_upload_refuses_a_missing_file(settings, monkeypatch, tmp_path):
    bucket(settings, monkeypatch)
    with pytest.raises(CommandError, match="is not a file"):
        call_command("offsite_upload", file=str(tmp_path / "missing"), folder="daily")


def test_the_status_command_fills_the_page_s_answer_and_says_what_it_found(monkeypatch):
    from django.core.cache import cache

    from apps.core import status

    monkeypatch.setattr(status, "_servers", lambda: "ok")
    cache.clear()
    out = StringIO()
    call_command("refresh_status", stdout=out)
    assert cache.get(status.CACHE_KEY) is not None and "Hosting servers: ok" in out.getvalue()


@pytest.fixture
def sqlite_panel(tmp_path, monkeypatch, settings):
    live = tmp_path / "live.sqlite3"
    db = sqlite3.connect(live)
    db.execute("create table notes (text)")
    db.commit()
    db.close()
    monkeypatch.setattr(backup_panel, "connection", SimpleNamespace(vendor="sqlite", settings_dict={"NAME": str(live)}, close=lambda: None))
    settings.PRIVATE_MEDIA_ROOT, settings.MEDIA_ROOT = str(tmp_path / "private"), str(tmp_path / "media")
    return tmp_path


def test_the_backup_command_makes_verifies_and_warns_when_nothing_leaves_the_server(sqlite_panel):
    out = StringIO()
    call_command("backup_panel", to=str(sqlite_panel / "out"), keep=2, stdout=out)
    text = out.getvalue()
    assert "Backup written to" in text and "integrity ok" in text and "No off-site storage is set" in text
    made = next((sqlite_panel / "out").glob("panel-*-full.bak.enc"))
    out = StringIO()
    call_command("backup_panel", verify=str(made), stdout=out)
    assert "integrity ok" in out.getvalue()


def test_the_backup_command_copies_off_site_when_storage_is_set(sqlite_panel, settings, monkeypatch):
    log = bucket(settings, monkeypatch)
    out = StringIO()
    call_command("backup_panel", to=str(sqlite_panel / "out"), keep=2, stdout=out)
    assert len(log["put"]) >= 1 and log["put"][0].startswith("whpanel/daily/panel-") and "Copied off-site" in out.getvalue()
