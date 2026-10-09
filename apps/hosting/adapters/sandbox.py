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
import json
import os
import re
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .base import HostingError
from .capabilities import FileEntry, TrashItem

MAX_PATH_LENGTH = 1024
MAX_NAME_LENGTH = 255
MAX_SEARCH_RESULTS = 200
MAX_ARCHIVE_ENTRIES = 5000
MAX_ARCHIVE_BYTES = 200_000_000
TRASH_DIR = ".trash"  # reserved: holds deleted items until they are restored or emptied
TRASH_INDEX = ".index.json"
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


def check_mode(mode, is_dir):
    """A permission such as 644 or 0755, tidied to four digits, or a HostingError saying why it is not allowed on a shared host."""
    mode = str(mode or "").strip()
    if not re.fullmatch(r"[0-7]{3,4}", mode):
        raise HostingError("Use a permission such as 644 or 755.")
    digits = mode.zfill(4)
    if digits[0] != "0":
        raise HostingError("Special permission bits (setuid, setgid, sticky) are not allowed here.")
    owner, group, other = (int(d) for d in digits[1:])
    if other & 2:
        raise HostingError("Anyone-can-write permissions are not allowed: they let any other user on the server change your files.")
    if is_dir and owner != 7:
        raise HostingError("A folder needs full access for you (7 first), or you could lock yourself out of it.")
    if not is_dir and owner & 6 != 6:
        raise HostingError("A file must stay readable and writable by you (6 or 7 first).")
    return digits


class Sandbox:
    def __init__(self, root, meta_file=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._real_root = self.root.resolve()
        self._meta = Path(meta_file) if meta_file else None  # where permissions are kept, outside the website folder

    # --- Permissions (a sidecar map, so they are the same on every operating system) ----------------------------------

    def _modes(self) -> dict:
        try:
            return json.loads(self._meta.read_text(encoding="utf-8")) if self._meta else {}
        except (OSError, ValueError):
            return {}

    def _save_modes(self, modes):
        if self._meta:
            self._meta.parent.mkdir(parents=True, exist_ok=True)
            self._meta.write_text(json.dumps(modes), encoding="utf-8")

    def _carry_modes(self, old, new, *, keep_old):
        """After a move or copy, the entries under ``old`` now also (or only) live under ``new``; after a delete, ``new`` is None."""
        modes = self._modes()
        moved = {}
        for key in list(modes):
            if key == old or key.startswith(old + "/"):
                if new is not None:
                    moved[new + key[len(old):]] = modes[key]
                if not keep_old:
                    del modes[key]
        modes.update(moved)
        self._save_modes(modes)

    def set_permissions(self, rel, mode):
        target = self.resolve(rel)
        if target == self._real_root:
            raise HostingError("The permissions of your website's main folder cannot be changed.")
        digits = check_mode(mode, target.is_dir())
        modes = self._modes()
        modes[self.relative(target)] = digits
        self._save_modes(modes)

    # --- Resolving -------------------------------------------------------------------------------------------

    def resolve(self, rel, *, must_exist=True) -> Path:
        rel = clean_relative(rel)
        if rel.split("/")[0] == TRASH_DIR:
            raise HostingError("That folder is reserved for the trash.")
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
                         permissions=self._modes().get(self.relative(path), "0755" if path.is_dir() else "0644"))

    # --- Operations ------------------------------------------------------------------------------------------

    def list_files(self, rel):
        folder = self.resolve(rel)
        if not folder.is_dir():
            raise HostingError("That is a file, not a folder.")
        entries = [self._entry(p) for p in folder.iterdir()
                   if not p.is_symlink() and not (folder == self._real_root and p.name == TRASH_DIR)]
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
        old = self.relative(source)
        source.rename(destination)
        self._carry_modes(old, self.relative(destination), keep_old=False)

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
        self._carry_modes(self.relative(source), self.relative(destination), keep_old=True)

    def delete_path(self, rel):
        target = self.resolve(rel)
        if target == self._real_root:
            raise HostingError("Your website's main folder cannot be deleted.")
        old = self.relative(target)
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        self._carry_modes(old, None, keep_old=False)

    def search_files(self, rel, query):
        query = (query or "").strip().lower()
        if not query:
            raise HostingError("Type something to search for.")
        base = self.resolve(rel)
        if not base.is_dir():
            raise HostingError("Search inside a folder.")
        found, visited = [], 0
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not Path(dirpath, d).is_symlink() and d != TRASH_DIR]
            for name in dirnames + filenames:
                visited += 1
                path = Path(dirpath, name)
                if query in name.lower() and not path.is_symlink():
                    found.append(self._entry(path))
                if len(found) >= MAX_SEARCH_RESULTS or visited >= MAX_SEARCH_VISITED:
                    return sorted(found, key=lambda e: (not e.is_dir, e.path.lower()))
        return sorted(found, key=lambda e: (not e.is_dir, e.path.lower()))

    # --- Archives ------------------------------------------------------------------------------------------------

    def archive_paths(self, rels, archive_rel):
        """Zip the given files and folders into one new ``.zip`` (never overwriting anything)."""
        archive_rel = clean_relative(archive_rel)
        if not archive_rel or not archive_rel.lower().endswith(".zip"):
            raise HostingError("Name the archive with a .zip ending.")
        clean_name(archive_rel.rsplit("/", 1)[-1])
        target = self.resolve(archive_rel, must_exist=False)
        if target.exists():
            raise HostingError("Something with that name already exists.")
        if not target.parent.is_dir():
            raise HostingError("The destination folder does not exist.")
        sources = []
        for rel in rels:
            source = self.resolve(rel)
            if source == self._real_root:
                raise HostingError("Your website's main folder cannot be archived as a whole; pick what is inside it.")
            sources.append(source)
        if not sources:
            raise HostingError("Choose what to put in the archive.")
        total, count = 0, 0
        try:
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
                for source in sources:
                    base = source.parent
                    walk = [source]
                    if source.is_dir():
                        walk = [source] + [Path(d, n) for d, ds, fs in os.walk(source) for n in ds + fs]
                    for path in walk:
                        if path.is_symlink() or path.resolve() == target.resolve() or self._real_root / TRASH_DIR in path.parents:
                            continue
                        count += 1
                        if count > MAX_ARCHIVE_ENTRIES:
                            raise HostingError("That is too many files for one archive.")
                        arcname = path.relative_to(base).as_posix()
                        if path.is_dir():
                            zf.writestr(arcname + "/", b"")
                        else:
                            total += path.stat().st_size
                            if total > MAX_ARCHIVE_BYTES:
                                raise HostingError("Those files are too large for one archive.")
                            zf.write(path, arcname)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return count

    def extract_archive(self, rel, dest_rel):
        """Unpack a ``.zip`` into ``dest_rel`` (created if missing). Refuses anything unsafe or conflicting, all or nothing."""
        source = self.resolve(rel)
        if source.is_dir() or not source.name.lower().endswith(".zip") or not zipfile.is_zipfile(source):
            raise HostingError("That is not a .zip archive.")
        dest_rel = clean_relative(dest_rel)
        destination = self.resolve(dest_rel, must_exist=False)
        if destination.exists() and not destination.is_dir():
            raise HostingError("Something with that name already exists.")
        if not destination.parent.is_dir():
            raise HostingError("The destination folder does not exist.")
        with zipfile.ZipFile(source) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                raise HostingError("That archive has too many files.")
            if sum(i.file_size for i in infos) > MAX_ARCHIVE_BYTES:
                raise HostingError("That archive is too large to unpack here.")
            plan = []
            for info in infos:
                name = info.filename.replace("\\", "/")
                is_dir = name.endswith("/")
                try:
                    member = clean_relative(name.rstrip("/"))
                except HostingError:
                    raise HostingError("That archive contains an unsafe path, so it was not unpacked.")
                if not member:
                    continue
                if member.split("/")[0] == TRASH_DIR or (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise HostingError("That archive contains something that is not allowed, so it was not unpacked.")
                target_rel = f"{dest_rel}/{member}" if dest_rel else member
                target = self.resolve(target_rel, must_exist=False)
                if not is_dir and target.exists():
                    raise HostingError(f"'{member}' already exists, so nothing was unpacked.")
                plan.append((info, target, is_dir))
            destination.mkdir(exist_ok=True)
            for info, target, is_dir in plan:
                if is_dir:
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
        return len(plan)

    # --- Trash ---------------------------------------------------------------------------------------------------

    def _trash(self) -> Path:
        return self._real_root / TRASH_DIR

    def _read_index(self) -> dict:
        index = self._trash() / TRASH_INDEX
        try:
            return json.loads(index.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write_index(self, data: dict):
        self._trash().mkdir(exist_ok=True)
        (self._trash() / TRASH_INDEX).write_text(json.dumps(data), encoding="utf-8")

    def trash_path(self, rel):
        """Move a file or folder to the trash (recoverable). Returns the trash id."""
        source = self.resolve(rel)
        if source == self._real_root:
            raise HostingError("Your website's main folder cannot be deleted.")
        item_id = uuid.uuid4().hex[:12]
        original = self.relative(source)
        self._trash().mkdir(exist_ok=True)
        size = None if source.is_dir() else source.stat().st_size
        shutil.move(str(source), str(self._trash() / item_id))
        self._carry_modes(original, None, keep_old=False)
        index = self._read_index()
        index[item_id] = {"name": source.name, "original": original, "is_dir": source.is_dir(), "size": size,
                          "deleted_at": datetime.now(timezone.utc).isoformat()}
        self._write_index(index)
        return item_id

    def list_trash(self):
        index = self._read_index()
        items = []
        for item_id, meta in index.items():
            if (self._trash() / item_id).exists():
                items.append(TrashItem(item_id=item_id, name=meta["name"], original_path=meta["original"],
                                       is_dir=meta["is_dir"], deleted_at=meta["deleted_at"], size_bytes=meta.get("size")))
        return sorted(items, key=lambda i: i.deleted_at, reverse=True)

    def restore_trash(self, item_id):
        index = self._read_index()
        meta = index.get(item_id)
        stored = self._trash() / item_id if item_id and "/" not in item_id and "\\" not in item_id else None
        if meta is None or stored is None or not stored.exists():
            raise HostingError("That item is no longer in the trash.")
        original = clean_relative(meta["original"])
        parent_rel = original.rsplit("/", 1)[0] if "/" in original else ""
        try:
            parent = self.resolve(parent_rel)
            if not parent.is_dir():
                raise HostingError("gone")
        except HostingError:
            parent_rel, parent = "", self._real_root  # the old folder is gone: put it back at the top
        name, candidate, n = meta["name"], None, 0
        while candidate is None or candidate.exists():
            label = name if n == 0 else f"{name} (restored{'' if n == 1 else ' ' + str(n)})"
            candidate = parent / label
            n += 1
        shutil.move(str(stored), str(candidate))
        del index[item_id]
        self._write_index(index)
        return self.relative(candidate)

    def purge_trash(self, item_id=None):
        """Delete one trashed item for good, or (with no id) empty the whole trash."""
        if item_id is None:
            shutil.rmtree(self._trash(), ignore_errors=True)
            return
        index = self._read_index()
        if item_id not in index or "/" in item_id or "\\" in item_id:
            raise HostingError("That item is no longer in the trash.")
        stored = self._trash() / item_id
        if stored.is_dir():
            shutil.rmtree(stored)
        elif stored.exists():
            stored.unlink()
        del index[item_id]
        self._write_index(index)
