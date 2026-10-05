"""
The customer's File Manager. Every operation goes through the hosting-control gateway (``apps.hosting.control``), so
ownership, the allowlist, the live-account check and the audit trail are the same as for every other capability; the
provider's sandbox keeps every path inside the website's folder. Nothing here touches a disk or a server directly.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_POST

from apps.core import portal
from apps.core.exceptions import ServiceError

from . import control, dashboard, files
from .adapters.base import HostingError
from .views import _own_account


def _files_url(account, path="", **query):
    url = reverse("hosting_customer:files", args=[account.pk])
    params = {k: v for k, v in {"path": path, **query}.items() if v}
    if params:
        from urllib.parse import urlencode

        url += "?" + urlencode(params)
    return url


def _safe_path(raw):
    try:
        return files.normalise(raw), None
    except HostingError as exc:
        return "", str(exc)


def _page(request, account, template, **extra):
    return render(request, template, {"account": account, "sidebar": portal.service_sidebar(request, account), **extra})


def _supports_files(account):
    try:
        return "files" in control.capabilities_for(account)
    except ServiceError:
        return False


@login_required
def my_hosting_files(request, pk):
    account = _own_account(request, pk)
    if not _supports_files(account):
        return _page(request, account, "hosting/customer/section.html",
                     section=dashboard.section_page(request.user, account, "files"))
    path, problem = _safe_path(request.GET.get("path", ""))
    query = request.GET.get("q", "").strip()
    rows, error = [], problem
    if problem is None:
        try:
            if query:
                entries = control.perform(request.user, account, "files", "search_files", path, query)
            else:
                entries = control.perform(request.user, account, "files", "list_files", path)
        except ServiceError as exc:
            error = exc.message
        else:
            rows = [{"entry": e, "editable": not e.is_dir and files.is_editable_name(e.name)
                     and (e.size_bytes or 0) <= files.MAX_EDIT_BYTES, "folder": files.parent_of(e.path)} for e in entries]
    if error:
        messages.error(request, error)
    return _page(request, account, "hosting/customer/files.html", path=path, query=query, rows=rows, failed=bool(error),
                 crumbs=files.breadcrumbs(path), up_url=_files_url(account, files.parent_of(path)) if path else "",
                 max_upload_mb=files.MAX_UPLOAD_BYTES // 1_000_000)


@require_POST
@login_required
def my_hosting_file_action(request, pk):
    """Create a folder or file, upload, rename, copy or delete; always returns to the folder it happened in."""
    account = _own_account(request, pk)
    action = request.POST.get("action", "")
    folder, problem = _safe_path(request.POST.get("path", ""))
    if problem:
        messages.error(request, problem)
        return redirect(_files_url(account))
    back = _files_url(account, folder)
    try:
        if action == "mkdir":
            target = files.join(folder, request.POST.get("name", "").strip())
            control.perform(request.user, account, "files", "make_directory", target, request=request,
                            audit_metadata={"path": target})
            messages.success(request, "Folder created.")
        elif action == "new_file":
            target = files.join(folder, request.POST.get("name", "").strip())
            control.perform(request.user, account, "files", "write_file", target, b"", request=request,
                            audit_metadata={"path": target, "new": True})
            messages.success(request, "File created.")
            return redirect(reverse("hosting_customer:file_edit", args=[account.pk]) + "?path=" + _quote(target))
        elif action == "upload":
            upload = request.FILES.get("file")
            if upload is None:
                raise ServiceError("Choose a file to upload.", code="no_file")
            if upload.size > files.MAX_UPLOAD_BYTES:
                raise ServiceError(f"That file is larger than {files.MAX_UPLOAD_BYTES // 1_000_000} MB.", code="too_large")
            target = files.join(folder, upload.name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1])
            control.perform(request.user, account, "files", "write_file", target, upload.read(), request=request,
                            audit_metadata={"path": target, "bytes": upload.size})
            messages.success(request, "File uploaded.")
        elif action in ("rename", "copy"):
            source, problem = _safe_path(request.POST.get("item", ""))
            if problem or not source:
                raise ServiceError(problem or "Choose what to change.", code="bad_path")
            destination = files.join(files.parent_of(source), request.POST.get("new_name", "").strip())
            operation = "rename_path" if action == "rename" else "copy_path"
            control.perform(request.user, account, "files", operation, source, destination, request=request,
                            audit_metadata={"path": source, "to": destination})
            messages.success(request, "Renamed." if action == "rename" else "Copied.")
        elif action == "delete":
            source, problem = _safe_path(request.POST.get("item", ""))
            if problem or not source:
                raise ServiceError(problem or "Choose what to delete.", code="bad_path")
            control.perform(request.user, account, "files", "delete_path", source, request=request,
                            audit_metadata={"path": source})
            messages.success(request, "Deleted.")
        else:
            raise ServiceError("That action is not available.", code="unknown_action")
    except ServiceError as exc:
        messages.error(request, exc.message)
    return redirect(back)


def _quote(value):
    from urllib.parse import quote

    return quote(value, safe="/")


@login_required
def my_hosting_file_edit(request, pk):
    account = _own_account(request, pk)
    path, problem = _safe_path(request.GET.get("path") or request.POST.get("path", ""))
    if problem or not path:
        messages.error(request, problem or "Choose a file to edit.")
        return redirect(_files_url(account))
    folder_url = _files_url(account, files.parent_of(path))
    if request.method == "POST":
        content = request.POST.get("content", "").replace("\r\n", "\n").encode("utf-8")
        try:
            if len(content) > files.MAX_EDIT_BYTES:
                raise ServiceError("That file is too large to save from the editor.", code="too_large")
            control.perform(request.user, account, "files", "write_file", path, content, overwrite=True,
                            request=request, audit_metadata={"path": path, "bytes": len(content)})
        except ServiceError as exc:
            messages.error(request, exc.message)
            text = request.POST.get("content", "")
        else:
            messages.success(request, "Saved.")
            return redirect(reverse("hosting_customer:file_edit", args=[account.pk]) + "?path=" + _quote(path))
    else:
        try:
            data = control.perform(request.user, account, "files", "read_file", path, max_bytes=files.MAX_EDIT_BYTES)
        except ServiceError as exc:
            messages.error(request, exc.message)
            return redirect(folder_url)
        text = files.decode_text(data)
        if text is None:
            messages.error(request, "That file is not text, so it cannot be edited here. Download it instead.")
            return redirect(folder_url)
    return _page(request, account, "hosting/customer/file_edit.html", path=path, name=path.rsplit("/", 1)[-1], text=text,
                 folder_url=folder_url, crumbs=files.breadcrumbs(files.parent_of(path)))


@login_required
def my_hosting_file_download(request, pk):
    account = _own_account(request, pk)
    path, problem = _safe_path(request.GET.get("path", ""))
    if problem or not path:
        messages.error(request, problem or "Choose a file to download.")
        return redirect(_files_url(account))
    try:
        data = control.perform(request.user, account, "files", "read_file", path, max_bytes=files.MAX_DOWNLOAD_BYTES)
    except ServiceError as exc:
        messages.error(request, exc.message)
        return redirect(_files_url(account, files.parent_of(path)))
    response = HttpResponse(data, content_type="application/octet-stream")
    response["Content-Disposition"] = content_disposition_header(True, path.rsplit("/", 1)[-1])
    response["X-Content-Type-Options"] = "nosniff"
    return response
