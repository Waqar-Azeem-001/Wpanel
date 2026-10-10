"""
What a node does for backups (N8, ADR 10): gather one account's data into a staging folder, store it as an encrypted restic snapshot in a bucket the node does not control
the lifecycle of, check it, restore it, and prove it can be restored.

Safety rules that matter:
* **Links are never followed.** A customer could point a link at ``/etc/shadow``; copying what it points to would put the server's secrets into their backup. Links are skipped.
* **A snapshot belongs to the account it was made for** (a tag): every command refuses a snapshot of another account.
* **A restore takes a safety backup first**, then replaces only the parts the backup holds.
* **The backup keys are never backed up**: the repository settings file is excluded from the node-configuration snapshot.
* A test restore uses a scratch folder and touches nothing live.

Only the file/database/mail copying has run for real (on temporary folders against FakeRestic and FakeMariadb); RealRestic and the cut-down staging approach for very large accounts
(this copies the data first) have not. See docs/n8-native-backups.md.
"""
import json
import os
import shutil
import uuid
from pathlib import Path

from .. import catalog
from . import render
from .mariadb import ident
from .restic import RepositoryError, manifest_of
from .system import SystemError_
from .website import HandlerError

ENV_FILE = "/etc/whp/restic.env"
NODE_CONFIG_ROOTS = ("/var/lib/whp/", "/etc/whp/", "/etc/httpd/conf.d/whp/", "/etc/postfix/whp/", "/etc/dovecot/whp/", "/etc/rspamd/local.d/", "/etc/ssh/sshd_config.d/", "/etc/rspamd/dkim/")
NEVER_BACKED_UP = (ENV_FILE,)
COVERS = {"files": {"files"}, "database": {"database"}, "email": {"email"}, "full": {"files", "database"}}  # "full" is the web node's whole account; mail is its own backup on the mail node


def copy_tree(source, target):
    """Copy a folder, SKIPPING every symbolic link. Returns the number of files copied."""
    source, target = Path(source), Path(target)
    target.mkdir(parents=True, exist_ok=True)
    count = 0
    for entry in os.scandir(source):
        if entry.is_symlink():
            continue
        if entry.is_dir(follow_symlinks=False):
            if entry.name == ".trash":
                continue
            count += copy_tree(entry.path, target / entry.name)
        elif entry.is_file(follow_symlinks=False):
            shutil.copy2(entry.path, target / entry.name)
            count += 1
    return count


class Backups:
    def __init__(self, system, restic_for, root_for, engine, stage_root, mail=None, websites=None):
        self.s, self.restic_for, self.root_for, self.engine, self.stage_root, self.mail, self.web = system, restic_for, root_for, engine, Path(stage_root), mail, websites

    # --- repository ---------------------------------------------------------------------------------------------------------------------
    def _env(self):
        raw = self.s.read(ENV_FILE)
        if not raw:
            raise HandlerError("not_configured", "Backups have not been set up on this node.")
        return dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)

    def _restic(self):
        return self.restic_for(self._env())

    def run(self, command, params):
        try:
            p = catalog.validate(command, params)
            handler = {"BACKUP_CONFIGURE": self.configure, "BACKUP_RUN": self.backup, "BACKUP_VERIFY": self.verify, "BACKUP_RESTORE": self.restore, "BACKUP_FORGET": self.forget,
                       "BACKUP_TEST_RESTORE": self.test_restore, "BACKUP_NODE_CONFIG": self.node_config}[command]
            return handler(p)
        except catalog.InvalidCommand as exc:
            raise HandlerError("invalid", str(exc)) from None
        except RepositoryError as exc:
            raise HandlerError("repository_error", str(exc)[:300]) from None
        except SystemError_ as exc:
            raise HandlerError("system_error", str(exc)[:300]) from None

    def configure(self, p):
        env = (f"RESTIC_REPOSITORY=s3:{p['endpoint']}/{p['bucket']}/{p['prefix']}\nRESTIC_PASSWORD={p['repo_password']}\n"
               f"AWS_ACCESS_KEY_ID={p['access_key']}\nAWS_SECRET_ACCESS_KEY={p['secret_key']}\n")
        old = self.s.read(ENV_FILE)
        self.s.write(ENV_FILE, env, 0o600)  # root only
        try:
            created = self._restic().init()  # an existing repository must accept this password
        except Exception:
            if old is None:
                self.s.remove(ENV_FILE)
            else:
                self.s.write(ENV_FILE, old, 0o600)
            raise
        return "configured", {"created": bool(created)}

    # --- helpers ------------------------------------------------------------------------------------------------------------------------
    def _stage(self, account):
        path = self.stage_root / f"{account}-{uuid.uuid4().hex[:8]}"
        path.mkdir(parents=True)
        return path

    def _owned(self, restic, account, snapshot_id):
        """(kind, source) of a snapshot, only if it belongs to this account."""
        meta = restic.meta(snapshot_id)
        tags = meta["tags"]
        if f"account:{account}" not in tags:
            raise HandlerError("forbidden", "That backup does not belong to this account.")
        kind = next((t.split(":", 1)[1] for t in tags if t.startswith("kind:")), "")
        return kind

    def _gather(self, account, kind, stage):
        """Copy what a backup of this kind holds into ``stage``. Returns {"files": n, "databases": n, "mail": n}."""
        found = {"files": 0, "databases": 0, "mail": 0}
        if kind in ("files", "full"):
            root = self.root_for(account)
            if root is None or not Path(root).is_dir() or self.web is None or self.web._state(account) is None:
                raise HandlerError("not_found", "No such website on this node.")
            found["files"] = copy_tree(root, stage / "files")
        if kind in ("database", "full"):
            if self.web is None or self.web._state(account) is None:
                raise HandlerError("not_found", "No such account on this node.")
            (stage / "databases").mkdir()
            for name in self.engine.databases(account + "_"):
                self.engine.dump(name, str(stage / "databases" / f"{name}.sql"))
                found["databases"] += 1
        if kind == "full":
            (stage / "config").mkdir()
            state = self.web._state(account)
            for path in (render.paths(account)["state"], render.paths(account)["fpm_conf"], render.paths(account)["unit"], render.paths(account)["slice"],
                         render.paths(account, state["domain"])["vhost"]):
                text = self.s.read(path)
                if text is not None:
                    (stage / "config" / path.strip("/").replace("/", "__")).write_text(text, encoding="utf-8")
        if kind == "email":
            if self.mail is None:
                raise HandlerError("not_applicable", "This node does not hold mail.")
            states = [s for s in self.mail._all() if s["account"] == account]
            if not states:
                raise HandlerError("not_found", "No mail for this account on this node.")
            for st in states:
                (stage / "mail" / st["domain"]).mkdir(parents=True)
                found["mail"] += copy_tree(self.mail.mail_root / st["domain"], stage / "mail" / st["domain"]) if (self.mail.mail_root / st["domain"]).is_dir() else 0
                (stage / "mail" / f"{st['domain']}.json").write_text(json.dumps({k: v for k, v in st.items() if k != "mailboxes"}), encoding="utf-8")  # settings, not password hashes
        return found

    # --- commands -----------------------------------------------------------------------------------------------------------------------
    def backup(self, p):
        restic = self._restic()
        stage = self._stage(p["account"])
        try:
            found = self._gather(p["account"], p["kind"], stage)
            snapshot_id, size = restic.backup(stage, [f"account:{p['account']}", f"kind:{p['kind']}", f"source:{p['source']}"])
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        return "created", {"snapshot_id": snapshot_id, "size_mb": round(size / 1048576, 2), **found}

    def verify(self, p):
        restic = self._restic()
        self._owned(restic, p["account"], p["snapshot_id"])
        ok, detail = restic.check(p["snapshot_id"])
        if not ok:
            raise HandlerError("damaged", detail)
        return "verified", {"detail": detail}

    def forget(self, p):
        restic = self._restic()
        self._owned(restic, p["account"], p["snapshot_id"])
        return ("deleted" if restic.forget(p["snapshot_id"]) else "absent"), {}

    def restore(self, p):
        restic = self._restic()
        kind = self._owned(restic, p["account"], p["snapshot_id"])
        if p["kind"] not in COVERS.get(kind, set()) and p["kind"] != kind:
            raise HandlerError("invalid", f"That backup holds {kind}, not {p['kind']}.")
        ok, detail = restic.check(p["snapshot_id"])
        if not ok:
            raise HandlerError("damaged", f"This backup failed its check, so it was not restored: {detail}")
        wanted = COVERS[p["kind"]]
        safety = self._safety_backup(restic, p["account"], p["kind"])
        scratch = self._stage(p["account"])
        try:
            data = restic.restore(p["snapshot_id"], scratch)
            restored = {"files": 0, "databases": 0, "mail": 0}
            if "files" in wanted and (data / "files").is_dir():
                root = Path(self.root_for(p["account"]))
                for entry in list(os.scandir(root)):
                    if entry.name == ".trash":
                        continue
                    shutil.rmtree(entry.path) if entry.is_dir(follow_symlinks=False) else os.unlink(entry.path)
                restored["files"] = copy_tree(data / "files", root)
            if "database" in wanted and (data / "databases").is_dir():
                for dump in sorted((data / "databases").glob("*.sql")):
                    name = ident(dump.stem)
                    if not name.startswith(p["account"] + "_"):
                        raise HandlerError("forbidden", "The backup holds a database that is not this account's.")
                    self.engine.load(name, str(dump))
                    restored["databases"] += 1
            if "email" in wanted and (data / "mail").is_dir() and self.mail is not None:
                for domain_dir in [d for d in (data / "mail").iterdir() if d.is_dir()]:
                    state = self.mail._state(domain_dir.name)
                    if state is None or state["account"] != p["account"]:
                        continue  # mail for a domain that no longer belongs to the account is not put back
                    target = self.mail.mail_root / domain_dir.name
                    for box in [b for b in domain_dir.iterdir() if b.is_dir() and b.name in state["mailboxes"]]:
                        shutil.rmtree(target / box.name, ignore_errors=True)
                        restored["mail"] += copy_tree(box, target / box.name)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        return "restored", {**restored, "safety_snapshot_id": safety}

    def _safety_backup(self, restic, account, kind):
        """A backup of exactly what the restore is about to replace, tagged ``before_restore``. Returns its snapshot id."""
        stage = self._stage(account)
        try:
            self._gather(account, kind, stage)
            snapshot_id, _ = restic.backup(stage, [f"account:{account}", f"kind:{kind}", "source:before_restore"])
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        return snapshot_id

    def test_restore(self, p):
        restic = self._restic()
        self._owned(restic, p["account"], p["snapshot_id"])
        meta = restic.meta(p["snapshot_id"])
        scratch = self._stage(p["account"])
        try:
            data = restic.restore(p["snapshot_id"], scratch)
            found = manifest_of(data)
        except RepositoryError as exc:
            raise HandlerError("restore_test_failed", str(exc)[:200]) from None
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        expected = meta.get("manifest")  # a real restic repository checks content itself and has no manifest here
        if expected is not None and found != expected:
            raise HandlerError("restore_test_failed", "The restored files differ from the list made at backup time.")
        return "restore_ok", {"files": len(found), "detail": "every file matched"}

    def node_config(self, p):
        restic = self._restic()
        stage = self.stage_root / f"nodeconfig-{uuid.uuid4().hex[:8]}"
        stage.mkdir(parents=True)
        try:
            count = 0
            for prefix in NODE_CONFIG_ROOTS:
                for path in self.s.list(prefix):
                    if path in NEVER_BACKED_UP or path.endswith(".whp-tmp"):
                        continue
                    text = self.s.read(path)
                    if text is not None:
                        (stage / path.strip("/").replace("/", "__")).write_text(text, encoding="utf-8")
                        count += 1
            snapshot_id, size = restic.backup(stage, ["node-config"])
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        return "created", {"snapshot_id": snapshot_id, "files": count, "size_mb": round(size / 1048576, 2)}
