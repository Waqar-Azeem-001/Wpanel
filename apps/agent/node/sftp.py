"""
SFTP logins on a native node (N6, ADR 09). Every login is an SFTP-only user locked into one folder of its account: no shell, no tunnelling, no way up out of the
folder. OpenSSH does the locking with ``ChrootDirectory``; the folder shown inside it is a bind mount of the account's chosen directory.

How the pieces fit:
* the login is a system user ``<account>_<name>`` that shares the account's uid (``useradd -o``) so it can read and write the account's files, and is in the group
  ``whp-sftp``, to which one ``Match Group`` block in ``sshd_config.d`` applies: chroot to ``/srv/whp/sftp/%u``, ``internal-sftp`` only, no forwarding, no tty;
* ``/srv/whp/sftp/<login>`` is owned by root (OpenSSH insists), its ``files`` folder is the bind mount (a systemd mount unit, so it survives reboots);
* the password goes to ``chpasswd`` on standard input, never on a command line.

Only run against FakeSystem so far; the sshd and mount behaviour has never been tried on a real node. There is no login for the main account yet.
"""
import json

from .. import catalog
from . import render
from .system import SystemError_
from .website import HandlerError, Websites

SSHD_CONF = "/etc/ssh/sshd_config.d/60-whp-sftp.conf"
SSHD_TEXT = ("# managed by WH Agent - do not edit by hand\nMatch Group whp-sftp\n    ChrootDirectory /srv/whp/sftp/%u\n    ForceCommand internal-sftp\n"
             "    AllowTcpForwarding no\n    X11Forwarding no\n    PermitTTY no\n    PasswordAuthentication yes\n")


def login_of(account, name):
    return f"{account}_{name}"


def mount_unit(login):
    return f"srv-whp-sftp-{login}-files.mount"


class Sftp:
    def __init__(self, system, root_for):
        self.s, self.root_for, self.web = system, root_for, Websites(system)

    def _folder(self, account, directory):
        """The real folder the login will see, checked with the file sandbox (no links, inside the account's area)."""
        from apps.hosting.adapters.base import HostingError

        from .files import NodeSandbox

        root = self.root_for(account)
        if root is None:
            raise HandlerError("not_found", "No such account on this node.")
        try:
            box = NodeSandbox(root)
            target = box.resolve(directory)
        except HostingError as exc:
            raise HandlerError("file_error", str(exc)) from None
        if not target.is_dir():
            raise HandlerError("file_error", "That folder does not exist in your hosting space. Create it in the file manager first.")
        return "/home/" + account + "/sites" + ("/" + directory if directory else "")

    def _ensure_sshd(self):
        if self.s.read(SSHD_CONF) != SSHD_TEXT:
            self.s.write(SSHD_CONF, SSHD_TEXT)
            self.s.run(["sshd", "-t"])  # never reload a config that does not parse
            self.s.run(["systemctl", "reload", "sshd"])

    def run(self, command, params):
        try:
            p = catalog.validate(command, params)
            handler = {"SFTP_LIST": self.list, "SFTP_CREATE": self.create, "SFTP_SET_PASSWORD": self.set_password, "SFTP_DELETE": self.delete}[command]
            return handler(p)
        except catalog.InvalidCommand as exc:
            raise HandlerError("invalid", str(exc)) from None
        except SystemError_ as exc:
            raise HandlerError("system_error", str(exc)[:300]) from None

    def _state(self, account):
        return self.web._need(account)

    def list(self, p):
        state = self._state(p["account"])
        return "listed", {"limit": state["limits"].get("sftp_accounts", render.DEFAULTS["sftp_accounts"]),
                          "logins": [{"name": n, "login": login_of(p["account"], n), "directory": v["directory"]} for n, v in sorted(state.get("sftp", {}).items())]}

    def create(self, p):
        account, name = p["account"], p["name"]
        state = self._state(account)
        logins = state.get("sftp", {})
        if name in logins:
            raise HandlerError("exists", "That SFTP login already exists.")
        if len(logins) >= state["limits"].get("sftp_accounts", render.DEFAULTS["sftp_accounts"]):
            raise HandlerError("limit_reached", "Your plan's limit of SFTP logins has been reached.")
        source = self._folder(account, p["directory"])
        login, undo = login_of(account, name), []
        base, unit = f"/srv/whp/sftp/{login}", f"/etc/systemd/system/{mount_unit(login)}"
        try:
            self._ensure_sshd()
            self.s.run(["useradd", "-o", "-u", str(state["uid"]), "-g", account, "-G", "whp-sftp", "-M", "-d", base, "-s", "/usr/sbin/nologin", login])
            undo.append(lambda: self.s.run(["userdel", login]))
            self.s.run(["chpasswd"], input=f"{login}:{p['password']}\n")
            self.s.mkdir(base, 0o755)  # root-owned: OpenSSH refuses to chroot into anything else
            self.s.mkdir(base + "/files", 0o755)
            undo.append(lambda: self.s.remove(base))
            self.s.write(unit, f"# managed by WH Agent\n[Unit]\nDescription=SFTP folder for {login}\n\n[Mount]\nWhat={source}\nWhere={base}/files\nType=none\n"
                               f"Options=bind\n\n[Install]\nWantedBy=multi-user.target\n")
            undo.append(lambda: self.s.remove(unit))
            self.s.run(["systemctl", "daemon-reload"])
            self.s.run(["systemctl", "enable", "--now", mount_unit(login)])
            logins[name] = {"directory": p["directory"]}
            self.web._save(account, {**state, "sftp": logins})
        except Exception:
            for step in reversed(undo):
                try:
                    step()
                except Exception:
                    pass
            raise
        return "created", {"login": login}

    def set_password(self, p):
        state = self._state(p["account"])
        if p["name"] not in state.get("sftp", {}):
            raise HandlerError("not_found", "That SFTP login no longer exists.")
        self.s.run(["chpasswd"], input=f"{login_of(p['account'], p['name'])}:{p['password']}\n")
        return "changed", {}

    def _remove_login(self, account, name):
        login = login_of(account, name)
        self.s.run(["systemctl", "disable", "--now", mount_unit(login)])
        self.s.remove(f"/etc/systemd/system/{mount_unit(login)}")
        self.s.run(["userdel", login])
        self.s.remove(f"/srv/whp/sftp/{login}")

    def delete(self, p):
        account, name = p["account"], p["name"]
        state = self._state(account)
        if name not in state.get("sftp", {}):
            return "absent", {}
        self._remove_login(account, name)
        self.s.run(["systemctl", "daemon-reload"])
        logins = dict(state["sftp"])
        del logins[name]
        self.web._save(account, {**state, "sftp": logins})
        return "deleted", {}

    # --- used by the website commands --------------------------------------------------------------------------------------------------
    def remove_all(self, account, state):
        for name in list(state.get("sftp", {})):
            self._remove_login(account, name)

    def lock_all(self, account, state, lock):
        for name in state.get("sftp", {}):
            self.s.run(["usermod", "--lock" if lock else "--unlock", login_of(account, name)])


_ = json
