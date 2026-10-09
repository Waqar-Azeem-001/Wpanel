"""The File Manager on a cPanel server: UAPI ``Fileman`` for reading and listing, API 2 ``Fileman::fileop`` and ``mkdir`` for changes. Every path is made
relative to the account's own home folder first, so nothing outside it can be named. Uploads of binary files, the trash view and name search are not offered."""
from datetime import datetime, timezone

from .base import HostingError
from .capabilities import FileEntry, FilesCapability
from .cpanel_api import CpanelApi
from .sandbox import check_mode, clean_name, clean_relative

HIDDEN = {".trash", ".cpanel", ".cphorde", ".softaculous", ".spamassassin"}


def _parent(rel):
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


def _leaf(rel):
    return rel.rsplit("/", 1)[-1]


def _iso(epoch):
    try:
        return datetime.fromtimestamp(int(float(epoch)), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


class CpanelFiles(CpanelApi, FilesCapability):
    def _abs(self, user, rel):
        rel = clean_relative(rel)
        home = self._home(user)
        return f"{home}/{rel}" if rel else home

    def _op(self, user, op, source_rel, dest=None, **extra):
        params = {"op": op, "sourcefiles": self._abs(user, source_rel), "doubledecode": 0, **extra}
        if dest is not None:
            params["destfiles"] = dest
        self._api2(user, "Fileman", "fileop", params)

    def _exists(self, user, rel):
        return any(e.name == _leaf(rel) for e in self.list_files(user, _parent(rel)))

    # --- reading ---------------------------------------------------------------------------------------------------

    def list_files(self, username, path):
        rel = clean_relative(path)
        entries = []
        for item in self._uapi(username, "Fileman", "list_files", {"dir": self._abs(username, rel), "include_mime": 0, "include_permissions": 1,
                                                                    "show_hidden": 1}) or []:
            name = str(item.get("file") or item.get("name") or "")
            if not name or name in (".", "..") or name in HIDDEN:
                continue
            is_dir = str(item.get("type", "")).lower() in ("dir", "directory")
            size = item.get("size")
            entries.append(FileEntry(name=name, path=f"{rel}/{name}" if rel else name, is_dir=is_dir,
                                     size_bytes=None if is_dir or size in (None, "") else int(size), modified_at=_iso(item.get("mtime")),
                                     permissions=str(item.get("nicemode") or "").zfill(4) if item.get("nicemode") else ""))
        return sorted(entries, key=lambda e: (not e.is_dir, e.name.lower()))

    def read_file(self, username, path, *, max_bytes):
        rel = clean_relative(path)
        if not rel:
            raise HostingError("Choose a file.")
        data = self._uapi(username, "Fileman", "get_file_content", {"dir": self._abs(username, _parent(rel)), "file": _leaf(rel)}) or {}
        content = str(data.get("content", "")).encode("utf-8")
        if len(content) > max_bytes:
            raise HostingError("That file is too large to open here.")
        return content

    def write_file(self, username, path, content, *, overwrite=False):
        rel = clean_relative(path)
        clean_name(_leaf(rel))
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise HostingError("Only text files can be saved on this server from here. Upload other files with FTP or SFTP.")
        if not overwrite and self._exists(username, rel):
            raise HostingError("Something with that name already exists.")
        self._uapi(username, "Fileman", "save_file_content", {"dir": self._abs(username, _parent(rel)), "file": _leaf(rel), "content": text}, secret=True)

    # --- changing --------------------------------------------------------------------------------------------------

    def make_directory(self, username, path):
        rel = clean_relative(path)
        clean_name(_leaf(rel))
        self._api2(username, "Fileman", "mkdir", {"path": self._abs(username, _parent(rel)), "name": _leaf(rel), "permissions": "0755"})

    def rename_path(self, username, path, new_path):
        source, target = clean_relative(path), clean_relative(new_path)
        if not source or not target:
            raise HostingError("Choose a new name.")
        clean_name(_leaf(target))
        if self._exists(username, target):
            raise HostingError("Something with that name already exists.")
        if _parent(source) == _parent(target):
            self._op(username, "rename", source, _leaf(target))
            return
        self._op(username, "move", source, self._abs(username, _parent(target)))
        if _leaf(source) != _leaf(target):
            self._op(username, "rename", f"{_parent(target)}/{_leaf(source)}".lstrip("/"), _leaf(target))

    def copy_path(self, username, path, new_path):
        source, target = clean_relative(path), clean_relative(new_path)
        if not source or not target:
            raise HostingError("Choose where to copy it.")
        clean_name(_leaf(target))
        if self._exists(username, target):
            raise HostingError("Something with that name already exists.")
        self._op(username, "copy", source, self._abs(username, _parent(target)))
        if _leaf(source) != _leaf(target):
            self._op(username, "rename", f"{_parent(target)}/{_leaf(source)}".lstrip("/"), _leaf(target))

    def set_permissions(self, username, path, mode):
        rel = clean_relative(path)
        if not rel:
            raise HostingError("The permissions of your website's main folder cannot be changed.")
        is_dir = any(e.is_dir and e.name == _leaf(rel) for e in self.list_files(username, _parent(rel)))
        self._op(username, "chmod", rel, None, metadata=check_mode(mode, is_dir)[1:])

    def delete_path(self, username, path):
        rel = clean_relative(path)
        if not rel:
            raise HostingError("Your website's main folder cannot be deleted.")
        self._op(username, "unlink", rel)

    def trash_path(self, username, path):
        rel = clean_relative(path)
        if not rel:
            raise HostingError("Your website's main folder cannot be deleted.")
        self._op(username, "trash", rel)
        return ""

    def archive_paths(self, username, paths, archive_path):
        rels = [clean_relative(p) for p in paths or []]
        archive = clean_relative(archive_path)
        if not rels or not archive or "" in rels:
            raise HostingError("Choose what to zip.")
        clean_name(_leaf(archive))
        if self._exists(username, archive):
            raise HostingError("Something with that name already exists.")
        self._api2(username, "Fileman", "fileop", {"op": "compress", "sourcefiles": ",".join(self._abs(username, r) for r in rels),
                                                   "destfiles": self._abs(username, archive), "metadata": "zip", "doubledecode": 0})
        return len(rels)

    def extract_archive(self, username, path, destination):
        source, dest = clean_relative(path), clean_relative(destination)
        if not source.lower().endswith(".zip"):
            raise HostingError("Only .zip files can be extracted.")
        self._op(username, "extract", source, self._abs(username, dest))
        return 0

    # --- not offered on cPanel servers yet -------------------------------------------------------------------------

    def list_trash(self, username):
        self._unsupported("The trash view")

    def restore_trash(self, username, item_id):
        self._unsupported("The trash view")

    def purge_trash(self, username, item_id):
        self._unsupported("The trash view")

    def search_files(self, username, path, query):
        self._unsupported("Searching file names")
