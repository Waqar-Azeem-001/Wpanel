"""The Storage and backups page (Setup): sizes, backups (make, download, delete, restore) and customer documents (delete). Every rule is in ``storage``."""
from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.roles import perm
from apps.audit import services as audit
from apps.core.decorators import portal_permission_required
from apps.core.exceptions import ServiceError
from apps.core.web import ACTION_ERRORS, error_text

from . import storage

VIEW = perm("view_settings")
MANAGE = perm("manage_settings")


def _done(request, action):
    try:
        message = action()
    except ACTION_ERRORS as exc:
        messages.error(request, error_text(exc))
    else:
        if message:
            messages.success(request, message)
    return redirect("console:storage")


@portal_permission_required(VIEW)
def storage_page(request):
    user = request.user
    disk = storage.disk()
    from apps.core.management.commands import backup_panel

    return render(request, "console/storage.html", {
        "disk": disk, "overview": storage.overview(), "panel_backups": storage.panel_backups(), "host_dumps": storage.host_dumps() if user.is_superuser else [],
        "host_dir_set": storage.host_dir() is not None, "documents": storage.documents(), "unreferenced": storage.unreferenced(), "media": storage.media_files(),
        "can_manage": user.has_perm(MANAGE), "is_super": user.is_superuser,
        "keep": settings.PANEL_BACKUP_KEEP, "weekdays": settings.PANEL_BACKUP_WEEKDAYS, "offsite": backup_panel.offsite_configured()})


@require_POST
@portal_permission_required(MANAGE)
def backup_now(request):
    def run():
        target, text = storage.backup_now(request.user, request=request)
        return f"Backup made: {target.name} ({text})."

    return _done(request, run)


@require_POST
@portal_permission_required(MANAGE)
def backups_delete(request):
    items = []
    for value in request.POST.getlist("backups"):
        kind, _sep, name = value.partition(":")
        items.append((kind, name))
    if not items:
        messages.warning(request, "Tick at least one backup first.")
        return redirect("console:storage")
    return _done(request, lambda: f"{storage.delete_backups(request.user, items, request=request)} backup(s) deleted.")


@portal_permission_required(MANAGE)
def backup_download(request, kind, name):
    try:
        storage.authorise_download(request.user, kind)
        path = storage.resolve(kind, name)
    except ServiceError as exc:
        raise Http404(exc.message)
    audit.record("storage.backup_downloaded", actor=request.user, metadata={"kind": kind, "name": name}, request=request)
    response = FileResponse(open(path, "rb"), as_attachment=True, filename=name, content_type="application/octet-stream")
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


@require_POST
@portal_permission_required(MANAGE)
def restore(request):
    def run():
        name = storage.start_restore(request.user, request.POST.get("name", ""), request.POST.get("confirm", ""), request=request)
        return (f"The restore from {name} has started. A copy of the database as it is now is saved first. It takes a few seconds; you may be signed out when it finishes. "
                "The team is told when it is done.")

    return _done(request, run)


@require_POST
@portal_permission_required(MANAGE)
def restore_upload(request):
    """Restore from a backup file the person picks from their computer: it is added to the list (after it has been read back) and the restore starts."""
    def run():
        upload = request.FILES.get("backup")
        if upload is None:
            raise ServiceError("Choose a backup file first.", code="no_file")
        storage.require_super(request.user)
        if (request.POST.get("confirm") or "").strip() != "RESTORE":
            raise ServiceError("Type the word RESTORE to confirm.", code="confirm_required")
        name = storage.upload_backup(request.user, upload, request=request)
        storage.start_restore(request.user, name, request.POST.get("confirm", ""), request=request)
        return f"{name} was added and the restore has started. A copy of the current state is saved first."

    return _done(request, run)


@require_POST
@portal_permission_required(MANAGE)
def media_delete(request):
    paths = request.POST.getlist("media")
    if not paths:
        messages.warning(request, "Tick at least one file first.")
        return redirect("console:storage")
    return _done(request, lambda: f"{storage.delete_media(request.user, paths, request=request)} media file(s) deleted.")


@require_POST
@portal_permission_required(MANAGE)
def documents_delete(request):
    keys = request.POST.getlist("documents")
    if not keys:
        messages.warning(request, "Tick at least one document first.")
        return redirect("console:storage")
    return _done(request, lambda: f"{storage.delete_documents(request.user, keys, request=request)} document file(s) deleted. Their records stay.")


@require_POST
@portal_permission_required(MANAGE)
def unreferenced_delete(request):
    paths = request.POST.getlist("files")
    if not paths:
        messages.warning(request, "Tick at least one file first.")
        return redirect("console:storage")
    return _done(request, lambda: f"{storage.delete_unreferenced(request.user, paths, request=request)} file(s) deleted.")
