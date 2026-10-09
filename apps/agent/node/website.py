"""
What the node does for the website commands (N3): make, suspend, unsuspend, change PHP version, change limits, delete.

Each handler works through ``system`` (see system.py), is safe to repeat (the account's state file says what is already done) and, when a step fails
part-way while creating, undoes what it did so far instead of leaving a half-made account. Deleting is the one destructive step that cannot be undone, so
the archive is made first and a failed archive stops everything before anything is removed.

Written for AlmaLinux 9 (ADR 20) with Apache event MPM + one PHP-FPM master per account under its own systemd slice (ADR 01, 02, 03, 11). It has run
only against FakeSystem; it has never been run on a real node.
"""
import json

from .. import catalog
from . import render
from .system import SystemError_

INDEX = "<!doctype html><title>{domain}</title><h1>{domain}</h1><p>This website is ready. Upload your files to replace this page.</p>\n"


class HandlerError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class Websites:
    def __init__(self, system):
        self.s = system

    # --- helpers ----------------------------------------------------------------------------------------------------------------------
    def _state(self, account):
        raw = self.s.read(render.paths(account)["state"])
        return json.loads(raw) if raw else None

    def _save(self, account, state):
        self.s.write(render.paths(account)["state"], json.dumps(state, sort_keys=True), 0o600)

    def _need(self, account, domain=None):
        state = self._state(account)
        if state is None or (domain and state["domain"] != domain):
            raise HandlerError("not_found", "No such website on this node.")
        return state

    def _reload_web(self):
        self.s.run(["httpd", "-t"])  # a bad config is caught before it can take every site down
        self.s.run(["systemctl", "reload", "httpd"])

    def _quota(self, account, disk_mb):
        uid = self.s.uid(account)
        self.s.run(["xfs_quota", "-x", "-c", f"project -s -p /home/{account} {uid}", "/home"])
        self.s.run(["xfs_quota", "-x", "-c", f"limit -p bsoft={int(disk_mb * 0.9)}m bhard={disk_mb}m {uid}", "/home"])

    # --- commands ---------------------------------------------------------------------------------------------------------------------
    def run(self, command, params):
        try:
            params = catalog.validate(command, params)  # again, on the node's own side
            handler = {"CREATE_WEBSITE": self.create, "DELETE_WEBSITE": self.delete, "SUSPEND_WEBSITE": self.suspend, "UNSUSPEND_WEBSITE": self.unsuspend,
                       "CHANGE_PHP_VERSION": self.change_php, "SET_LIMITS": self.set_limits}.get(command)
            if handler is None:
                raise HandlerError("not_implemented", f"{command} is not implemented on this node yet.")
            return handler(params)
        except catalog.InvalidCommand as exc:
            raise HandlerError("invalid", str(exc)) from None
        except SystemError_ as exc:
            raise HandlerError("system_error", str(exc)[:300]) from None

    def create(self, p):
        account, domain = p["account"], p["domain"]
        existing = self._state(account)
        if existing:
            if existing["domain"] == domain:
                return "exists", {"root": render.paths(account, domain)["docroot"], "uid": existing["uid"]}  # already done: the same answer again
            raise HandlerError("conflict", "That account already hosts another website.")
        if self.s.uid(account) is not None:
            raise HandlerError("conflict", "A system user with that name already exists.")
        limits, paths, undo = render.limits_of(p), render.paths(account, domain), []
        try:
            self.s.run(["useradd", "--create-home", "--home-dir", paths["home"], "--shell", "/sbin/nologin", "--user-group", account])
            undo.append(lambda: self.s.run(["userdel", "--remove", account]))
            uid = self.s.uid(account)
            self.s.mkdir(paths["home"], 0o711, account)
            for folder, mode in ((f"{paths['home']}/sites", 0o755), (paths["site"], 0o755), (paths["docroot"], 0o755), (paths["logs"], 0o750), (paths["tmp"], 0o700)):
                self.s.mkdir(folder, mode, account)
            self.s.write(f"{paths['docroot']}/index.html", INDEX.format(domain=domain), 0o644, account)
            self._quota(account, limits["disk_mb"])
            undo.append(lambda: self.s.run(["xfs_quota", "-x", "-c", f"limit -p bsoft=0 bhard=0 {uid}", "/home"]))
            self.s.write(paths["slice"], render.slice_unit(account, limits))
            self.s.write(paths["fpm_conf"], render.fpm_conf(account, limits))
            self.s.write(paths["unit"], render.unit(account, p["php_version"]))
            undo.append(lambda: [self.s.remove(paths[k]) for k in ("slice", "fpm_conf", "unit")])
            self.s.run(["systemctl", "daemon-reload"])
            self.s.run(["systemctl", "enable", "--now", f"whp-php-{account}.service"])
            undo.append(lambda: self.s.run(["systemctl", "disable", "--now", f"whp-php-{account}.service"]))
            self.s.write(paths["vhost"], render.vhost(account, domain))
            undo.append(lambda: self.s.remove(paths["vhost"]))
            self._reload_web()
            self._save(account, {"domain": domain, "php_version": p["php_version"], "limits": limits, "suspended": False, "uid": uid})
        except Exception:
            for step in reversed(undo):  # best effort: a failing undo must not hide the original failure
                try:
                    step()
                except Exception:
                    pass
            if undo:
                try:
                    self.s.run(["systemctl", "daemon-reload"])
                except Exception:
                    pass
            raise
        return "created", {"root": paths["docroot"], "uid": uid}

    def suspend(self, p):
        state = self._need(p["account"])
        if state["suspended"]:
            return "suspended", {}
        paths = render.paths(p["account"], state["domain"])
        self.s.write(paths["vhost"], render.vhost(p["account"], state["domain"], suspended=True))
        self.s.run(["systemctl", "stop", f"whp-php-{p['account']}.service"])
        self._reload_web()
        self._save(p["account"], {**state, "suspended": True})
        return "suspended", {}

    def unsuspend(self, p):
        state = self._need(p["account"])
        if not state["suspended"]:
            return "active", {}
        paths = render.paths(p["account"], state["domain"])
        self.s.run(["systemctl", "start", f"whp-php-{p['account']}.service"])
        self.s.write(paths["vhost"], render.vhost(p["account"], state["domain"]))
        self._reload_web()
        self._save(p["account"], {**state, "suspended": False})
        return "active", {}

    def change_php(self, p):
        state = self._need(p["account"], p["domain"])
        if state["php_version"] == p["php_version"]:
            return "unchanged", {}
        self.s.write(render.paths(p["account"])["unit"], render.unit(p["account"], p["php_version"]))
        self.s.run(["systemctl", "daemon-reload"])
        if not state["suspended"]:
            self.s.run(["systemctl", "restart", f"whp-php-{p['account']}.service"])
        self._save(p["account"], {**state, "php_version": p["php_version"]})
        return "changed", {}

    def set_limits(self, p):
        state = self._need(p["account"])
        limits = render.limits_of(p)
        if limits == state["limits"]:
            return "unchanged", {}
        paths = render.paths(p["account"])
        self._quota(p["account"], limits["disk_mb"])
        self.s.write(paths["slice"], render.slice_unit(p["account"], limits))
        self.s.write(paths["fpm_conf"], render.fpm_conf(p["account"], limits))
        self.s.run(["systemctl", "daemon-reload"])
        if not state["suspended"]:
            self.s.run(["systemctl", "restart", f"whp-php-{p['account']}.service"])
        self._save(p["account"], {**state, "limits": limits})
        return "changed", {}

    def delete(self, p):
        account = p["account"]
        state = self._state(account)
        if state is None:
            return "absent", {}  # already gone: deleting twice is not an error
        if state["domain"] != p["domain"]:
            raise HandlerError("conflict", "That account hosts a different website.")
        paths = render.paths(account, state["domain"])
        data = {}
        if p["keep_archive"] == "yes":  # first, and a failure here stops everything before anything is removed
            archive = f"/var/backups/whp/archive-{account}.tar.zst"
            self.s.run(["tar", "--zstd", "-cf", archive, "-C", "/home", account])
            data["archive"] = archive
        self.s.run(["systemctl", "disable", "--now", f"whp-php-{account}.service"])
        for key in ("vhost", "unit", "slice", "fpm_conf"):
            self.s.remove(paths[key])
        self.s.run(["systemctl", "daemon-reload"])
        self._reload_web()
        self.s.run(["xfs_quota", "-x", "-c", f"limit -p bsoft=0 bhard=0 {state['uid']}", "/home"])
        self.s.run(["userdel", "--remove", account])
        self.s.remove(paths["state"])
        return "deleted", data
