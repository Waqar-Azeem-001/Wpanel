"""
Backups on native hosting (N8): the same ``BackupsCapability`` the customer's Backups screen already uses, stored as encrypted restic snapshots in the node's backup bucket (ADR 10).

A backup is started and shows as "running" until the node reports back; WHPanel's own register (``NativeBackup``) is what the screen lists. "Full" is the web node's whole account (files,
databases, settings) plus the mail node's part when the account uses mail. A restore is only offered after the backup passes its check, takes a safety copy of what it replaces (listed as
"before restore") and is carried out in the background.
"""
import uuid

from django.utils import timezone

from apps.agent import services as agent
from apps.core.exceptions import ServiceError

from .base import HostingError
from .capabilities import BackupInfo, BackupOptions, BackupsCapability

SCHEDULES = [("off", "Off"), ("daily", "Every day"), ("weekly", "Every week")]


def kinds(has_mail):
    base = [("files", "Website files", "Everything in your website folder."), ("database", "Databases", "All databases, their tables and users.")]
    if has_mail:
        base.append(("email", "Email", "Mailboxes and messages (kept on the mail server)."))
    base.append(("full", "Full account", "Files, databases and settings" + (", and email." if has_mail else ".")))
    return base


def _iso(value):
    return value.isoformat() if value else None


class NativeBackups(BackupsCapability):
    def _repo_ready(self, node_server, what):
        from apps.agent.models import AgentNode

        node = AgentNode.objects.filter(server=node_server).first()
        repo = getattr(node, "backup_repository", None) if node else None
        if repo is None or not repo.applied_ok:
            raise HostingError(f"Backups are not set up on the {what} server yet. Please contact support.")
        return node

    def _records(self, username, group=None):
        from apps.hosting.models import NativeBackup

        account = self._account(username)
        rows = NativeBackup.objects.filter(account=account).exclude(status__in=("deleted", "deleting")).select_related("node")
        return rows.filter(group=group) if group else rows

    def _info(self, rows):
        first = sorted(rows, key=lambda r: r.created_at)[0]
        statuses = {r.status for r in rows}
        status = "running" if "running" in statuses else "failed" if "failed" in statuses else "complete"
        complete = [r for r in rows if r.status == "complete"]
        verified = "failed" if any(r.verified == "failed" for r in complete) else "ok" if complete and all(r.verified == "ok" for r in complete) else ""
        times = [r.verified_at for r in complete if r.verified_at]
        return BackupInfo(backup_id=first.group, kind=first.requested_kind, created_at=_iso(first.created_at), size_mb=round(sum(r.size_mb or 0 for r in rows), 2) if complete else None,
                          status=status, source=first.source, verified=verified, verified_at=_iso(min(times)) if times and verified == "ok" else None, offsite="ok" if complete else "")

    def _groups(self, username):
        groups = {}
        for rec in self._records(username):
            groups.setdefault(rec.group, []).append(rec)
        return sorted(groups.values(), key=lambda rows: max(r.created_at for r in rows), reverse=True)

    def backup_options(self, username):
        from apps.hosting.native_backups import max_backups
        from apps.hosting.models import NativeBackupSettings

        account = self._account(username)
        settings_row = NativeBackupSettings.objects.filter(account=account).first()
        return BackupOptions(kinds=kinds(bool(account.mail_server_id)), max_backups=max_backups(account), schedules=list(SCHEDULES), schedule=settings_row.schedule if settings_row else "off",
                             last_scheduled_at=_iso(settings_row.last_scheduled_at) if settings_row else None, offsite=True)

    def list_backups(self, username):
        return [self._info(rows) for rows in self._groups(username)]

    def create_backup(self, username, kind, source="manual"):
        from apps.hosting.models import NativeBackup

        account = self._account(username)
        if kind not in ("files", "database", "email", "full"):
            raise HostingError("Choose what to back up.")
        if kind == "email" and not account.mail_server_id:
            raise HostingError("This account does not use email here.")
        parts = [("mail", "email")] if kind == "email" else [("web", kind)] + ([("mail", "email")] if kind == "full" and account.mail_server_id else [])
        group, made = uuid.uuid4().hex[:12], []
        for part, part_kind in parts:
            server = account.mail_server if part == "mail" else self.server
            rec = NativeBackup.objects.create(account=account, group=group, part=part, kind=part_kind, requested_kind=kind, source=source)
            try:
                node = self._repo_ready(server, "mail" if part == "mail" else "hosting")
                rec.node = node
                rec.save(update_fields=["node"])
                agent.dispatch(None, server, "BACKUP_RUN", {"account": username, "kind": part_kind, "source": source}, subject=f"backup:{rec.pk}")
            except (HostingError, ServiceError) as exc:
                rec.status, rec.error = "failed", (exc.message if isinstance(exc, ServiceError) else str(exc))[:300]
                rec.save(update_fields=["status", "error"])
            made.append(rec)
        if all(r.status == "failed" for r in made):
            raise HostingError(made[0].error or "The backup could not be started.")
        return group

    def verify_backup(self, username, backup_id):
        rows = [r for r in self._records(username, backup_id) if r.status == "complete"]
        if not rows:
            raise HostingError("That backup is not available.")
        for rec in rows:
            try:
                agent.call(rec.node.server, "BACKUP_VERIFY", {"account": username, "snapshot_id": rec.snapshot_id}, wait=300)
                rec.verified = "ok"
            except agent.CommandFailed as exc:
                rec.verified, rec.error = "failed", exc.message[:300]
            except ServiceError as exc:
                raise HostingError(exc.message) from exc
            rec.verified_at = timezone.now()
            rec.save(update_fields=["verified", "verified_at", "error"])
        return self._info(list(self._records(username, backup_id)))

    def restore_backup(self, username, backup_id):
        rows = [r for r in self._records(username, backup_id) if r.status == "complete"]
        if not rows:
            raise HostingError("That backup is not available.")
        info = self.verify_backup(username, backup_id)  # a restore is only offered for a backup that passes the check, now
        if info.verified != "ok":
            raise HostingError("This backup failed its check, so it was not restored. Choose another backup or contact support.")
        for rec in [r for r in self._records(username, backup_id) if r.status == "complete"]:
            try:
                agent.dispatch(None, rec.node.server, "BACKUP_RESTORE", {"account": username, "snapshot_id": rec.snapshot_id, "kind": rec.kind}, subject=f"backup:{rec.pk}")
            except ServiceError as exc:
                raise HostingError(exc.message) from exc

    def delete_backup(self, username, backup_id):
        rows = [r for r in self._records(username, backup_id) if r.status in ("complete", "failed")]
        if not rows:
            raise HostingError("That backup no longer exists.")
        for rec in rows:
            if rec.status == "failed" or not rec.snapshot_id:
                rec.status = "deleted"
                rec.save(update_fields=["status"])
                continue
            rec.status = "deleting"
            rec.save(update_fields=["status"])
            try:
                agent.dispatch(None, rec.node.server, "BACKUP_FORGET", {"account": username, "snapshot_id": rec.snapshot_id}, subject=f"backup:{rec.pk}")
            except ServiceError as exc:
                rec.status = "complete"
                rec.save(update_fields=["status"])
                raise HostingError(exc.message) from exc

    def set_backup_schedule(self, username, schedule):
        from apps.hosting.models import NativeBackupSettings

        if schedule not in dict(SCHEDULES):
            raise HostingError("Choose off, every day or every week.")
        NativeBackupSettings.objects.update_or_create(account=self._account(username), defaults={"schedule": schedule})

    def set_backup_offsite(self, username, enabled):
        if not enabled:
            raise HostingError("Native backups are always kept away from the server; that cannot be switched off.")
