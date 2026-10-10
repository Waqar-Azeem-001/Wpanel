"""
Databases on a native node (N4): the same ``DatabasesCapability`` the cPanel and demo providers offer, so the customer's database screens work unchanged.

Every call is a typed, signed agent command and waits for the node's answer (``apps.agent.services.call``). Names get the account prefix (``<account>_``) that
the node insists on. Row-level editing and free SQL are NOT offered on native nodes yet: those would need a free-text command, which the agent deliberately
does not have. Browsing is read-only and typed.
"""
from apps.agent import services as agent
from apps.agent.catalog import DB_PRIVILEGES
from apps.core.exceptions import ServiceError

from .base import HostingError
from .capabilities import (ColumnInfo, DatabaseAdminAccess, DatabaseGrant, DatabaseInfo, DatabaseLimits, DatabasesCapability, DatabaseUserInfo, TableData,
                           TableInfo)

NOT_YET = "Editing data and running SQL are not available on this server yet. You can browse tables, and use any database tool with your database user."


class NativeDatabases(DatabasesCapability):
    def _call(self, command, params, wait=15):
        try:
            return agent.call(self.server, command, params, wait=wait)
        except ServiceError as exc:  # NoAnswer, CommandFailed and refusals all become a plain message for the customer
            raise HostingError(exc.message) from exc

    @staticmethod
    def _prefix(username):
        return f"{username}_"

    def _full(self, username, name):
        name = (name or "").strip().lower()
        if not name or any(not (c.isascii() and (c.isalnum() or c == "_")) for c in name):
            raise HostingError("Use letters, numbers and underscores only.")
        prefix = self._prefix(username)
        return name if name.startswith(prefix) else prefix + name

    def _inventory(self, username):
        return self._call("DB_INVENTORY", {"account": username})

    # --- limits and access -------------------------------------------------------------------------------------------------------------
    def database_limits(self, username):
        limit = self._inventory(username)["limit"]
        return DatabaseLimits(name_prefix=self._prefix(username), max_databases=None if limit >= 1000 else limit, max_users=None, password_min_length=12)

    def database_admin_access(self, username, database):
        return DatabaseAdminAccess(kind="embedded")

    # --- databases, users, grants ----------------------------------------------------------------------------------------------------------
    def list_databases(self, username):
        return [DatabaseInfo(name=d["name"], size_mb=d["size_mb"], users=list(d["users"])) for d in self._inventory(username)["databases"]]

    def create_database(self, username, name):
        self._call("CREATE_DATABASE", {"account": username, "name": self._full(username, name)})

    def delete_database(self, username, name):
        self._call("DELETE_DATABASE", {"account": username, "name": self._full(username, name)})

    def list_database_users(self, username):
        return [DatabaseUserInfo(name=u["name"], databases=list(u["databases"])) for u in self._inventory(username)["users"]]

    def create_database_user(self, username, name, password):
        self._call("CREATE_DB_USER", {"account": username, "name": self._full(username, name), "password": password})

    def set_database_user_password(self, username, name, password):
        self._call("SET_DB_PASSWORD", {"account": username, "name": self._full(username, name), "password": password})

    def delete_database_user(self, username, name):
        self._call("DELETE_DB_USER", {"account": username, "name": self._full(username, name)})

    def list_database_grants(self, username):
        return [DatabaseGrant(user=g["user"], database=g["database"], privileges=list(g["privileges"])) for g in self._inventory(username)["grants"]]

    def grant_database_access(self, username, database, user, privileges):
        if any(p not in DB_PRIVILEGES for p in privileges):
            raise HostingError("That privilege is not available.")
        self._call("GRANT_DB_ACCESS", {"account": username, "database": self._full(username, database), "user": self._full(username, user),
                                       "privileges": list(privileges)})

    # --- looking inside (read-only) --------------------------------------------------------------------------------------------------------
    def list_tables(self, username, database):
        data = self._call("DB_TABLES", {"account": username, "database": self._full(username, database)})
        return [TableInfo(name=t["name"], rows=t["rows"], size_kb=t["size_kb"]) for t in data["tables"]]

    def describe_table(self, username, database, table):
        data = self._call("DB_DESCRIBE", {"account": username, "database": self._full(username, database), "table": table})
        return [ColumnInfo(name=c["name"], type=c["type"], nullable=c["nullable"], primary_key=c["primary_key"], default=c["default"]) for c in data["columns"]]

    def browse_table(self, username, database, table, offset, limit):
        data = self._call("DB_BROWSE", {"account": username, "database": self._full(username, database), "table": table, "offset": max(0, int(offset)),
                                        "limit": min(100, max(1, int(limit)))})
        return TableData(columns=data["columns"], rows=data["rows"], total=data["total"], key_columns=data["key_columns"])

    def run_query(self, username, database, sql):
        raise HostingError(NOT_YET)

    def insert_row(self, username, database, table, values):
        raise HostingError(NOT_YET)

    def update_row(self, username, database, table, key, values):
        raise HostingError(NOT_YET)

    def delete_row(self, username, database, table, key):
        raise HostingError(NOT_YET)

    # --- database backups (used by the backup platform in N8; no screen calls these yet) -------------------------------------------------
    def backup_database(self, username, name):
        return self._call("BACKUP_DATABASE", {"account": username, "name": self._full(username, name)})["backup_id"]

    def restore_database(self, username, name, backup_id):
        return self._call("RESTORE_DATABASE", {"account": username, "name": self._full(username, name), "backup_id": backup_id})
