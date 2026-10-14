"""Native backups (N8): encrypted restic-style snapshots in a bucket, verification, restore with a safety copy, retention, schedules, restore tests, node configuration and the panel's own database."""
import json
import os
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.accounts.roles import Role
from apps.agent import backup_repo, catalog, enrollment, services as agent
from apps.agent.models import AgentJob, AgentNode, BackupRepository
from apps.agent.node.restic import FakeRestic, RepositoryError
from apps.agent.reference_agent import NativeMachine
from apps.audit.models import AuditEvent
from apps.core.management.commands import backup_panel
from apps.hosting import control, native_backups, services as hosting
from apps.hosting.adapters.base import HostingError
from apps.hosting.models import NativeBackup, NativeBackupSettings

from .test_native_website import make_node, order, world  # noqa: F401

pytestmark = pytest.mark.django_db
KEY = ("AKIAEXAMPLEKEY123456", "secret-key-material-1234567890", "backup password for the repository")
ENDPOINT = "https://s3.example.net"


def configure(manager, server, ref, prefix="node-1"):
    node = AgentNode.objects.get(server=server)
    repo = backup_repo.save_repository(manager, node, endpoint=ENDPOINT, bucket="whp-backups", prefix=prefix, access_key=KEY[0], secret_key=KEY[1], password=KEY[2], object_lock=True)
    ref.poll_once  # noqa: B018 (the agent answers when the job is applied below)
    backup_repo.apply(manager, repo)
    ref.poll_once()
    repo.refresh_from_db()
    return repo


@pytest.fixture
def backed(world, tmp_path):  # noqa: F811
    manager, client, product = world
    product.resource_limits = {"disk_mb": 2048, "databases": 3, "backups": 2}
    product.save()
    server, ref = make_node(manager, "web-1")
    ref.machine = NativeMachine(files_dir=tmp_path / "web", bucket_dir=tmp_path / "bucket")
    account = order(manager, client, product, server)
    ref.poll_once()
    account.refresh_from_db()
    site = tmp_path / "web" / account.username / "sites" / "acme.com" / "public_html"
    site.mkdir(parents=True)
    (site / "index.html").write_text("<h1>original</h1>")
    (site / "style.css").write_text("body{}")
    repo = configure(manager, server, ref)
    agent.inline_agent = ref.poll_once
    yield manager, account, ref, client.contacts.get().user, site, repo, tmp_path
    agent.inline_agent = None


def b(owner, account, op, *args, **kw):
    return control.perform(owner, account, "backups", op, *args, **kw)


def run(ref):
    while ref.poll_once():
        pass


# --- storage settings ---------------------------------------------------------------------------------------------------------------------

def test_the_storage_keys_are_kept_encrypted_and_reach_the_node_only_in_its_root_file(backed):
    manager, account, ref, owner, site, repo, tmp = backed
    assert repo.applied_ok and KEY[1] not in repo.secret_key_encrypted and KEY[2] not in repo.password_encrypted and repo.secrets() == KEY
    env = ref.machine.system.files["/etc/whp/restic.env"]
    assert "RESTIC_PASSWORD=" + KEY[2] in env and ["write", "/etc/whp/restic.env", "0o600", "root"] in ref.machine.system.commands
    job = AgentJob.objects.get(command="BACKUP_CONFIGURE")
    assert job.params["secret_key"] == "***" and job.params["repo_password"] == "***" and job.params["access_key"] == "***"
    blob = json.dumps([e.metadata for e in AuditEvent.objects.all()])
    assert KEY[0] not in blob and KEY[1] not in blob and KEY[2] not in blob


def test_settings_are_checked_and_the_password_cannot_be_changed(backed):
    manager, account, ref, owner, site, repo, tmp = backed
    node = repo.node
    from apps.core.exceptions import ServiceError

    for bad in ({"endpoint": "http://plain.example.net"}, {"bucket": "UPPER"}, {"prefix": "../x"}, {"endpoint": "https://a b"}):
        with pytest.raises(ServiceError):
            backup_repo.save_repository(manager, node, **{"endpoint": ENDPOINT, "bucket": "whp-backups", "prefix": "node-1", **bad})
    with pytest.raises(ServiceError, match="cannot be changed"):
        backup_repo.save_repository(manager, node, endpoint=ENDPOINT, bucket="whp-backups", prefix="node-1", password="a different password!")
    kept = backup_repo.save_repository(manager, node, endpoint=ENDPOINT, bucket="other", prefix="node-1")  # keys left blank are kept
    assert kept.secrets() == KEY and not kept.applied_ok  # but the node has to be told again


def test_a_node_that_refuses_the_settings_is_reported_and_the_old_ones_stay(backed):
    manager, account, ref, owner, site, repo, tmp = backed
    node = repo.node
    backup_repo.save_repository(manager, node, endpoint=ENDPOINT, bucket="whp-backups", prefix="node-1")
    (tmp / "bucket" / "whp-backups" / "node-1" / "check").write_bytes(b"WHPB1" + b"x" * 40)  # a repository that does not accept our password
    job = backup_repo.apply(manager, BackupRepository.objects.get(node=node))
    ref.poll_once()
    repo.refresh_from_db()
    assert not repo.applied_ok and "decrypted" in repo.apply_error
    assert "RESTIC_PASSWORD=" + KEY[2] in ref.machine.system.files["/etc/whp/restic.env"] and AgentJob.objects.get(pk=job.pk).status == "failed"


# --- encryption and integrity --------------------------------------------------------------------------------------------------------------

def test_a_backup_is_encrypted_at_rest(backed):
    manager, account, ref, owner, site, repo, tmp = backed
    b(owner, account, "create_backup", "files")
    run(ref)
    stored = next((tmp / "bucket" / "whp-backups" / "node-1" / "snapshots").glob("*.enc")).read_bytes()
    assert stored.startswith(b"WHPB1") and b"original" not in stored and b"index.html" not in stored and b"account:" not in stored


def test_the_wrong_password_and_a_tampered_file_are_detected(backed):
    manager, account, ref, owner, site, repo, tmp = backed
    b(owner, account, "create_backup", "files")
    run(ref)
    folder = tmp / "bucket" / "whp-backups" / "node-1"
    with pytest.raises(RepositoryError, match="wrong key"):
        FakeRestic(folder, "not the password").snapshots()
    snap = next((folder / "snapshots").glob("*.enc"))
    data = bytearray(snap.read_bytes())
    data[-5] ^= 0xFF
    snap.write_bytes(bytes(data))
    ok, detail = FakeRestic(folder, KEY[2]).check(snap.stem)
    assert not ok and "altered" in detail


# --- the customer's backups screen --------------------------------------------------------------------------------------------------------

def test_a_backup_runs_is_listed_verified_and_kept_off_the_server(backed):
    manager, account, ref, owner, site, repo, tmp = backed
    options = b(owner, account, "backup_options")
    assert options.max_backups == 2 and options.offsite and [k[0] for k in options.kinds] == ["files", "database", "full"]
    group = b(owner, account, "create_backup", "files")
    assert b(owner, account, "list_backups")[0].status == "running"  # started, not finished: only the node can finish it
    run(ref)
    info = b(owner, account, "list_backups")[0]
    assert (info.backup_id, info.kind, info.status, info.source, info.offsite, info.verified) == (group, "files", "complete", "manual", "ok", "") and info.size_mb > 0
    assert b(owner, account, "verify_backup", group).verified == "ok"
    assert AuditEvent.objects.filter(action="hosting.backup_completed").exists()
    with pytest.raises(Exception, match="always kept away"):
        b(owner, account, "set_backup_offsite", False)


def test_full_holds_files_databases_and_settings_and_restore_puts_them_back(backed):
    manager, account, ref, owner, site, repo, tmp = backed
    prefix = account.username + "_"
    control.perform(owner, account, "databases", "create_database", "shop")
    ref.machine.engine.add_table(prefix + "shop", "orders", [("id", "int", False, True, None)], [[1], [2], [3]])
    group = b(owner, account, "create_backup", "full")
    run(ref)
    record = NativeBackup.objects.get(group=group)
    assert record.status == "complete" and record.kind == "full"
    (site / "index.html").write_text("<h1>DEFACED</h1>")
    (site / "extra.txt").write_text("added later")
    ref.machine.engine.dbs[prefix + "shop"]["orders"]["rows"] = []
    b(owner, account, "restore_backup", group)
    run(ref)
    assert (site / "index.html").read_text() == "<h1>original</h1>" and not (site / "extra.txt").exists()  # the backup's state, exactly
    assert ref.machine.engine.dbs[prefix + "shop"]["orders"]["rows"] == [[1], [2], [3]]
    safety = NativeBackup.objects.get(source="before restore")
    assert safety.status == "complete" and safety.snapshot_id
    listing = b(owner, account, "list_backups")
    assert {i.source for i in listing} == {"manual", "before restore"}
    # the mistake can be undone: restoring the safety copy brings the defaced page back
    b(owner, account, "restore_backup", safety.group)
    run(ref)
    assert (site / "index.html").read_text() == "<h1>DEFACED</h1>"


def test_a_backup_that_fails_its_check_is_never_restored(backed):
    manager, account, ref, owner, site, repo, tmp = backed
    group = b(owner, account, "create_backup", "files")
    run(ref)
    snap = next((tmp / "bucket" / "whp-backups" / "node-1" / "snapshots").glob("*.enc"))
    data = bytearray(snap.read_bytes())
    data[-9] ^= 0xFF
    snap.write_bytes(bytes(data))
    (site / "index.html").write_text("<h1>current</h1>")
    with pytest.raises(Exception, match="failed its check"):
        b(owner, account, "restore_backup", group)
    assert (site / "index.html").read_text() == "<h1>current</h1>" and NativeBackup.objects.get(group=group).verified == "failed"


def test_links_inside_the_website_are_never_followed_into_a_backup(backed):
    manager, account, ref, owner, site, repo, tmp = backed
    secret = tmp / "shadow.txt"
    secret.write_text("root:SECRETHASH")
    try:
        os.symlink(secret, site / "leak.txt")
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot make symbolic links")
    b(owner, account, "create_backup", "files")
    run(ref)
    snapshot = NativeBackup.objects.get().snapshot_id
    restic = FakeRestic(tmp / "bucket" / "whp-backups" / "node-1", KEY[2])
    assert "files/acme.com/public_html/leak.txt" not in restic.meta(snapshot)["manifest"] and "index.html" in " ".join(restic.meta(snapshot)["manifest"])
    assert "SECRETHASH" not in str(restic.meta(snapshot))


def test_another_accounts_backup_cannot_be_used_or_deleted(backed, world):  # noqa: F811
    manager, account, ref, owner, site, repo, tmp = backed
    b(owner, account, "create_backup", "files")
    run(ref)
    snapshot = NativeBackup.objects.get().snapshot_id
    for command, extra in (("BACKUP_VERIFY", {}), ("BACKUP_FORGET", {}), ("BACKUP_TEST_RESTORE", {}), ("BACKUP_RESTORE", {"kind": "files"})):
        with pytest.raises(Exception, match="does not belong"):
            ref.machine.backups.run(command, {"account": "otheracct", "snapshot_id": snapshot, **extra})
    assert NativeBackup.objects.get().status == "complete"


def test_nothing_can_be_backed_up_until_storage_is_applied(world, tmp_path):  # noqa: F811
    manager, client, product = world
    server, ref = make_node(manager, "web-2")
    ref.machine = NativeMachine(files_dir=tmp_path / "w2")
    account = order(manager, client, product, server)
    ref.poll_once()
    owner = client.contacts.get().user
    with pytest.raises(Exception, match="not set up"):
        b(owner, account, "create_backup", "files")
    assert NativeBackup.objects.get().status == "failed"


def test_retention_keeps_the_newest_and_forgets_the_rest(backed):
    manager, account, ref, owner, site, repo, tmp = backed
    groups = []
    for i in range(3):
        groups.append(b(owner, account, "create_backup", "files"))
        run(ref)
        NativeBackup.objects.filter(group=groups[-1]).update(created_at=timezone.now() - timedelta(days=3 - i))
    run(ref)
    shown = [x.backup_id for x in b(owner, account, "list_backups")]
    assert shown == [groups[2], groups[1]]  # the plan keeps 2
    assert NativeBackup.objects.get(group=groups[0]).status == "deleted"
    assert len(list((tmp / "bucket" / "whp-backups" / "node-1" / "snapshots").glob("*.enc"))) == 2


def test_deleting_a_backup_removes_it_from_the_storage_too(backed):
    manager, account, ref, owner, site, repo, tmp = backed
    group = b(owner, account, "create_backup", "files")
    run(ref)
    b(owner, account, "delete_backup", group)
    run(ref)
    assert b(owner, account, "list_backups") == [] and not list((tmp / "bucket" / "whp-backups" / "node-1" / "snapshots").glob("*.enc"))


# --- schedules, restore tests, node configuration ---------------------------------------------------------------------------------------

def test_scheduled_backups_run_when_due_and_not_before(backed):
    manager, account, ref, owner, site, repo, tmp = backed
    b(owner, account, "set_backup_schedule", "daily")
    now = timezone.now()
    assert native_backups.run_due_backups(now) == 1
    run(ref)
    assert native_backups.run_due_backups(now + timedelta(hours=6)) == 0
    assert native_backups.run_due_backups(now + timedelta(hours=25)) == 1
    assert NativeBackup.objects.filter(source="scheduled").count() == 2
    b(owner, account, "set_backup_schedule", "off")
    assert native_backups.run_due_backups(now + timedelta(days=9)) == 0
    with pytest.raises(Exception, match="off, every day"):
        b(owner, account, "set_backup_schedule", "hourly")


def test_a_monthly_restore_test_proves_a_backup_can_be_restored_and_catches_one_that_cannot(backed):
    manager, account, ref, owner, site, repo, tmp = backed
    b(owner, account, "create_backup", "files")
    run(ref)
    assert native_backups.run_restore_tests() == 1
    run(ref)
    record = NativeBackup.objects.get()
    assert record.restore_test_ok is True and record.restore_tested_at and AuditEvent.objects.filter(action="hosting.backup_restore_test_passed").exists()
    assert (site / "index.html").read_text() == "<h1>original</h1>"  # nothing live was touched
    snap = next((tmp / "bucket" / "whp-backups" / "node-1" / "snapshots").glob("*.enc"))
    snap.write_bytes(snap.read_bytes()[:-30])
    native_backups.run_restore_tests()
    run(ref)
    record.refresh_from_db()
    assert record.restore_test_ok is False and AuditEvent.objects.filter(action="hosting.backup_restore_test_failed").exists()


def test_the_nodes_own_configuration_is_backed_up_but_never_its_backup_keys(backed):
    manager, account, ref, owner, site, repo, tmp = backed
    backup_repo.backup_node_config(manager, repo.node)
    ref.poll_once()
    repo.refresh_from_db()
    assert repo.node_config_snapshot and repo.node_config_at
    restic = FakeRestic(tmp / "bucket" / "whp-backups" / "node-1", KEY[2])
    names = " ".join(restic.meta(repo.node_config_snapshot)["manifest"])
    assert "var__lib__whp__accounts" in names and "restic.env" not in names
    assert KEY[2] not in str(restic.meta(repo.node_config_snapshot))


def test_a_failed_backup_is_recorded_and_shown_to_staff(backed, client):
    manager, account, ref, owner, site, repo, tmp = backed
    import shutil

    shutil.rmtree(site.parent.parent)  # the website folder is gone on the node
    b(owner, account, "create_backup", "files")
    run(ref)
    rec = NativeBackup.objects.get()
    assert rec.status == "failed" and "No such website" in rec.error and AuditEvent.objects.filter(action="hosting.backup_failed").exists()
    client.force_login(manager)
    html = client.get(reverse("agent_staff:backups")).content.decode()
    assert "web-1" in html and "Ready" in html and "No such website" in html and "never restore-tested" in html


def test_staff_backup_page_saves_applies_and_never_shows_secrets(backed, client):
    manager, account, ref, owner, site, repo, tmp = backed
    client.force_login(manager)
    page = client.get(reverse("agent_staff:backups")).content.decode()
    assert KEY[1] not in page and KEY[2] not in page and KEY[0] not in page and "already set" in page
    client.post(reverse("agent_staff:backup_save", args=[repo.node_id]), {"endpoint": ENDPOINT, "bucket": "whp-backups", "prefix": "node-1", "object_lock": ""})
    repo.refresh_from_db()
    assert not repo.object_lock_confirmed and "not been confirmed as locked" in client.get(reverse("agent_staff:backups")).content.decode()
    client.post(reverse("agent_staff:backup_apply", args=[repo.node_id]))
    ref.poll_once()
    repo.refresh_from_db()
    assert repo.applied_ok


def test_backup_commands_are_typed():
    ok = {"endpoint": ENDPOINT, "bucket": "whp-backups", "prefix": "node-1", "access_key": KEY[0], "secret_key": KEY[1], "repo_password": KEY[2]}
    assert catalog.validate("BACKUP_CONFIGURE", ok)["bucket"] == "whp-backups"
    for bad in ({**ok, "endpoint": "ftp://x.example"}, {**ok, "bucket": "Bad_Bucket"}, {**ok, "prefix": "a b"}, {**ok, "repo_password": "short"}):
        with pytest.raises(catalog.InvalidCommand):
            catalog.validate("BACKUP_CONFIGURE", bad)
    with pytest.raises(catalog.InvalidCommand):
        catalog.validate("BACKUP_VERIFY", {"account": "abcd1234", "snapshot_id": "../../x"})
    assert catalog.validate("BACKUP_NODE_CONFIG", {}) == {} and not catalog.COMMANDS["BACKUP_RUN"].idempotent


# --- WHPanel's own database ----------------------------------------------------------------------------------------------------------------

def test_the_panels_own_database_is_backed_up_encrypted_and_can_be_verified(tmp_path, settings, staff):
    staff(Role.MANAGER)  # some data in the database
    path = backup_panel.make_backup(tmp_path / "panel")
    from django.db import connection

    raw = path.read_bytes()
    assert path.name.startswith(f"panel-{connection.vendor}-") and b"SQLite format" not in raw and b"PGDMP" not in raw and b"auth_user" not in raw  # encrypted
    assert ("integrity ok" if connection.vendor == "sqlite" else "postgresql dump") in backup_panel.verify_backup(path)
    damaged = tmp_path / f"panel-{connection.vendor}-damaged.bak.enc"
    damaged.write_bytes(raw[:-20])
    with pytest.raises(Exception):
        backup_panel.verify_backup(damaged)


def test_old_panel_backups_are_pruned_and_the_command_reports(tmp_path, capsys):
    import time

    for _ in range(3):
        backup_panel.make_backup(tmp_path, keep=2)
        time.sleep(1.1)  # file names carry the second
    assert len(list(tmp_path.glob("panel-*.bak.enc"))) == 2
    call_command("backup_panel", "--to", str(tmp_path), "--keep", "5")
    from django.db import connection

    assert ("integrity ok" if connection.vendor == "sqlite" else "postgresql dump") in capsys.readouterr().out
    _ = (hosting, HostingError, NativeBackupSettings, enrollment)
