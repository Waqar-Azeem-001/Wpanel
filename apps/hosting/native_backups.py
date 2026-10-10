"""
Account-level backup work for native hosting (N8): what happens when a node reports on a backup, keeping only the newest few, the scheduled run, and the monthly test restore.

WHPanel's own register (``NativeBackup``) is the list customers see, so backups stay known even if a node is lost.
"""
import random
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from apps.audit import services as audit

from .models import HostingAccount, HostingStatus, NativeBackup, NativeBackupSettings

DEFAULT_MAX = 7
PERIOD = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}


def max_backups(account):
    from apps.products.models import Product

    product = Product.objects.filter(whm_package_name=account.package_name).first()
    value = (product.resource_limits or {}).get("backups") if product and isinstance(product.resource_limits, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else DEFAULT_MAX


def job_finished(job):
    try:
        record = NativeBackup.objects.select_related("account", "node").get(pk=int(job.subject.split(":", 1)[1]))
    except (ValueError, NativeBackup.DoesNotExist):
        return
    ok = job.result_ok is True
    message = (job.result_message or job.get_status_display())[:300]
    account = record.account
    from .control import _bump_cache_version

    _bump_cache_version(account)  # the screen asks again instead of showing a list remembered from before this result
    if job.command == "BACKUP_RUN":
        if ok:
            record.status, record.snapshot_id, record.size_mb, record.error = "complete", job.result_data.get("snapshot_id", ""), job.result_data.get("size_mb"), ""
            record.save(update_fields=["status", "snapshot_id", "size_mb", "error"])
            audit.record("hosting.backup_completed", target=account, metadata={"group": record.group, "part": record.part, "kind": record.kind, "source": record.source})
            enforce_retention(account)
        else:
            record.status, record.error = "failed", message
            record.save(update_fields=["status", "error"])
            audit.record("hosting.backup_failed", target=account, metadata={"group": record.group, "part": record.part, "error": message})
    elif job.command == "BACKUP_RESTORE":
        if ok:
            audit.record("hosting.backup_restored", target=account, metadata={"group": record.group, "part": record.part})
            safety = job.result_data.get("safety_snapshot_id")
            if safety:  # what the restore replaced is kept, and listed, so a mistake can be undone
                NativeBackup.objects.create(account=account, node=record.node, group=_new_group(), part=record.part, kind=record.kind, requested_kind=record.requested_kind,
                                            source="before restore", snapshot_id=safety, status="complete")
        else:
            account.last_error = f"A restore did not complete: {message}"
            account.save(update_fields=["last_error", "updated_at"])
            audit.record("hosting.backup_restore_failed", target=account, metadata={"group": record.group, "part": record.part, "error": message})
    elif job.command == "BACKUP_FORGET":
        if ok:
            record.status = "deleted"
        else:
            record.status, record.error = "complete", f"Could not be deleted: {message}"
            audit.record("hosting.backup_delete_failed", target=account, metadata={"group": record.group, "error": message})
        record.save(update_fields=["status", "error"])
    elif job.command == "BACKUP_TEST_RESTORE":
        record.restore_tested_at, record.restore_test_ok = timezone.now(), ok
        record.save(update_fields=["restore_tested_at", "restore_test_ok"])
        audit.record("hosting.backup_restore_test_" + ("passed" if ok else "failed"), target=account, metadata={"group": record.group, "part": record.part, "detail": message,
                                                                                                                     "files": job.result_data.get("files")})


def _new_group():
    import uuid

    return uuid.uuid4().hex[:12]


def enforce_retention(account):
    """Keep the newest ``max_backups`` complete groups (safety copies from restores are kept separately and never count); queue the removal of older ones."""
    from apps.agent import services as agent
    from apps.core.exceptions import ServiceError

    groups = []
    for rec in NativeBackup.objects.filter(account=account, status="complete").exclude(source="before restore"):
        if rec.group not in groups:
            groups.append(rec.group)  # newest first (the default ordering)
    for group in groups[max_backups(account):]:
        for rec in NativeBackup.objects.filter(account=account, group=group, status="complete"):
            rec.status = "deleting"
            rec.save(update_fields=["status"])
            try:
                agent.dispatch(None, rec.node.server, "BACKUP_FORGET", {"account": account.username, "snapshot_id": rec.snapshot_id}, subject=f"backup:{rec.pk}")
            except ServiceError as exc:
                rec.status, rec.error = "complete", exc.message[:300]
                rec.save(update_fields=["status", "error"])


def run_due_backups(now=None):
    """Called daily: start a full backup of every native account whose schedule says it is due. Returns the number started."""
    from .adapters.base import HostingError
    from .services import get_adapter

    now = now or timezone.now()
    started = 0
    for settings_row in NativeBackupSettings.objects.exclude(schedule="off").select_related("account__server"):
        account = settings_row.account
        period = PERIOD.get(settings_row.schedule)
        if period is None or account.status != HostingStatus.ACTIVE or account.server is None or account.server.kind != "native":
            continue
        if settings_row.last_scheduled_at and now - settings_row.last_scheduled_at < period - timedelta(hours=1):
            continue
        try:
            get_adapter(account.server).create_backup(account.username, "full", source="scheduled")
            started += 1
        except HostingError as exc:
            audit.record("hosting.scheduled_backup_failed", target=account, metadata={"error": str(exc)})
        settings_row.last_scheduled_at = now  # also after a failure: it is tried again at the next period, and the failure is in the audit trail
        settings_row.save(update_fields=["last_scheduled_at"])
    return started


def run_restore_tests(limit=5, rng=random):
    """Called monthly: restore a random recent backup of a few accounts into a scratch folder on the node and compare it with what was backed up. Returns the number started."""
    from apps.agent import services as agent
    from apps.core.exceptions import ServiceError

    candidates = list(NativeBackup.objects.filter(status="complete", part="web", source__in=["manual", "scheduled"]).exclude(snapshot_id="")
                      .values("account_id").annotate(n=Count("id")).filter(n__gte=1))
    rng.shuffle(candidates)
    started = 0
    for row in candidates[:limit]:
        rec = NativeBackup.objects.filter(account_id=row["account_id"], status="complete", part="web").exclude(snapshot_id="").order_by("-created_at").first()
        try:
            agent.dispatch(None, rec.node.server, "BACKUP_TEST_RESTORE", {"account": rec.account.username, "snapshot_id": rec.snapshot_id}, subject=f"backup:{rec.pk}")
            started += 1
        except ServiceError as exc:
            audit.record("hosting.backup_restore_test_failed", target=rec.account, metadata={"detail": exc.message})
    return started


_ = (HostingAccount, Q)
