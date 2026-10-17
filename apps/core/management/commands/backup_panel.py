"""
Back up WHPanel itself, in ONE encrypted file: the database AND the files customers uploaded (ticket attachments, proofs of payment, public media).

Inside the encryption the file is a compressed archive: ``manifest.json``, ``database.dump`` (PostgreSQL ``pg_dump``, custom format) or ``database.sqlite`` (SQLite's own online copy), and ``files/private/...``
and ``files/media/...``. The database password goes in the environment, never on a command line. Older backups (the database alone, no archive) can still be read and restored.

The file is encrypted with the credentials key (``CREDENTIALS_ENCRYPTION_KEY``, or the key derived from ``SECRET_KEY``). That key is NOT in the backup and must be kept somewhere else, offline:
without it the backup cannot be read. Copying to remote storage is optional (``PANEL_BACKUP_S3_*``).

What a restore does is described on ``restore_backup``. The server's own settings file (``.env``) is NOT in the backup: it holds the keys themselves.
"""
import json
import os
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from apps.core import crypto

MANIFEST = "manifest.json"
DB_NAMES = {"postgresql": "database.dump", "sqlite": "database.sqlite"}
FILE_ROOTS = (("files/private", "PRIVATE_MEDIA_ROOT"), ("files/media", "MEDIA_ROOT"))


def _roots():
    return [(arc, Path(getattr(settings, attr))) for arc, attr in FILE_ROOTS]


def _dump_database(raw):
    """Write a consistent copy of the live database to ``raw``. Returns the vendor."""
    vendor = connection.vendor
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
    return vendor


def make_backup(target_dir, keep=14):
    """Write ``panel-<database>-<time>-full.bak.enc`` into ``target_dir`` and delete the oldest beyond ``keep`` (0 keeps all). Returns the file."""
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    moment = timezone.now()
    while (target_dir / f"panel-{connection.vendor}-{moment:%Y%m%dT%H%M%SZ}-full.bak.enc").exists():
        moment += timedelta(seconds=1)  # two backups in the same second must not overwrite each other (a restore takes a safety copy right after)
    stamp = moment.strftime("%Y%m%dT%H%M%SZ")
    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        raw = scratch / "db"
        vendor = _dump_database(raw)
        bundle = scratch / "bundle.tar.gz"
        counts = {}
        with tarfile.open(bundle, "w:gz") as tar:
            for arc, root in _roots():
                counts[arc] = sum(1 for p in root.rglob("*") if p.is_file()) if root.is_dir() else 0
            manifest = scratch / MANIFEST
            manifest.write_text(json.dumps({"format": 1, "vendor": vendor, "created": stamp, "files": counts}), encoding="utf-8")
            tar.add(manifest, arcname=MANIFEST)
            tar.add(raw, arcname=DB_NAMES[vendor])
            for arc, root in _roots():
                if root.is_dir():
                    tar.add(root, arcname=arc)
        target = target_dir / f"panel-{vendor}-{stamp}-full.bak.enc"
        crypto.encrypt_file(bundle, target)
    if keep:
        for old in sorted(target_dir.glob("panel-*.bak.enc"), key=lambda p: p.stat().st_mtime)[:-keep]:
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


def prune_offsite(keep):
    """Delete the oldest off-site backups beyond the newest ``keep`` (by the time they were stored). Returns how many were deleted."""
    import boto3

    client = boto3.client("s3", endpoint_url=settings.PANEL_BACKUP_S3_ENDPOINT or None, aws_access_key_id=settings.PANEL_BACKUP_S3_ACCESS_KEY,
                          aws_secret_access_key=settings.PANEL_BACKUP_S3_SECRET_KEY, region_name=settings.PANEL_BACKUP_S3_REGION or None)
    prefix = (settings.PANEL_BACKUP_S3_PREFIX or "panel-backups").strip("/") + "/"
    found = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=settings.PANEL_BACKUP_S3_BUCKET, Prefix=prefix):
        found += [o for o in page.get("Contents", []) if o["Key"].endswith(".bak.enc")]
    found.sort(key=lambda o: o["LastModified"], reverse=True)
    old = found[keep:] if keep else []
    for item in old:
        client.delete_object(Bucket=settings.PANEL_BACKUP_S3_BUCKET, Key=item["Key"])
    return len(old)


def run_backup(keep=None):
    """The whole backup job: make the encrypted file, read it back, copy it off-site when storage is set, delete the oldest beyond ``keep``. Returns (file, short text)."""
    keep = keep if keep is not None else getattr(settings, "PANEL_BACKUP_KEEP", 4)
    target = make_backup(settings.PANEL_BACKUP_DIR, keep)
    verify_backup(target)  # a backup that cannot be read back is not a backup
    if offsite_configured():
        text = f"off-site {upload_offsite(target)}, {prune_offsite(keep)} old ones deleted there"
    else:
        text = "no off-site storage set"
    return target, text


# --- reading a backup ---------------------------------------------------------------------------------------

def _open(path, scratch):
    """Decrypt ``path`` into ``scratch``. Returns ("bundle", archive path) or ("legacy", raw database file) for an older backup that is the database alone."""
    scratch = Path(scratch)
    plain = scratch / "plain"
    try:
        crypto.decrypt_file(Path(path), plain)
    except (ValueError, OSError) as exc:
        raise CommandError(str(exc)) from exc
    with open(plain, "rb") as handle:
        head = handle.read(2)
    return ("bundle", plain) if head == b"\x1f\x8b" else ("legacy", plain)


def _file_names(archive):
    """The names of the regular files in the archive (not the folders)."""
    with tarfile.open(archive, "r:gz") as tar:
        return [m.name for m in tar.getmembers() if m.isfile()]


def _extract_database(archive, scratch):
    """The database file from a bundle, and its vendor."""
    scratch = Path(scratch)
    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
        for vendor, name in DB_NAMES.items():
            if name in names:
                with tar.extractfile(name) as src, open(scratch / name, "wb") as dst:
                    while chunk := src.read(1024 * 1024):
                        dst.write(chunk)
                return vendor, scratch / name
    raise CommandError("This backup holds no database.")


def _check_database(vendor, raw):
    if vendor == "sqlite":
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


def verify_backup(path):
    """Decrypt a backup and check what is inside is sound. Returns a short description, or raises CommandError."""
    path = Path(path)
    with tempfile.TemporaryDirectory() as scratch:
        kind, plain = _open(path, scratch)
        if kind == "legacy":
            vendor = "sqlite" if "-sqlite-" in path.name else "postgresql"
            return _check_database(vendor, plain) + " (database only)"
        try:
            vendor, raw = _extract_database(plain, scratch)
            files = sum(1 for n in _file_names(plain) if n.startswith("files/"))
        except tarfile.TarError as exc:
            raise CommandError("The backup archive is damaged.") from exc
        return f"{_check_database(vendor, raw)}, {files} uploaded file(s)"


# --- restoring ----------------------------------------------------------------------------------------------

def _safe_target(root, name):
    """Where ``name`` (a path inside the archive, below ``files/<x>/``) goes under ``root``; never outside it."""
    target = (root / name).resolve()
    if root.resolve() not in target.parents:
        raise CommandError("The backup holds a file with an unsafe path.")
    return target


def _restore_files(archive):
    count = 0
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            for arc, root in _roots():
                if member.name.startswith(arc + "/"):
                    target = _safe_target(root, member.name[len(arc) + 1:])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as src, open(target, "wb") as dst:
                        while chunk := src.read(1024 * 1024):
                            dst.write(chunk)
                    count += 1
    return count


def restore_backup(path):
    """
    Put the panel back as it was in one of its encrypted backups: the database, and (for a full backup) the uploaded files. Before anything is touched, the file is read back to prove it is whole,
    and a fresh full backup of the CURRENT state is taken (kept, never pruned) so the restore can be undone. PostgreSQL is restored in ONE transaction (``--single-transaction``): it completes
    or changes nothing; SQLite (development) is copied back through its own backup interface. Files are written back over files of the same name; files newer than the backup are left alone
    (they show as "nothing points to them" on the Storage page, where they can be deleted). Then migrations run (a backup older than the code is brought up to date) and the cache is cleared.
    """
    from django.core.cache import cache
    from django.core.management import call_command

    path = Path(path)
    summary = verify_backup(path)
    safety = make_backup(settings.PANEL_BACKUP_DIR, 0)  # keep=0: nothing is pruned
    with tempfile.TemporaryDirectory() as scratch:
        kind, plain = _open(path, scratch)
        if kind == "legacy":
            vendor, raw, archive = ("sqlite" if "-sqlite-" in path.name else "postgresql"), plain, None
        else:
            archive = plain
            vendor, raw = _extract_database(archive, scratch)
        if vendor != connection.vendor:
            raise CommandError(f"This backup is of a {vendor} database; this panel uses {connection.vendor}.")
        if vendor == "postgresql":
            db = connection.settings_dict
            env = {**os.environ, "PGPASSWORD": db.get("PASSWORD") or ""}
            done = subprocess.run(["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges", "--single-transaction", "--no-password",
                                   "-h", db.get("HOST") or "localhost", "-p", str(db.get("PORT") or 5432), "-U", db["USER"], "-d", db["NAME"], str(raw)],
                                  shell=False, capture_output=True, text=True, env=env, check=False, timeout=3600)
            if done.returncode != 0:
                raise CommandError("The restore failed and changed nothing: " + (done.stderr.strip().splitlines() or ["?"])[-1][:200])
        else:
            source = sqlite3.connect(raw)
            target = sqlite3.connect(connection.settings_dict["NAME"])
            try:
                with target:
                    source.backup(target)
            finally:
                source.close()
                target.close()
        restored_files = _restore_files(archive) if archive is not None else 0
    connection.close()  # the old connection saw the old tables
    call_command("migrate", interactive=False, verbosity=0)
    cache.clear()
    return f"restored from {path.name} ({summary}); {restored_files} file(s) put back; the state as it was just before is saved as {safety.name}"


class Command(BaseCommand):
    help = "Make an encrypted backup of WHPanel (database and uploaded files); with --verify FILE prove a backup can be read; with --restore FILE put one back."

    def add_arguments(self, parser):
        parser.add_argument("--to", default=None, help="Folder for the backup files (default: PANEL_BACKUP_DIR).")
        parser.add_argument("--keep", type=int, default=None, help="How many backup files to keep (0 keeps all). Default: PANEL_BACKUP_KEEP.")
        parser.add_argument("--verify", default=None, help="Check an existing backup file instead of making one.")
        parser.add_argument("--restore", default=None, help="Restore this backup file (asks for no confirmation: use the Storage page for the careful way).")

    def handle(self, *args, **options):
        if options["verify"]:
            self.stdout.write(self.style.SUCCESS(verify_backup(options["verify"])))
            return
        if options["restore"]:
            self.stdout.write(self.style.SUCCESS(restore_backup(options["restore"])))
            return
        keep = options["keep"] if options["keep"] is not None else getattr(settings, "PANEL_BACKUP_KEEP", 4)
        target = make_backup(options["to"] or getattr(settings, "PANEL_BACKUP_DIR", settings.BASE_DIR / "panel_backups"), keep)
        self.stdout.write(self.style.SUCCESS(f"Backup written to {target} ({verify_backup(target)})"))
        if offsite_configured():
            self.stdout.write(self.style.SUCCESS(f"Copied off-site: {upload_offsite(target)} ({prune_offsite(keep)} older ones deleted there)"))
        else:
            self.stdout.write(self.style.WARNING("No off-site storage is set (PANEL_BACKUP_S3_*): this backup exists only on this server."))
