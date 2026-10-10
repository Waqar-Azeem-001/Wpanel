"""
What the node does for the file commands (N6). Every operation works inside one folder per account (``/home/<account>/sites``) through the same sandbox the demo
provider uses: paths are relative, ``..`` and links are refused, the root itself cannot be renamed or deleted, a name is a plain name.

IMPORTANT for production: this code must run as the account's own Linux user, never as root, so that a mistake or a trick (a link swapped between the check and the
use) can reach no further than the account's own files. ``run_as_account`` below starts a child process under the account's uid/gid for that. It has not been run on
a real node (it cannot run on Windows at all).
"""
import base64
import binascii
import errno
import json
import os
import subprocess
import sys
from pathlib import Path

from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.sandbox import Sandbox, check_mode

from .. import catalog
from .website import HandlerError

MAX_READ = catalog.MAX_INLINE_BYTES


class NodeSandbox(Sandbox):
    """The sandbox with REAL permissions on a Linux node (the demo keeps them in a side file because it also runs on Windows)."""

    def set_permissions(self, rel, mode):
        if os.name != "posix":
            return super().set_permissions(rel, mode)
        target = self.resolve(rel)
        if target == self._real_root:
            raise HostingError("The permissions of your website's main folder cannot be changed.")
        os.chmod(target, int(check_mode(mode, target.is_dir()), 8))

    def _entry(self, path):
        entry = super()._entry(path)
        if os.name != "posix":
            return entry
        from dataclasses import replace

        return replace(entry, permissions="0%03o" % (path.stat().st_mode & 0o777))


def entry_json(e):
    return {"name": e.name, "path": e.path, "is_dir": e.is_dir, "size_bytes": e.size_bytes, "modified_at": e.modified_at, "permissions": e.permissions}


class Files:
    def __init__(self, root_for, meta_for=None):
        self.root_for, self.meta_for = root_for, meta_for or (lambda account: None)

    def _box(self, account):
        root = self.root_for(account)
        if root is None or not Path(root).is_dir():  # never create an account's folder here: a missing one means no such account
            raise HandlerError("not_found", "No such account on this node.")
        return NodeSandbox(Path(root), meta_file=self.meta_for(account))

    def run(self, command, params):
        try:
            p = catalog.validate(command, params)
            box = self._box(p["account"])
            return getattr(self, "do_" + command.lower())(box, p)
        except catalog.InvalidCommand as exc:
            raise HandlerError("invalid", str(exc)) from None
        except HostingError as exc:
            raise HandlerError("file_error", str(exc)) from None
        except OSError as exc:
            if exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", -1)):
                raise HandlerError("disk_full", "Your disk space is full, so this could not be saved. Delete files you do not need, or upgrade your plan.") from None
            raise HandlerError("system_error", f"The file system refused that ({exc.strerror or 'error'}).") from None

    # --- reads ----------------------------------------------------------------------------------------------------------------------
    def do_file_list(self, box, p):
        return "listed", {"entries": [entry_json(e) for e in box.list_files(p["path"])]}

    def do_file_read(self, box, p):
        return "read", {"content": base64.b64encode(box.read_file(p["path"], max_bytes=min(p["max_bytes"], MAX_READ))).decode()}

    def do_file_search(self, box, p):
        return "listed", {"entries": [entry_json(e) for e in box.search_files(p["path"], p["query"])]}

    def do_file_trash_list(self, box, p):
        return "listed", {"items": [{"item_id": i.item_id, "name": i.name, "original_path": i.original_path, "is_dir": i.is_dir, "deleted_at": i.deleted_at,
                                     "size_bytes": i.size_bytes} for i in box.list_trash()]}

    # --- changes ----------------------------------------------------------------------------------------------------------------------
    def do_file_write(self, box, p):
        try:
            content = base64.b64decode(p["content"], validate=True)
        except (binascii.Error, ValueError):
            raise HandlerError("invalid", "The content is not valid base64.") from None
        box.write_file(p["path"], content, overwrite=p["overwrite"] == "yes")
        return "written", {"size": len(content)}

    def do_file_mkdir(self, box, p):
        box.make_directory(p["path"])
        return "created", {}

    def do_file_rename(self, box, p):
        box.rename_path(p["path"], p["new_path"])
        return "renamed", {}

    def do_file_copy(self, box, p):
        box.copy_path(p["path"], p["new_path"])
        return "copied", {}

    def do_file_chmod(self, box, p):
        box.set_permissions(p["path"], p["mode"])
        return "changed", {}

    def do_file_delete(self, box, p):
        box.delete_path(p["path"])
        return "deleted", {}

    def do_file_archive(self, box, p):
        return "archived", {"count": box.archive_paths(p["paths"], p["archive_path"])}

    def do_file_extract(self, box, p):
        return "extracted", {"count": box.extract_archive(p["path"], p["destination"])}

    def do_file_trash(self, box, p):
        return "trashed", {"item_id": box.trash_path(p["path"])}

    def do_file_trash_restore(self, box, p):
        return "restored", {"path": box.restore_trash(p["item_id"])}

    def do_file_trash_purge(self, box, p):
        box.purge_trash(None if p["item_id"] == "ALL" else p["item_id"])
        return "purged", {}


def run_as_account(account, uid, gid, command, params):
    """UNTESTED. Run one file command in a child process under the account's own uid/gid (Linux, Python 3.9+), and return its (code, data)."""
    request = json.dumps({"account": account, "command": command, "params": params})
    done = subprocess.run([sys.executable, "-m", "apps.agent.node.file_worker"], input=request, capture_output=True, text=True, shell=False, timeout=600,
                          user=uid, group=gid, extra_groups=[], check=False)
    try:
        reply = json.loads(done.stdout)
    except ValueError:
        raise HandlerError("system_error", "The file worker failed.") from None
    if reply.get("error"):
        raise HandlerError(reply["error"]["code"], reply["error"]["message"])
    return reply["code"], reply["data"]
