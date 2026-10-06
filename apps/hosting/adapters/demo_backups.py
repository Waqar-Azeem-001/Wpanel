"""
The demo provider's backups: real, restorable archives. A backup is a zip kept beside the account (never inside the website folder)
with a checksum recorded when it was made.

* Kinds: ``files`` (the website folder), ``database`` (databases and their data), ``email`` (mailboxes and messages) and ``full``
  (those three plus the account's settings: domains, DNS, SSL, PHP, cron jobs, FTP accounts and IP blocks).
* Integrity: ``verify`` recomputes the checksum, tests every archive member and reads every settings file; a restore is refused
  unless that passes ("a successful backup job alone is not proof of recoverability").
* Restore: takes a safety copy of what it is about to replace (source "before restore"), then replaces exactly the parts the
  backup holds. Archive paths are checked, so a damaged or hostile archive cannot write outside the account.
* Retention: at most seven backups are kept; making room removes the oldest. A schedule (daily or weekly, full backups) is
  checked whenever backups are listed.
No server snapshot is involved; a real provider (the cPanel adapter, a native node with off-server storage) implements the same
``BackupsCapability`` contract.
"""
import hashlib
import json
import shutil
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .base import HostingError
from .capabilities import BackupInfo, BackupOptions

MAX_BACKUPS = 7
MAX_BYTES = 300_000_000
KINDS = [("files", "Website files", "Everything in your website folder."),
         ("database", "Databases", "All databases, their tables and users."),
         ("email", "Email", "Mailboxes, messages, forwarders and replies."),
         ("full", "Full account", "Files, databases, email and all settings (domains, DNS, SSL, PHP, cron, FTP, IP blocks).")]
SCHEDULES = [("off", "Off"), ("daily", "Every day"), ("weekly", "Every week")]
PERIOD = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}
SETTINGS = ["domains.json", "ssl.json", "php.json", "cron.json", "ftp.json", "ip_rules.json"]
TRASH = ".trash"


def _now():
    return datetime.now(timezone.utc)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe(name):
    parts = name.split("/")
    return bool(name) and not name.startswith("/") and "\\" not in name and ".." not in parts and "\x00" not in name


class DemoBackups:
    def __init__(self, state_dir, site_dir):
        self.state = Path(state_dir)
        self.site = Path(site_dir)
        self.dir = self.state / "backups"
        self.index_file = self.dir / "backups.json"
        self.schedule_file = self.state / "backup_schedule.json"  # not part of any backup: it must survive a restore

    # --- bookkeeping -----------------------------------------------------------------------------------------------

    def _index(self):
        try:
            return json.loads(self.index_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _save_index(self, rows):
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_file.write_text(json.dumps(rows), encoding="utf-8")

    def _schedule(self):
        try:
            return json.loads(self.schedule_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"schedule": "off", "last": None}

    def _find(self, rows, backup_id):
        for row in rows:
            if row["id"] == backup_id:
                return row
        raise HostingError("That backup no longer exists.")

    @staticmethod
    def _info(row):
        return BackupInfo(backup_id=row["id"], kind=row["kind"], created_at=row["created_at"], size_mb=round(row["size"] / 1048576, 2),
                          status="complete", source=row["source"], verified=row.get("verified", ""), verified_at=row.get("verified_at"))

    def options(self):
        data = self._schedule()
        return BackupOptions(kinds=list(KINDS), max_backups=MAX_BACKUPS, schedules=list(SCHEDULES), schedule=data["schedule"],
                             last_scheduled_at=data.get("last"))

    # --- what a backup holds ---------------------------------------------------------------------------------------

    def _members(self, kind):
        """(archive name, real path) for everything a backup of this kind holds."""
        found = []
        if kind in ("files", "full") and self.site.exists():
            for path in sorted(self.site.rglob("*")):
                rel = path.relative_to(self.site)
                if rel.parts[0] == TRASH or path.is_symlink() or not path.is_file():
                    continue
                found.append((f"files/{rel.as_posix()}", path))
        if kind in ("database", "full"):
            if (self.state / "databases.json").is_file():
                found.append(("state/databases.json", self.state / "databases.json"))
            for path in sorted((self.state / "data").glob("*")) if (self.state / "data").is_dir() else []:
                if path.is_file() and not path.is_symlink():
                    found.append((f"state/data/{path.name}", path))
        if kind in ("email", "full") and (self.state / "email.json").is_file():
            found.append(("state/email.json", self.state / "email.json"))
        if kind == "full":
            found += [(f"state/{n}", self.state / n) for n in SETTINGS if (self.state / n).is_file()]
        return found

    def _archive(self, backup_id):
        return self.dir / f"{backup_id}.zip"

    # --- creating --------------------------------------------------------------------------------------------------

    def _make(self, kind, source):
        if kind not in {k for k, _, _ in KINDS}:
            raise HostingError("Choose what to back up.")
        members = self._members(kind)
        if sum(p.stat().st_size for _, p in members) > MAX_BYTES:
            raise HostingError("This is too large to back up here. Remove files you do not need and try again.")
        backup_id = uuid.uuid4().hex[:12]
        self.dir.mkdir(parents=True, exist_ok=True)
        target = self._archive(backup_id)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("MANIFEST.json", json.dumps({"kind": kind, "files": len(members)}))
            for name, path in members:
                archive.write(path, name)
        rows = self._index()
        rows.insert(0, {"id": backup_id, "kind": kind, "created_at": _now().isoformat(), "size": target.stat().st_size,
                        "sha256": _sha256(target), "source": source})
        for old in rows[MAX_BACKUPS:]:  # retention: the newest are kept
            self._archive(old["id"]).unlink(missing_ok=True)
        self._save_index(rows[:MAX_BACKUPS])
        return backup_id

    def create(self, kind):
        return self._make(kind, "manual")

    def _run_schedule(self):
        data = self._schedule()
        period = PERIOD.get(data["schedule"])
        if not period:
            return
        last = datetime.fromisoformat(data["last"]) if data.get("last") else None
        if last is None or _now() - last >= period:
            self._make("full", "scheduled")
            data["last"] = _now().isoformat()
            self.schedule_file.write_text(json.dumps(data), encoding="utf-8")

    def list(self):
        self._run_schedule()
        return [self._info(r) for r in self._index()]

    def set_schedule(self, schedule):
        if schedule not in dict(SCHEDULES):
            raise HostingError("Choose off, every day or every week.")
        data = self._schedule()
        data["schedule"] = schedule
        if schedule != "off" and not data.get("last"):
            data["last"] = _now().isoformat()  # the first scheduled backup comes one period from now
        self.state.mkdir(parents=True, exist_ok=True)
        self.schedule_file.write_text(json.dumps(data), encoding="utf-8")

    # --- checking and removing -------------------------------------------------------------------------------------

    def _check(self, row):
        """None when the archive is sound, otherwise a plain reason."""
        path = self._archive(row["id"])
        if not path.is_file():
            return "The backup file is missing."
        if _sha256(path) != row["sha256"]:
            return "The backup file has changed since it was made."
        try:
            with zipfile.ZipFile(path) as archive:
                if archive.testzip() is not None:
                    return "The backup file is damaged."
                names = archive.namelist()
                if any(not _safe(n) for n in names):
                    return "The backup holds unsafe file names."
                for name in names:
                    if name.endswith(".json"):
                        json.loads(archive.read(name))
        except (zipfile.BadZipFile, ValueError, OSError):
            return "The backup file cannot be read."
        return None

    def verify(self, backup_id):
        rows = self._index()
        row = self._find(rows, backup_id)
        problem = self._check(row)
        row["verified"], row["verified_at"] = ("failed" if problem else "ok"), _now().isoformat()
        self._save_index(rows)
        if problem:
            raise HostingError(f"{problem} Do not rely on this backup; take a new one.")
        return self._info(row)

    def delete(self, backup_id):
        rows = self._index()
        self._find(rows, backup_id)
        self._archive(backup_id).unlink(missing_ok=True)
        self._save_index([r for r in rows if r["id"] != backup_id])

    # --- restoring -------------------------------------------------------------------------------------------------

    def restore(self, backup_id):
        row = self._find(self._index(), backup_id)
        problem = self._check(row)
        if problem:
            raise HostingError(f"{problem} It was not restored and nothing was changed.")
        kind = row["kind"]
        self._make(kind, "before restore")  # a way back if the restore turns out to be a mistake
        with zipfile.ZipFile(self._archive(backup_id)) as archive:
            names = [n for n in archive.namelist() if n != "MANIFEST.json"]
            if kind in ("files", "full"):
                self._clear_site()
                for name in (n for n in names if n.startswith("files/")):
                    self._extract(archive, name, self.site / name[len("files/"):])
            if kind in ("database", "full"):
                (self.state / "databases.json").unlink(missing_ok=True)
                shutil.rmtree(self.state / "data", ignore_errors=True)
                for name in (n for n in names if n == "state/databases.json" or n.startswith("state/data/")):
                    self._extract(archive, name, self.state / name[len("state/"):])
            if kind in ("email", "full"):
                (self.state / "email.json").unlink(missing_ok=True)
                if "state/email.json" in names:
                    self._extract(archive, "state/email.json", self.state / "email.json")
            if kind == "full":
                for setting in SETTINGS:
                    (self.state / setting).unlink(missing_ok=True)
                    if f"state/{setting}" in names:
                        self._extract(archive, f"state/{setting}", self.state / setting)

    def _clear_site(self):
        for child in list(self.site.iterdir()) if self.site.exists() else []:
            if child.name == TRASH:
                continue
            shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()

    def _extract(self, archive, name, destination):
        if not _safe(name):
            raise HostingError("The backup holds an unsafe file name, so it was not restored.")
        root = (self.site if name.startswith("files/") else self.state).resolve()
        target = destination.resolve()
        if target != root and root not in target.parents:
            raise HostingError("The backup holds a file outside your account, so it was not restored.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read(name))
