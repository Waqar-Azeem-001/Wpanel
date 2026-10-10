"""
What the node does for the database commands (N4). Every database and user belongs to exactly one account and carries its name as a prefix
(``<account>_``): the node refuses to touch a name that does not, so one account can never reach another's databases, whatever it is asked.
The plan's database limit is checked here, where the databases really are. Rows are only ever read, one page at a time, with long values cut.
"""
import json
import secrets
import time

from .. import catalog
from . import render
from .mariadb import native_hash
from .system import SystemError_
from .website import HandlerError

MAX_CELL = 500


class Databases:
    def __init__(self, system, engine):
        self.s, self.db = system, engine

    # --- helpers ----------------------------------------------------------------------------------------------------------------------
    def _limit(self, account):
        raw = self.s.read(render.paths(account)["state"])
        if not raw:
            raise HandlerError("not_found", "No such account on this node.")
        return json.loads(raw)["limits"].get("databases", render.DEFAULTS["databases"])

    def _own(self, account, name, kind="database"):
        if not name.startswith(account + "_"):
            raise HandlerError("forbidden", f"A {kind} name must start with {account}_.")
        return name

    def _owned_db(self, account, name):
        self._own(account, name)
        if name not in self.db.databases(account + "_"):
            raise HandlerError("not_found", "That database does not exist on this account.")
        return name

    def _owned_user(self, account, name):
        self._own(account, name, "user")
        if name not in self.db.users_with(account + "_"):
            raise HandlerError("not_found", "That database user does not exist on this account.")
        return name

    # --- entry ----------------------------------------------------------------------------------------------------------------------------
    def run(self, command, params):
        try:
            p = catalog.validate(command, params)
            handler = {"DB_INVENTORY": self.inventory, "CREATE_DATABASE": self.create_db, "DELETE_DATABASE": self.delete_db, "CREATE_DB_USER": self.create_user,
                       "SET_DB_PASSWORD": self.set_password, "DELETE_DB_USER": self.delete_user, "GRANT_DB_ACCESS": self.grant, "DB_TABLES": self.tables,
                       "DB_DESCRIBE": self.describe, "DB_BROWSE": self.browse, "BACKUP_DATABASE": self.backup, "RESTORE_DATABASE": self.restore}[command]
            return handler(p)
        except catalog.InvalidCommand as exc:
            raise HandlerError("invalid", str(exc)) from None
        except SystemError_ as exc:
            raise HandlerError("system_error", str(exc)[:300]) from None

    # --- commands ---------------------------------------------------------------------------------------------------------------------------
    def inventory(self, p):
        account, prefix = p["account"], p["account"] + "_"
        self._limit(account)
        grants = self.db.grants_of(prefix)
        users = self.db.users_with(prefix)
        return "listed", {"limit": self._limit(account), "databases": [{"name": d, "size_mb": self.db.size_mb(d), "users": sorted({g[1] for g in grants if g[0] == d})}
                                                                           for d in self.db.databases(prefix)],
                          "users": [{"name": u, "databases": sorted({g[0] for g in grants if g[1] == u})} for u in users],
                          "grants": [{"database": d, "user": u, "privileges": privs} for d, u, privs in grants]}

    def create_db(self, p):
        account = p["account"]
        self._own(account, p["name"])
        existing = self.db.databases(account + "_")
        if p["name"] in existing:
            return "exists", {}
        if len(existing) >= self._limit(account):
            raise HandlerError("limit_reached", "Your plan's database limit has been reached.")
        self.db.create_db(p["name"])
        return "created", {}

    def delete_db(self, p):
        self._own(p["account"], p["name"])
        if p["name"] not in self.db.databases(p["account"] + "_"):
            return "absent", {}
        self.db.drop_db(p["name"])
        return "deleted", {}

    def create_user(self, p):
        self._own(p["account"], p["name"], "user")
        if p["name"] in self.db.users_with(p["account"] + "_"):
            raise HandlerError("exists", "That database user already exists.")
        self.db.create_user(p["name"], native_hash(p["password"]))
        return "created", {}

    def set_password(self, p):
        self.db.set_hash(self._owned_user(p["account"], p["name"]), native_hash(p["password"]))
        return "changed", {}

    def delete_user(self, p):
        self._own(p["account"], p["name"], "user")
        if p["name"] not in self.db.users_with(p["account"] + "_"):
            return "absent", {}
        self.db.drop_user(p["name"])
        return "deleted", {}

    def grant(self, p):
        self.db.set_grants(self._owned_db(p["account"], p["database"]), self._owned_user(p["account"], p["user"]), p["privileges"])
        return "granted", {}

    def tables(self, p):
        db = self._owned_db(p["account"], p["database"])
        return "listed", {"tables": [{"name": n, "rows": r, "size_kb": k} for n, r, k in self.db.tables(db)]}

    def _table(self, p):
        db = self._owned_db(p["account"], p["database"])
        if p["table"] not in [t[0] for t in self.db.tables(db)]:
            raise HandlerError("not_found", "That table does not exist.")
        return db

    def describe(self, p):
        db = self._table(p)
        return "listed", {"columns": [{"name": n, "type": t, "nullable": nl, "primary_key": pk, "default": d} for n, t, nl, pk, d in self.db.columns(db, p["table"])]}

    def browse(self, p):
        db = self._table(p)
        columns = self.db.columns(db, p["table"])
        rows, total = self.db.rows(db, p["table"], p["offset"], p["limit"])
        cut = [[None if v is None else str(v)[:MAX_CELL] for v in row] for row in rows]
        return "listed", {"columns": [c[0] for c in columns], "rows": cut, "total": total, "key_columns": [c[0] for c in columns if c[3]]}

    def _dump(self, account, name):
        backup_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(4)
        path = f"/var/backups/whp/db/{account}/{name}-{backup_id}.sql.zst"
        self.s.mkdir(f"/var/backups/whp/db/{account}", 0o700)
        self.db.dump(name, path)
        self.s.write(path + ".ok", "", 0o600)  # a marker: only a dump that finished is ever restored
        return backup_id, path

    def backup(self, p):
        name = self._owned_db(p["account"], p["name"])
        backup_id, _ = self._dump(p["account"], name)
        return "created", {"backup_id": backup_id}

    def restore(self, p):
        name = self._own(p["account"], p["name"])
        path = f"/var/backups/whp/db/{p['account']}/{name}-{p['backup_id']}.sql.zst"
        if not self.s.exists(path + ".ok"):
            raise HandlerError("not_found", "No such backup for this database.")
        safety = None
        if name in self.db.databases(p["account"] + "_"):
            safety, _ = self._dump(p["account"], name)  # the current contents are kept first, so a wrong restore can be undone
        self.db.load(name, path)
        return "restored", {"safety_backup_id": safety} if safety else {}
