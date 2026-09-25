"""
Validation of files a person uploads to a ticket.

A ticket attachment is untrusted input from the outside world, so it is checked before anything is written:

* an allow-list of extensions (images, PDF and plain text - nothing executable, no HTML/SVG that a browser would run);
* the file's *content* must match its extension (a ``.png`` must start like a PNG), because a name proves nothing;
* size limits per file and per message, and no empty files;
* the content type stored is the one *we* derive from the extension, never the one the browser claimed.

Stored files are served only as downloads (``Content-Disposition: attachment``, ``nosniff``) by an authenticated view.
"""
import re
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError

MAX_FILES = 5

RIFF, WEBP = b"RIFF", b"WEBP"
# extension -> (content type, signature check)
ALLOWED = {
    ".png": ("image/png", lambda head: head.startswith(b"\x89PNG\r\n\x1a\n")),
    ".jpg": ("image/jpeg", lambda head: head.startswith(b"\xff\xd8\xff")),
    ".jpeg": ("image/jpeg", lambda head: head.startswith(b"\xff\xd8\xff")),
    ".gif": ("image/gif", lambda head: head.startswith((b"GIF87a", b"GIF89a"))),
    ".webp": ("image/webp", lambda head: head[:4] == RIFF and head[8:12] == WEBP),
    ".pdf": ("application/pdf", lambda head: head.startswith(b"%PDF-")),
    ".txt": ("text/plain", lambda head: b"\x00" not in head),
    ".log": ("text/plain", lambda head: b"\x00" not in head),
    ".csv": ("text/plain", lambda head: b"\x00" not in head),
}


@dataclass
class CheckedUpload:
    file: object
    name: str
    content_type: str
    size: int


def max_bytes():
    return int(settings.SUPPORT_MAX_ATTACHMENT_MB) * 1024 * 1024


def clean_name(raw):
    """A safe display name: no path, no control or odd characters, bounded length; the extension is kept."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[^\w.\- ()]", "_", name, flags=re.UNICODE).strip(" .")
    stem, dot, ext = name.rpartition(".")
    if not dot:
        return name[:100]
    return f"{stem[:100 - len(ext) - 1]}.{ext}"[:100]


def check_uploads(files):
    """Validate a batch; returns ``CheckedUpload`` items or raises ``ValidationError`` naming the first problem."""
    files = [f for f in (files or []) if f]
    if len(files) > MAX_FILES:
        raise ValidationError(f"You can attach at most {MAX_FILES} files to one message.")
    limit, total, checked = max_bytes(), 0, []
    for upload in files:
        name = clean_name(getattr(upload, "name", ""))
        extension = "." + name.rsplit(".", 1)[1].lower() if "." in name else ""
        if extension not in ALLOWED:
            allowed = ", ".join(sorted(e.lstrip(".") for e in ALLOWED))
            raise ValidationError(f"'{name or 'file'}' is not an allowed file type. Allowed: {allowed}.")
        size = upload.size
        if size == 0:
            raise ValidationError(f"'{name}' is empty.")
        if size > limit:
            raise ValidationError(f"'{name}' is larger than {settings.SUPPORT_MAX_ATTACHMENT_MB} MB.")
        total += size
        if total > limit * MAX_FILES:
            raise ValidationError("The attachments are too large in total.")
        upload.seek(0)
        head = upload.read(8192)
        upload.seek(0)
        content_type, signature_ok = ALLOWED[extension]
        if not signature_ok(head):
            raise ValidationError(f"'{name}' does not look like a valid {extension.lstrip('.').upper()} file.")
        checked.append(CheckedUpload(file=upload, name=name, content_type=content_type, size=size))
    return checked
