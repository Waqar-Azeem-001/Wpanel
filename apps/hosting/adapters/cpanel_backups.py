"""
Backups on a cPanel server: UAPI ``Backup`` can list the account's backups and start a full one into its home folder. cPanel cannot check a backup's
integrity or restore a whole account from a customer login, so those are honestly not offered here: use "Open cPanel" (the transitional tool) to restore.
"""
import re
from datetime import datetime, timezone

from .base import HostingError
from .capabilities import BackupInfo, BackupOptions, BackupsCapability
from .cpanel_api import CpanelApi

NAME = re.compile(r"^backup-(\d{1,2})\.(\d{1,2})\.(\d{4})_(\d{2})-(\d{2})-(\d{2})_[A-Za-z0-9_.-]+\.tar\.gz$")


def _when(name):
    match = NAME.match(name)
    if not match:
        return None
    month, day, year, hour, minute, second = (int(x) for x in match.groups())
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


class CpanelBackups(CpanelApi, BackupsCapability):
    def backup_options(self, username):
        return BackupOptions(kinds=[("full", "Full account", "Everything: files, databases, email and settings, made as one archive in your home folder.")],
                             max_backups=0, schedules=[("off", "Off")], schedule="off", offsite=False)

    def list_backups(self, username):
        found = []
        for item in self._uapi(username, "Backup", "list_backups") or []:
            name = str(item.get("file") if isinstance(item, dict) else item or "")
            when = _when(name)
            if not name or when is None:
                continue
            size = item.get("size") if isinstance(item, dict) else None
            found.append(BackupInfo(backup_id=name, kind="full", created_at=when, size_mb=round(int(size) / 1048576, 2) if str(size).isdigit() else None))
        return sorted(found, key=lambda b: b.created_at, reverse=True)

    def create_backup(self, username, kind):
        if kind != "full":
            raise HostingError("Only a full account backup can be made on this server.")
        self._uapi(username, "Backup", "fullbackup_to_homedir", {"email": ""})
        return ""

    def verify_backup(self, username, backup_id):
        self._unsupported("Testing a backup")

    def restore_backup(self, username, backup_id):
        self._unsupported("Restoring a backup")

    def delete_backup(self, username, backup_id):
        self._unsupported("Deleting a backup")

    def set_backup_schedule(self, username, schedule):
        self._unsupported("Scheduled backups")

    def set_backup_offsite(self, username, enabled):
        self._unsupported("Off-server copies")
