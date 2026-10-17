"""
The Storage and backups page: what is stored on this server for the panel, and the power to back it up, restore it and delete from it.

* **Backups**: the panel's own encrypted database backups (made by the schedule or "Back up now"), and the plain database dumps taken before each deploy (in a server folder mounted
  into the app). List, download, delete one or many, and **restore** (Super Admin).
* **Documents**: files customers uploaded (ticket attachments, proofs of payment). Deleting one removes the file and keeps its record, so a conversation still shows that a file once
  was there. Files on disk that no record points to ("unreferenced") can be listed and deleted too.
* **The server**: disk, database size and the size of each folder, so growth is visible.

Rules: everything needs ``manage_settings`` to change; the plain dumps (a full copy of every table, readable by anyone who holds the file) and the restore are Super Admin only; every
action is audited; a file name is only ever matched against a fixed pattern inside its own folder, never joined from what the browser sends.
"""
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.db import connection
from rest_framework import status

from apps.audit import services as audit
from apps.core.exceptions import ServiceError

PANEL_NAME = re.compile(r"^panel-(postgresql|sqlite)-\d{8}T\d{6}Z(-full)?\.bak\.enc$")
HOST_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.sql\.gz$")


def _denied(message="You do not have permission to do this."):
    return ServiceError(message, code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)


def require_manage(actor):
    if not actor.has_perm("accounts.manage_settings"):
        raise _denied()


def require_super(actor):
    if not actor.is_superuser:
        raise _denied("Only a Super Admin can do this.")


# --- numbers ------------------------------------------------------------------------------------------------

def _size(path):
    try:
        return path.stat().st_size
    except OSError:
        return 0


def folder_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            total += _size(Path(root) / name)
    return total


def disk():
    usage = shutil.disk_usage(settings.BASE_DIR)
    return {"total": usage.total, "used": usage.used, "free": usage.free, "percent": round(usage.used * 100 / usage.total) if usage.total else 0}


def database_size():
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_database_size(current_database())")
            return cursor.fetchone()[0]
    return _size(Path(str(connection.settings_dict.get("NAME") or "")))


def overview():
    """The sizes, for the top of the page: [(label, bytes)]."""
    private = Path(settings.PRIVATE_MEDIA_ROOT)
    media = Path(settings.MEDIA_ROOT)
    backups = Path(settings.PANEL_BACKUP_DIR)
    rows = [("Database", database_size()), ("Panel backups", folder_size(backups) if backups.exists() else 0),
            ("Documents (attachments and proofs)", folder_size(private) if private.exists() else 0), ("Public media", folder_size(media) if media.exists() else 0)]
    host = host_dir()
    if host is not None:
        rows.insert(2, ("Backups taken before deploys", folder_size(host)))
    return rows


# --- backups ------------------------------------------------------------------------------------------------

def host_dir():
    value = getattr(settings, "HOST_BACKUP_DIR", "") or ""
    path = Path(value) if value else None
    return path if path is not None and path.is_dir() else None


def _listing(folder, pattern):
    if folder is None or not Path(folder).is_dir():
        return []
    out = []
    for file in Path(folder).iterdir():
        if file.is_file() and pattern.match(file.name):
            info = file.stat()
            out.append({"name": file.name, "size": info.st_size, "modified": datetime.fromtimestamp(info.st_mtime, timezone.utc), "full": file.name.endswith("-full.bak.enc")})
    return sorted(out, key=lambda row: row["modified"], reverse=True)


def panel_backups():
    return _listing(settings.PANEL_BACKUP_DIR, PANEL_NAME)


def host_dumps():
    return _listing(host_dir(), HOST_NAME)


def resolve(kind, name):
    """The real file for a backup kind ("panel" or "host") and a name; only a name in that folder's own pattern is accepted."""
    if kind == "panel":
        folder, pattern = Path(settings.PANEL_BACKUP_DIR), PANEL_NAME
    elif kind == "host":
        folder, pattern = host_dir(), HOST_NAME
    else:
        raise ServiceError("Unknown backup kind.", code="invalid_kind")
    if folder is None or not pattern.match(name or ""):
        raise ServiceError("That backup was not found.", code="not_found", status_code=status.HTTP_404_NOT_FOUND)
    path = folder / name
    if not path.is_file() or path.resolve().parent != folder.resolve():
        raise ServiceError("That backup was not found.", code="not_found", status_code=status.HTTP_404_NOT_FOUND)
    return path


def authorise_download(actor, kind):
    require_manage(actor)
    if kind == "host":
        require_super(actor)  # a plain dump is every table, readable by anyone who holds the file


def delete_backups(actor, items, *, request=None):
    """``items`` = [(kind, name)]. Returns how many were deleted."""
    require_manage(actor)
    count = 0
    for kind, name in items:
        if kind == "host":
            require_super(actor)
        path = resolve(kind, name)
        size = _size(path)
        path.unlink()
        audit.record("storage.backup_deleted", actor=actor, metadata={"kind": kind, "name": name, "bytes": size}, request=request)
        count += 1
    return count


def backup_now(actor, *, request=None):
    require_manage(actor)
    from apps.core.management.commands import backup_panel

    try:
        target, text = backup_panel.run_backup()
    except Exception as exc:  # noqa: BLE001 - shown as a message
        raise ServiceError(f"The backup failed: {str(exc)[:200]}", code="backup_failed") from exc
    audit.record("storage.backup_made", actor=actor, metadata={"name": target.name, "copy": text}, request=request)
    return target, text


def start_restore(actor, name, confirm, *, request=None):
    """Super Admin: restore the panel's database from one of its own backups (in the background). The word RESTORE must be typed."""
    require_super(actor)
    if (confirm or "").strip() != "RESTORE":
        raise ServiceError("Type the word RESTORE to confirm.", code="confirm_required")
    path = resolve("panel", name)
    from apps.core.tasks import restore_panel_task

    audit.record("storage.restore_requested", actor=actor, metadata={"backup": path.name}, request=request)  # this row will itself be replaced by the restore: a new one is written after it
    restore_panel_task.delay(path.name, actor.pk)
    return path.name


def upload_backup(actor, upload, *, request=None):
    """Super Admin: put a backup file that was downloaded earlier (or from another server) into the list, so it can be restored. The name must be the one the panel gave it, and it must decrypt and read back."""
    require_super(actor)
    from apps.core.management.commands import backup_panel

    name = os.path.basename(getattr(upload, "name", "") or "")
    if not PANEL_NAME.match(name):
        raise ServiceError("Choose a backup file as the panel named it (panel-...bak.enc).", code="invalid_name")
    folder = Path(settings.PANEL_BACKUP_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    if target.exists():
        raise ServiceError("A backup with that name is already in the list.", code="exists")
    with open(target, "wb") as handle:
        for chunk in upload.chunks():
            handle.write(chunk)
    try:
        backup_panel.verify_backup(target)
    except Exception as exc:  # noqa: BLE001 - not a readable backup of this panel's key: remove it again
        target.unlink(missing_ok=True)
        raise ServiceError(f"That file cannot be read as a backup of this panel ({exc}).", code="unreadable") from exc
    audit.record("storage.backup_uploaded", actor=actor, metadata={"name": name, "bytes": _size(target)}, request=request)
    return name


# --- public media -------------------------------------------------------------------------------------------

def media_files():
    """Files in the public media folder: [{path, size, modified}], newest first."""
    root = Path(settings.MEDIA_ROOT)
    if not root.is_dir():
        return []
    out = []
    for file in root.rglob("*"):
        if file.is_file():
            info = file.stat()
            out.append({"path": file.relative_to(root).as_posix(), "size": info.st_size, "modified": datetime.fromtimestamp(info.st_mtime, timezone.utc)})
    return sorted(out, key=lambda r: r["modified"], reverse=True)


def delete_media(actor, paths, *, request=None):
    """Delete public media files by their path under the media folder (one or many). Returns how many were deleted."""
    require_manage(actor)
    known = {row["path"] for row in media_files()}
    root = Path(settings.MEDIA_ROOT).resolve()
    count = 0
    for rel in paths:
        if rel not in known:
            continue
        target = (Path(settings.MEDIA_ROOT) / rel).resolve()
        if root not in target.parents or not target.is_file():
            continue
        target.unlink()
        audit.record("storage.media_deleted", actor=actor, metadata={"file": os.path.basename(rel)}, request=request)
        count += 1
    return count


# --- documents ----------------------------------------------------------------------------------------------

def _private_root():
    return Path(settings.PRIVATE_MEDIA_ROOT)


def documents():
    """Every stored customer file: [{key, kind, name, size, created, where, url}], newest first."""
    from django.urls import reverse

    from apps.billing.models import Transaction
    from apps.support.models import TicketAttachment

    rows = []
    for a in TicketAttachment.objects.exclude(file="").select_related("message__ticket"):
        ticket = a.message.ticket
        rows.append({"key": f"attachment:{a.pk}", "kind": "Ticket attachment", "name": a.original_name, "size": a.size, "created": a.created_at,
                     "where": f"Ticket {ticket.reference}", "url": reverse("support_staff:ticket", args=[ticket.pk])})
    for t in Transaction.objects.exclude(proof="").select_related("invoice"):
        rows.append({"key": f"proof:{t.pk}", "kind": "Payment proof", "name": t.proof_name or "proof", "size": _size(_private_root() / t.proof.name) if t.proof.name else 0, "created": t.created_at,
                     "where": f"Invoice {t.invoice.reference}", "url": reverse("billing_staff:invoice_detail", args=[t.invoice_id])})
    return sorted(rows, key=lambda r: r["created"], reverse=True)


def delete_documents(actor, keys, *, request=None):
    """Delete the files behind ``keys`` ("attachment:12", "proof:5"); the records stay. Returns how many files were removed."""
    require_manage(actor)
    from apps.billing.models import Transaction
    from apps.support.models import TicketAttachment

    count = 0
    for key in keys:
        kind, _sep, raw = (key or "").partition(":")
        if not raw.isdigit():
            continue
        if kind == "attachment":
            row = TicketAttachment.objects.filter(pk=int(raw)).exclude(file="").first()
        elif kind == "proof":
            row = Transaction.objects.filter(pk=int(raw)).exclude(proof="").first()
        else:
            continue
        if row is None:
            continue
        field = row.file if kind == "attachment" else row.proof
        name = field.name
        field.delete(save=False)
        if kind == "attachment":
            row.file = ""
            row.save(update_fields=["file", "updated_at"])
        else:
            row.proof = ""
            row.save(update_fields=["proof", "updated_at"])
        audit.record("storage.document_deleted", actor=actor, metadata={"kind": kind, "id": int(raw), "file": os.path.basename(name)}, request=request)
        count += 1
    return count


def unreferenced():
    """Files in the private folder that no record points to: [{path, size, modified}]."""
    from apps.billing.models import Transaction
    from apps.support.models import TicketAttachment

    root = _private_root()
    if not root.is_dir():
        return []
    known = set(TicketAttachment.objects.exclude(file="").values_list("file", flat=True)) | set(Transaction.objects.exclude(proof="").values_list("proof", flat=True))
    known = {k.replace("\\", "/") for k in known}
    found = []
    for file in root.rglob("*"):
        if file.is_file():
            rel = file.relative_to(root).as_posix()
            if rel not in known:
                info = file.stat()
                found.append({"path": rel, "size": info.st_size, "modified": datetime.fromtimestamp(info.st_mtime, timezone.utc)})
    return sorted(found, key=lambda r: r["modified"], reverse=True)


def delete_unreferenced(actor, paths, *, request=None):
    require_manage(actor)
    allowed = {row["path"] for row in unreferenced()}
    root = _private_root().resolve()
    count = 0
    for rel in paths:
        if rel not in allowed:
            continue  # only files that really are unreferenced, and only inside the private folder
        target = (_private_root() / rel).resolve()
        if root not in target.parents or not target.is_file():
            continue
        target.unlink()
        audit.record("storage.unreferenced_deleted", actor=actor, metadata={"file": os.path.basename(rel)}, request=request)
        count += 1
    return count
