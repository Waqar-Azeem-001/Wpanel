"""
The node's database engine (N4, ADR 05: one MariaDB per node). ``FakeMariadb`` is a small in-memory stand-in used by the tests; ``RealMariadb`` talks to the
``mariadb`` command line client and is **untested on a real server**.

Rules for both: names are checked again here (letters, digits, underscore only) before they are placed in SQL; a password never appears in a command line (it
is sent to the client's standard input, already turned into a hash); the client is started from an argument list, never a shell.
"""
import re
import subprocess

from .system import SystemError_

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
PRIV_SQL = {"select": "SELECT", "insert": "INSERT", "update": "UPDATE", "delete": "DELETE", "create": "CREATE", "alter": "ALTER", "drop": "DROP", "index": "INDEX"}


def native_hash(password):
    """The mysql_native_password hash, so the clear password never has to be written into SQL."""
    import hashlib

    return "*" + hashlib.sha1(hashlib.sha1(password.encode()).digest()).hexdigest().upper()


def ident(name):
    if not isinstance(name, str) or not IDENT.fullmatch(name):
        raise SystemError_(f"Refused database name {name!r}.")
    return name


class FakeMariadb:
    def __init__(self):
        self.dbs = {}  # name -> {table: {"columns": [(name, type, nullable, primary_key, default)], "rows": [[...]]}}
        self.users, self.grants, self.dumps = {}, {}, {}  # users: name -> hash; grants: (db, user) -> set; dumps: path -> snapshot

    def add_table(self, db, table, columns, rows):  # test helper
        self.dbs[db][table] = {"columns": columns, "rows": rows}

    def databases(self, prefix):
        return sorted(d for d in self.dbs if d.startswith(prefix))

    def create_db(self, name):
        self.dbs[ident(name)] = {}

    def drop_db(self, name):
        self.dbs.pop(ident(name), None)
        self.grants = {k: v for k, v in self.grants.items() if k[0] != name}

    def users_with(self, prefix):
        return sorted(u for u in self.users if u.startswith(prefix))

    def create_user(self, name, password_hash):
        self.users[ident(name)] = password_hash

    def set_hash(self, name, password_hash):
        self.users[ident(name)] = password_hash

    def drop_user(self, name):
        self.users.pop(ident(name), None)
        self.grants = {k: v for k, v in self.grants.items() if k[1] != name}

    def set_grants(self, db, user, privileges):
        if privileges:
            self.grants[(ident(db), ident(user))] = set(privileges)
        else:
            self.grants.pop((db, user), None)

    def grants_of(self, prefix):
        return [(d, u, sorted(p)) for (d, u), p in sorted(self.grants.items()) if d.startswith(prefix)]

    def size_mb(self, db):
        return round(sum(len(t["rows"]) for t in self.dbs[db].values()) * 0.01, 2)

    def tables(self, db):
        return [(n, len(t["rows"]), max(16, len(t["rows"]))) for n, t in sorted(self.dbs[db].items())]

    def columns(self, db, table):
        return list(self.dbs[db][table]["columns"])

    def rows(self, db, table, offset, limit):
        data = self.dbs[db][table]["rows"]
        return data[offset:offset + limit], len(data)

    def dump(self, db, path):
        self.dumps[path] = {t: {"columns": list(v["columns"]), "rows": [list(r) for r in v["rows"]]} for t, v in self.dbs[db].items()}

    def load(self, db, path):
        self.dbs[ident(db)] = {t: {"columns": list(v["columns"]), "rows": [list(r) for r in v["rows"]]} for t, v in self.dumps[path].items()}


class RealMariadb:
    """UNTESTED against a real server. Talks to MariaDB as root over the local socket through the ``mariadb`` client."""

    def _sql(self, sql, database=None):
        argv = ["mariadb", "--batch", "--raw", "--skip-column-names"] + ([database] if database else [])
        done = subprocess.run(argv, input=sql, shell=False, capture_output=True, text=True, timeout=120, check=False)
        if done.returncode != 0:
            raise SystemError_(f"mariadb failed: {done.stderr.strip()[:200]}")
        return [line.split("\t") for line in done.stdout.splitlines()]

    def databases(self, prefix):
        return [r[0] for r in self._sql(f"SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE '{ident(prefix.rstrip('_'))}\\_%' ORDER BY 1;")]

    def create_db(self, name):
        self._sql(f"CREATE DATABASE `{ident(name)}` CHARACTER SET utf8mb4;")

    def drop_db(self, name):
        self._sql(f"DROP DATABASE IF EXISTS `{ident(name)}`;")

    def users_with(self, prefix):
        return [r[0] for r in self._sql(f"SELECT user FROM mysql.user WHERE user LIKE '{ident(prefix.rstrip('_'))}\\_%' AND host='localhost' ORDER BY 1;")]

    def create_user(self, name, password_hash):
        if not re.fullmatch(r"\*[0-9A-F]{40}", password_hash):
            raise SystemError_("Not a password hash.")
        self._sql(f"CREATE USER '{ident(name)}'@'localhost' IDENTIFIED BY PASSWORD '{password_hash}';")

    def set_hash(self, name, password_hash):
        if not re.fullmatch(r"\*[0-9A-F]{40}", password_hash):
            raise SystemError_("Not a password hash.")
        self._sql(f"ALTER USER '{ident(name)}'@'localhost' IDENTIFIED BY PASSWORD '{password_hash}';")

    def drop_user(self, name):
        self._sql(f"DROP USER IF EXISTS '{ident(name)}'@'localhost';")

    def set_grants(self, db, user, privileges):
        self._sql(f"REVOKE ALL PRIVILEGES ON `{ident(db)}`.* FROM '{ident(user)}'@'localhost';")
        if privileges:
            self._sql(f"GRANT {', '.join(PRIV_SQL[p] for p in privileges)} ON `{ident(db)}`.* TO '{ident(user)}'@'localhost';")

    def grants_of(self, prefix):
        rows = self._sql(f"SELECT table_schema, grantee FROM information_schema.schema_privileges WHERE table_schema LIKE '{ident(prefix.rstrip('_'))}\\_%';")
        found = {}
        for schema, grantee in rows:
            user = grantee.split("'")[1]
            found.setdefault((schema, user), set())
        return [(d, u, []) for (d, u) in sorted(found)]  # TODO: map each privilege back (SELECT privilege_type ... GROUP BY)

    def size_mb(self, db):
        row = self._sql(f"SELECT ROUND(IFNULL(SUM(data_length+index_length),0)/1048576,2) FROM information_schema.tables WHERE table_schema='{ident(db)}';")
        return float(row[0][0]) if row else 0.0

    def tables(self, db):
        return [(r[0], int(r[1] or 0), int(r[2] or 0) // 1024) for r in self._sql(
            f"SELECT table_name, table_rows, data_length+index_length FROM information_schema.tables WHERE table_schema='{ident(db)}' ORDER BY 1;")]

    def columns(self, db, table):
        rows = self._sql(f"SELECT column_name, column_type, is_nullable, column_key, column_default FROM information_schema.columns "
                         f"WHERE table_schema='{ident(db)}' AND table_name='{ident(table)}' ORDER BY ordinal_position;")
        return [(r[0], r[1], r[2] == "YES", r[3] == "PRI", None if r[4] == "NULL" else r[4]) for r in rows]

    def rows(self, db, table, offset, limit):
        total = int(self._sql(f"SELECT COUNT(*) FROM `{ident(db)}`.`{ident(table)}`;")[0][0])
        return self._sql(f"SELECT * FROM `{ident(table)}` LIMIT {int(limit)} OFFSET {int(offset)};", database=ident(db)), total

    def dump(self, db, path):
        with open(path, "wb") as out:
            dump = subprocess.Popen(["mariadb-dump", "--single-transaction", "--routines", ident(db)], stdout=subprocess.PIPE, shell=False)
            packer = subprocess.run(["zstd", "-q", "-T0"], stdin=dump.stdout, stdout=out, shell=False, check=False)
            dump.wait()
        if dump.returncode != 0 or packer.returncode != 0:
            raise SystemError_("The database dump failed.")

    def load(self, db, path):
        self.drop_db(db)
        self.create_db(db)
        unpack = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE, shell=False)
        done = subprocess.run(["mariadb", ident(db)], stdin=unpack.stdout, shell=False, capture_output=True, check=False)
        unpack.wait()
        if unpack.returncode != 0 or done.returncode != 0:
            raise SystemError_("Restoring the database failed.")
