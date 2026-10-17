"""
Back up WHPanel's OWN database, encrypted (N8 / ADR 18: the panel's database is backed up separately from the nodes' backups). Run it nightly and copy the files away from this server.

* SQLite: a consistent online copy through SQLite's backup interface.
* PostgreSQL: ``pg_dump`` (custom format); the password goes in the environment, never on the command line. NOT tried against a real PostgreSQL.

The files are encrypted with the credentials key (``CREDENTIALS_ENCRYPTION_KEY``, or the key derived from ``SECRET_KEY``). That key is NOT in the backup and must be kept
somewhere else, offline: without it the backup cannot be read. Copying to remote storage is not done here (use restic or rclone on the file).
"""
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from apps.core import crypto


def make_backup(target_dir, keep=14):
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
    vendor = connection.vendor
    with tempfile.TemporaryDirectory() as scratch:
        raw = Path(scratch) / "dump"
        if vendor == "sqlite":
            source = sqlite3.connect(connection.settings_dict["NAME"])
            copy = sqlite3.connect(raw)
            with copy:
                source.backup(copy)
            copy.close()
            source.close()
        elif vendor == "postgresql":
            db = connection.settings_dict
            env = {**os.environ, "PGPASSWORD": db.get("PASSWORD") or ""}
            done = subprocess.run(["pg_dump", "--format=custom", "--no-password", "-h", db.get("HOST") or "localhost", "-p", str(db.get("PORT") or 5432), "-U", db["USER"], "-f", str(raw), db["NAME"]],
                                  shell=False, capture_output=True, text=True, env=env, check=False, timeout=3600)
            if done.returncode != 0:
                raise CommandError("pg_dump failed: " + (done.stderr.strip().splitlines() or ["?"])[-1][:200])
        else:
            raise CommandError(f"Backing up a {vendor} database is not supported.")
        target = target_dir / f"panel-{vendor}-{stamp}.bak.enc"
        target.write_bytes(crypto.encrypt_bytes(raw.read_bytes()))
    for old in sorted(target_dir.glob("panel-*.bak.enc"))[:-keep] if keep else []:
        old.unlink()
    return target


def offsite_configured():
    return bool(getattr(settings, "PANEL_BACKUP_S3_BUCKET", "") and getattr(settings, "PANEL_BACKUP_S3_ACCESS_KEY", "") and getattr(settings, "PANEL_BACKUP_S3_SECRET_KEY", ""))


def upload_offsite(path):
    """Copy an (already encrypted) backup to S3-compatible storage (Backblaze B2, Wasabi, S3...), away from this server. Raises CommandError on a failure; the local copy stays."""
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    path = Path(path)
    client = boto3.client("s3", endpoint_url=settings.PANEL_BACKUP_S3_ENDPOINT or None, aws_access_key_id=settings.PANEL_BACKUP_S3_ACCESS_KEY,
                          aws_secret_access_key=settings.PANEL_BACKUP_S3_SECRET_KEY, region_name=settings.PANEL_BACKUP_S3_REGION or None)
    key = f"{(settings.PANEL_BACKUP_S3_PREFIX or 'panel-backups').strip('/')}/{path.name}"
    try:
        client.upload_file(str(path), settings.PANEL_BACKUP_S3_BUCKET, key)
    except (BotoCoreError, ClientError, OSError) as exc:
        raise CommandError(f"The off-site copy failed ({type(exc).__name__}); the backup is kept on this server.") from exc
    return key


def verify_backup(path):
    """Decrypt a backup and check the database inside is sound. Returns a short description, or raises CommandError."""
    path = Path(path)
    try:
        data = crypto.decrypt_bytes(path.read_bytes())
    except (ValueError, OSError) as exc:
        raise CommandError(str(exc)) from exc
    with tempfile.TemporaryDirectory() as scratch:
        raw = Path(scratch) / "dump"
        raw.write_bytes(data)
        if "-sqlite-" in path.name:
            db = sqlite3.connect(raw)
            try:
                result = db.execute("PRAGMA integrity_check").fetchone()[0]
                tables = db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
            finally:
                db.close()
            if result != "ok":
                raise CommandError(f"The database inside is damaged: {result}")
            return f"sqlite database, {tables} tables, integrity ok"
        done = subprocess.run(["pg_restore", "--list", str(raw)], shell=False, capture_output=True, text=True, check=False)
        if done.returncode != 0:
            raise CommandError("pg_restore could not read the dump.")
        return f"postgresql dump, {len(done.stdout.splitlines())} entries"


class Command(BaseCommand):
    help = "Make an encrypted backup of WHPanel's own database (and with --verify FILE, prove a backup can be read)."

    def add_arguments(self, parser):
        parser.add_argument("--to", default=None, help="Folder for the backup files (default: PANEL_BACKUP_DIR).")
        parser.add_argument("--keep", type=int, default=14, help="How many backup files to keep (0 keeps all).")
        parser.add_argument("--verify", default=None, help="Check an existing backup file instead of making one.")

    def handle(self, *args, **options):
        if options["verify"]:
            self.stdout.write(self.style.SUCCESS(verify_backup(options["verify"])))
            return
        target = make_backup(options["to"] or getattr(settings, "PANEL_BACKUP_DIR", settings.BASE_DIR / "panel_backups"), options["keep"])
        self.stdout.write(self.style.SUCCESS(f"Backup written to {target} ({verify_backup(target)})"))
        if offsite_configured():
            self.stdout.write(self.style.SUCCESS(f"Copied off-site: {upload_offsite(target)}"))
        else:
            self.stdout.write(self.style.WARNING("No off-site storage is set (PANEL_BACKUP_S3_*): this backup exists only on this server."))
