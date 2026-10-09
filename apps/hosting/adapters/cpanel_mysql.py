"""MySQL databases on a cPanel server, through UAPI's ``Mysql`` module. Looking inside a database (tables, rows, SQL) is not offered here."""
from .base import HostingError
from .capabilities import (DatabaseGrant, DatabaseInfo, DatabaseLimits, DatabasesCapability, DatabaseUserInfo)
from .cpanel_api import CpanelApi, plan_limit

PRIVILEGES = {"SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "INDEX", "REFERENCES", "LOCK TABLES",
              "CREATE TEMPORARY TABLES", "CREATE ROUTINE", "ALTER ROUTINE", "EXECUTE", "CREATE VIEW", "SHOW VIEW", "TRIGGER", "EVENT"}


class CpanelMysql(CpanelApi, DatabasesCapability):
    def _prefix(self, user):
        restrictions = self._remember(("restrictions", user), lambda: self._uapi(user, "Mysql", "get_restrictions") or {})
        return str(restrictions.get("prefix") or f"{user}_")

    def _full(self, user, name):
        """A database or user name with the account's prefix (added when the customer left it off), checked for plain characters."""
        name = (name or "").strip()
        if not name or any(not (c.isascii() and (c.isalnum() or c in "_-")) for c in name):
            raise HostingError("Use letters, numbers, dashes and underscores only.")
        prefix = self._prefix(user)
        return name if name.startswith(prefix) else prefix + name

    def _owned(self, user, name, kind):
        """The full name, only if it is one of this account's own databases ("databases") or users ("users")."""
        full = self._full(user, name)
        listing = self._uapi(user, "Mysql", "list_databases" if kind == "databases" else "list_users") or []
        key = "database" if kind == "databases" else "user"
        if full not in [i.get(key) for i in listing if isinstance(i, dict)]:
            raise HostingError("That does not exist on this account.")
        return full

    def database_limits(self, username):
        return DatabaseLimits(name_prefix=self._prefix(username), max_databases=plan_limit(self._summary(username).get("maxsql")), max_users=None,
                              password_min_length=12)

    def list_databases(self, username):
        found = []
        for item in self._uapi(username, "Mysql", "list_databases") or []:
            size = item.get("disk_usage")
            found.append(DatabaseInfo(name=item.get("database", ""), size_mb=round(int(size) / 1048576, 2) if str(size).isdigit() else None,
                                      users=list(item.get("users") or [])))
        return found

    def create_database(self, username, name):
        self._uapi(username, "Mysql", "create_database", {"name": self._full(username, name)})

    def delete_database(self, username, name):
        self._uapi(username, "Mysql", "delete_database", {"name": self._owned(username, name, "databases")})

    def list_database_users(self, username):
        return [DatabaseUserInfo(name=i.get("user", ""), databases=list(i.get("databases") or []))
                for i in self._uapi(username, "Mysql", "list_users") or [] if isinstance(i, dict)]

    def create_database_user(self, username, name, password):
        self._uapi(username, "Mysql", "create_user", {"name": self._full(username, name), "password": password}, secret=True)

    def set_database_user_password(self, username, name, password):
        self._uapi(username, "Mysql", "set_password", {"user": self._owned(username, name, "users"), "password": password}, secret=True)

    def delete_database_user(self, username, name):
        self._uapi(username, "Mysql", "delete_user", {"name": self._owned(username, name, "users")})

    def grant_database_access(self, username, database, user, privileges):
        database, user = self._owned(username, database, "databases"), self._owned(username, user, "users")
        wanted = {p.upper() for p in privileges or []}
        if wanted - PRIVILEGES - {"ALL PRIVILEGES", "ALL"}:
            raise HostingError("Choose from the listed privileges.")
        if not wanted:
            self._uapi(username, "Mysql", "revoke_access_to_database", {"user": user, "database": database})
            return
        listed = "ALL PRIVILEGES" if wanted & {"ALL", "ALL PRIVILEGES"} else ",".join(sorted(wanted))
        self._uapi(username, "Mysql", "set_privileges_on_database", {"user": user, "database": database, "privileges": listed})

    def list_database_grants(self, username):
        grants = []
        for user in self.list_database_users(username):
            for database in user.databases:
                got = self._uapi(username, "Mysql", "get_privileges_on_database", {"user": user.name, "database": database}) or []
                grants.append(DatabaseGrant(user=user.name, database=database, privileges=[str(p) for p in got]))
        return grants

    # Looking inside a database needs a direct SQL connection or phpMyAdmin's one-time session; neither is built yet.
    def database_admin_access(self, username, database):
        self._unsupported("The database manager")

    def list_tables(self, username, database):
        self._unsupported("Browsing tables")

    def browse_table(self, username, database, table, offset, limit):
        self._unsupported("Browsing tables")

    def describe_table(self, username, database, table):
        self._unsupported("Browsing tables")

    def run_query(self, username, database, sql):
        self._unsupported("Running SQL")

    def insert_row(self, username, database, table, values):
        self._unsupported("Editing rows")

    def update_row(self, username, database, table, key, values):
        self._unsupported("Editing rows")

    def delete_row(self, username, database, table, key):
        self._unsupported("Editing rows")
