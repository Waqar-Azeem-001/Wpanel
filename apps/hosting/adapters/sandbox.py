"""
A directory the code may work inside and never escape: the safety layer behind every file operation of a provider that
keeps an account's files on a disk we can reach (the demo provider today, a native node's agent later).

The rules, all enforced here and not left to callers:

* Every path is RELATIVE to the account's root. Absolute paths, drive letters, ``..`` parts, backslashes, NUL and other
  control characters are refused outright (not "cleaned up" into something that happens to be safe).
* The final location, after resolving, must still be inside the root, and no existing component along the way may be a
  symbolic link (a link could point anywhere).
* Names are plain names: no slash, not "." or "..", at most 255 characters.
* The root itself can be listed but never renamed, copied over or deleted.

Failures raise ``HostingError`` with a plain-language message, which the gateway turns into a normal error for the user.
"""
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .base import HostingError
from .capabilities import FileEntry

MAX_PATH_LENGTH = 1024
MAX_NAME_LENGTH = 255
MAX_SEARCH_RESULTS = 200
MAX_SEARCH_VISITED = 5000  # stop walking a huge tree: a search is a convenience, not a crawl


def clean_relative(rel) -> str:
    """The path as a normalised ``a/b/c`` string relative to the root ("" is the root), or HostingError."""
    rel = "" if rel is None else str(rel)
    if len(rel) > MAX_PATH_LENGTH:
        raise HostingError("That path is too long.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in rel):
        raise HostingError("That path contains characters that are not allowed.")
    if "\\" in rel or ":" in rel:
        raise HostingError("Use forward slashes and no drive letters in a path.")
    if rel.startswith("/"):
        raise HostingError("Paths are relative to your website's folder.")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise HostingError("A path cannot go up a folder.")
    if any(len(p) > MAX_NAME_LENGTH for p in parts):
        raise HostingError("A name in that path is too long.")
    return "/".join(parts)


def clean_name(name) -> str:
    name = "" if name is None else str(name).strip()
    if not name or name in (".", "..") or "/" in name or "\\" in name or ":" in name:
        raise HostingError("Enter a plain file or folder name (no slashes).")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name) or len(name) > MAX_NAME_LENGTH:
        raise HostingError("That name is not allowed.")
    return name


class Sandbox:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._real_root = self.root.resolve()

    # --- Resolving -------------------------------------------------------------------------------------------

    def resolve(self, rel, *, must_exist=True) -> Path:
        rel = clean_relative(rel)
        current = self._real_root
        for part in [p for p in rel.split("/") if p]:
            current = current / part
            if current.is_symlink():
                raise HostingError("Links are not followed inside your website's folder.")
        target = current.resolve(strict=False)
        if target != self._real_root and self._real_root not in target.parents:
            raise HostingError("That location is outside your website's folder.")
        if must_exist and not target.exists():
            raise HostingError("That file or folder does not exist.")
        return target

    def relative(self, path: Path) -> str:
        rel = path.resolve().relative_to(self._real_root).as_posix()
        return "" if rel == "." else rel

    def _entry(self, path: Path) -> FileEntry:
        stat = path.stat()
        return FileEntry(name=path.name, path=self.relative(path), is_dir=path.is_dir(),
                         size_bytes=None if path.is_dir() else stat.st_size,
                         modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                         permissions=oct(stat.st_mode & 0o777)[2:].zfill(4))

    # --- Operations ------------------------------------------------------------------------------------------

    def list_files(self, rel):
        folder = self.resolve(rel)
        if not folder.is_dir():
            raise HostingError("That is a file, not a folder.")
        entries = [self._entry(p) for p in folder.iterdir() if not p.is_symlink()]
        return sorted(entries, key=lambda e: (not e.is_dir, e.name.lower()))

    def read_file(self, rel, *, max_bytes):
        path = self.resolve(rel)
        if path.is_dir():
            raise HostingError("That is a folder, not a file.")
        if path.stat().st_size > max_bytes:
            raise HostingError("That file is too large to open here.")
        return path.read_bytes()

    def write_file(self, rel, content: bytes, *, overwrite=False):
        rel = clean_relative(rel)
        if not rel:
            raise HostingError("Choose a file name.")
        clean_name(rel.rsplit("/", 1)[-1])
        parent_rel = rel.rsplit("/", 1)[0] if "/" in rel else ""
        parent = self.resolve(parent_rel)
        if not parent.is_dir():
            raise HostingError("The folder for that file does not exist.")
        target = self.resolve(rel, must_exist=False)
        if target.exists():
            if target.is_dir():
                raise HostingError("A folder with that name already exists.")
            if not overwrite:
                raise HostingError("A file with that name already exists.")
        target.write_bytes(content)

    def make_directory(self, rel):
        rel = clean_relative(rel)
        if not rel:
            raise HostingError("Choose a folder name.")
        clean_name(rel.rsplit("/", 1)[-1])
        parent_rel = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if not self.resolve(parent_rel).is_dir():
            raise HostingError("The parent folder does not exist.")
        target = self.resolve(rel, must_exist=False)
        if target.exists():
            raise HostingError("Something with that name already exists.")
        target.mkdir()

    def rename_path(self, rel, new_rel):
        source = self.resolve(rel)
        if source == self._real_root:
            raise HostingError("Your website's main folder cannot be renamed.")
        new_rel = clean_relative(new_rel)
        if not new_rel:
            raise HostingError("Choose a new name.")
        clean_name(new_rel.rsplit("/", 1)[-1])
        destination = self.resolve(new_rel, must_exist=False)
        if destination.exists():
            raise HostingError("Something with that name already exists.")
        if not destination.parent.is_dir():
            raise HostingError("The destination folder does not exist.")
        if source.is_dir() and (destination == source or source in destination.parents):
            raise HostingError("A folder cannot be moved into itself.")
        source.rename(destination)

    def copy_path(self, rel, new_rel):
        source = self.resolve(rel)
        if source == self._real_root:
            raise HostingError("Your website's main folder cannot be copied.")
        new_rel = clean_relative(new_rel)
        if not new_rel:
            raise HostingError("Choose where to copy it.")
        clean_name(new_rel.rsplit("/", 1)[-1])
        destination = self.resolve(new_rel, must_exist=False)
        if destination.exists():
            raise HostingError("Something with that name already exists.")
        if not destination.parent.is_dir():
            raise HostingError("The destination folder does not exist.")
        if source.is_dir():
            if destination == source or source in destination.parents:
                raise HostingError("A folder cannot be copied into itself.")
            shutil.copytree(source, destination, symlinks=False)
        else:
            shutil.copy2(source, destination)

    def delete_path(self, rel):
        target = self.resolve(rel)
        if target == self._real_root:
            raise HostingError("Your website's main folder cannot be deleted.")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()

    def search_files(self, rel, query):
        query = (query or "").strip().lower()
        if not query:
            raise HostingError("Type something to search for.")
        base = self.resolve(rel)
        if not base.is_dir():
            raise HostingError("Search inside a folder.")
        found, visited = [], 0
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not Path(dirpath, d).is_symlink()]
            for name in dirnames + filenames:
                visited += 1
                path = Path(dirpath, name)
                if query in name.lower() and not path.is_symlink():
                    found.append(self._entry(path))
                if len(found) >= MAX_SEARCH_RESULTS or visited >= MAX_SEARCH_VISITED:
                    return sorted(found, key=lambda e: (not e.is_dir, e.path.lower()))
        return sorted(found, key=lambda e: (not e.is_dir, e.path.lower()))
