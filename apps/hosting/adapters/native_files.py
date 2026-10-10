"""
Files and SFTP logins on a native node (N6): the same ``FilesCapability`` and ``FtpCapability`` the customer's File Manager and FTP screens already use, served by the
node's agent. The File Manager moves file CONTENT inside commands, so it is limited to a few MB per file here; anything bigger goes by SFTP, which the screens already
recommend. Plain FTP is not offered on native nodes (ADR 09): the FTP screen shows SFTP only.
"""
import base64
import re

from apps.agent.catalog import MAX_INLINE_BYTES

from .base import HostingError
from .capabilities import FileEntry, FtpAccount, FtpOptions, FilesCapability, FtpCapability, TrashItem
from .ftp_rules import check_ftp_password

TOO_BIG = f"Files larger than {MAX_INLINE_BYTES // 1_000_000} MB cannot be moved through the web file manager on this server. Use SFTP for big files."
NAME = re.compile(r"^[a-z][a-z0-9]{1,14}$")


def check_password(password):
    check_ftp_password(password)
    if len(password) < 12:
        raise HostingError("Use a password of 12 to 128 characters.")  # native logins are stricter than the shared rule


def to_entry(e):
    return FileEntry(name=e["name"], path=e["path"], is_dir=e["is_dir"], size_bytes=e["size_bytes"], modified_at=e["modified_at"], permissions=e["permissions"])


class NativeFiles(FilesCapability):
    def _file(self, command, username, wait=30, **params):
        return self._call(command, {"account": username, **params}, wait=wait)

    def list_files(self, username, path):
        return [to_entry(e) for e in self._file("FILE_LIST", username, path=path)["entries"]]

    def read_file(self, username, path, *, max_bytes):
        try:
            data = self._file("FILE_READ", username, path=path, max_bytes=min(max_bytes, MAX_INLINE_BYTES))
        except HostingError as exc:
            if "too large" in str(exc).lower() and max_bytes > MAX_INLINE_BYTES:
                raise HostingError(TOO_BIG) from exc
            raise
        return base64.b64decode(data["content"])

    def write_file(self, username, path, content, *, overwrite=False):
        if len(content) > MAX_INLINE_BYTES:
            raise HostingError(TOO_BIG)
        self._file("FILE_WRITE", username, path=path, content=base64.b64encode(content).decode(), overwrite="yes" if overwrite else "no")

    def make_directory(self, username, path):
        self._file("FILE_MKDIR", username, path=path)

    def rename_path(self, username, path, new_path):
        self._file("FILE_RENAME", username, path=path, new_path=new_path)

    def copy_path(self, username, path, new_path):
        self._file("FILE_COPY", username, wait=120, path=path, new_path=new_path)

    def set_permissions(self, username, path, mode):
        self._file("FILE_CHMOD", username, path=path, mode=str(mode).strip())

    def delete_path(self, username, path):
        self._file("FILE_DELETE", username, wait=60, path=path)

    def archive_paths(self, username, paths, archive_path):
        return self._file("FILE_ARCHIVE", username, wait=120, paths=list(paths), archive_path=archive_path)["count"]

    def extract_archive(self, username, path, destination):
        return self._file("FILE_EXTRACT", username, wait=120, path=path, destination=destination)["count"]

    def trash_path(self, username, path):
        return self._file("FILE_TRASH", username, wait=60, path=path)["item_id"]

    def list_trash(self, username):
        return [TrashItem(item_id=i["item_id"], name=i["name"], original_path=i["original_path"], is_dir=i["is_dir"], deleted_at=i["deleted_at"],
                          size_bytes=i["size_bytes"]) for i in self._file("FILE_TRASH_LIST", username)["items"]]

    def restore_trash(self, username, item_id):
        return self._file("FILE_TRASH_RESTORE", username, wait=60, item_id=item_id)["path"]

    def purge_trash(self, username, item_id):
        self._file("FILE_TRASH_PURGE", username, wait=60, item_id=item_id or "ALL")

    def search_files(self, username, path, query):
        return [to_entry(e) for e in self._file("FILE_SEARCH", username, path=path, query=query)["entries"]]


class NativeFtp(FtpCapability):
    def _login_name(self, username, login):
        prefix = f"{username}_"
        if not (login or "").startswith(prefix):
            raise HostingError("That login does not belong to this account.")
        return login[len(prefix):]

    def ftp_options(self, username):
        limit = self._call("SFTP_LIST", {"account": username})["limit"]
        return FtpOptions(host=self.server.hostname, port=0, secure_port=22, max_accounts=None if limit >= 1000 else limit, root="")  # port 0: plain FTP is not offered

    def list_ftp_accounts(self, username):
        return [FtpAccount(login=r["login"], directory=r["directory"]) for r in self._call("SFTP_LIST", {"account": username})["logins"]]

    def create_ftp_account(self, username, name, password, directory):
        name = (name or "").strip().lower()
        if not NAME.match(name):
            raise HostingError("The login name uses 2 to 15 lowercase letters and numbers, starting with a letter.")
        check_password(password)
        directory = (directory or "").strip().strip("/")
        return self._call("SFTP_CREATE", {"account": username, "name": name, "password": password, "directory": directory})["login"]

    def set_ftp_password(self, username, login, password):
        check_password(password)
        self._call("SFTP_SET_PASSWORD", {"account": username, "name": self._login_name(username, login), "password": password})

    def delete_ftp_account(self, username, login):
        self._call("SFTP_DELETE", {"account": username, "name": self._login_name(username, login)})
