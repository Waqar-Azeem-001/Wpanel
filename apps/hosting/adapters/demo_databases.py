"""
The demo provider's databases: a small JSON file per account that behaves like a shared-hosting database service
(prefixed names, plan limits, users, per-database privileges, passwords that are checked and never stored in the clear).
There is no real database server behind it - it exists so the screens and the rules can be built and tested first. A real
provider (the cPanel adapter) implements the same ``DatabasesCapability`` contract.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from django.contrib.auth.hashers import make_password

from .base import HostingError
from .capabilities import DATABASE_PRIVILEGES, DatabaseGrant, DatabaseInfo, DatabaseLimits, DatabaseUserInfo
from .demo_sql import SqlDatabase

MAX_DATABASES = 10
MAX_USERS = 10
NAME_RE = re.compile(r"^[a-z0-9_]{1,40}$")
USER_RE = re.compile(r"^[a-z0-9_]{1,12}$")  # shorter: database user names are limited to 32 characters in total
PASSWORD_MIN = 12
PASSWORD_MAX = 128


def check_password(password):
    if not isinstance(password, str) or not (PASSWORD_MIN <= len(password) <= PASSWORD_MAX):
        raise HostingError(f"Use a password of {PASSWORD_MIN} to {PASSWORD_MAX} characters.")
    if " " in password or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise HostingError("The password needs letters and numbers, and no spaces.")


class DemoDatabases:
    def __init__(self, directory, username):
        self.file = Path(directory) / "databases.json"
        self.dir = Path(directory)
        self.prefix = f"{username}_"

    # --- storage ---------------------------------------------------------------------------------------------

    def _load(self):
        try:
            return json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"databases": {}, "users": {}, "grants": []}

    def _save(self, data):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(data), encoding="utf-8")

    def _full(self, typed, pattern, what):
        typed = (typed or "").strip().lower()
        if typed.startswith(self.prefix):
            typed = typed[len(self.prefix):]
        if not pattern.match(typed):
            raise HostingError(f"A {what} name can use lowercase letters, numbers and underscores only "
                               f"(up to {40 if pattern is NAME_RE else 12} characters).")
        return self.prefix + typed

    # --- the database itself (one SQLite file each, created with sample tables) ---------------------------------

    def sql(self, name) -> SqlDatabase:
        """The engine for an existing database; a missing file (an older demo record) is created with the sample tables."""
        if name not in self._load()["databases"]:
            raise HostingError("That database does not exist.")
        db = SqlDatabase(self.dir / "data" / f"{name}.sqlite3")
        if not db.exists():
            db.seed(sample_tables(name))
        return db

    # --- reading ---------------------------------------------------------------------------------------------

    def limits(self):
        return DatabaseLimits(name_prefix=self.prefix, max_databases=MAX_DATABASES, max_users=MAX_USERS,
                              password_min_length=PASSWORD_MIN)

    def databases(self):
        data = self._load()
        users_of = {}
        for grant in data["grants"]:
            users_of.setdefault(grant["database"], []).append(grant["user"])
        return [DatabaseInfo(name=name, size_mb=self.sql(name).size_mb(), users=sorted(users_of.get(name, [])))
                for name in sorted(data["databases"])]

    def users(self):
        data = self._load()
        dbs_of = {}
        for grant in data["grants"]:
            dbs_of.setdefault(grant["user"], []).append(grant["database"])
        return [DatabaseUserInfo(name=name, databases=sorted(dbs_of.get(name, []))) for name in sorted(data["users"])]

    def grants(self):
        return [DatabaseGrant(user=g["user"], database=g["database"], privileges=list(g["privileges"]))
                for g in sorted(self._load()["grants"], key=lambda g: (g["database"], g["user"]))]

    # --- changing --------------------------------------------------------------------------------------------

    def create_database(self, typed):
        name = self._full(typed, NAME_RE, "database")
        data = self._load()
        if name in data["databases"]:
            raise HostingError("A database with that name already exists.")
        if len(data["databases"]) >= MAX_DATABASES:
            raise HostingError(f"Your plan allows {MAX_DATABASES} databases. Delete one or upgrade your plan.")
        data["databases"][name] = {"created": datetime.now(timezone.utc).isoformat()}
        self._save(data)
        self.sql(name)  # make the file now, with its sample tables

    def delete_database(self, name):
        data = self._load()
        if name not in data["databases"]:
            raise HostingError("That database does not exist.")
        SqlDatabase(self.dir / "data" / f"{name}.sqlite3").delete_file()
        del data["databases"][name]
        data["grants"] = [g for g in data["grants"] if g["database"] != name]
        self._save(data)

    def create_user(self, typed, password):
        name = self._full(typed, USER_RE, "user")
        check_password(password)
        data = self._load()
        if name in data["users"]:
            raise HostingError("A user with that name already exists.")
        if len(data["users"]) >= MAX_USERS:
            raise HostingError(f"Your plan allows {MAX_USERS} database users. Delete one or upgrade your plan.")
        data["users"][name] = {"password_hash": make_password(password)}
        self._save(data)

    def set_password(self, name, password):
        check_password(password)
        data = self._load()
        if name not in data["users"]:
            raise HostingError("That user does not exist.")
        data["users"][name]["password_hash"] = make_password(password)
        self._save(data)

    def delete_user(self, name):
        data = self._load()
        if name not in data["users"]:
            raise HostingError("That user does not exist.")
        del data["users"][name]
        data["grants"] = [g for g in data["grants"] if g["user"] != name]
        self._save(data)

    def grant(self, database, user, privileges):
        data = self._load()
        if database not in data["databases"]:
            raise HostingError("That database does not exist.")
        if user not in data["users"]:
            raise HostingError("That user does not exist.")
        privileges = list(dict.fromkeys(privileges or []))
        unknown = [p for p in privileges if p not in DATABASE_PRIVILEGES]
        if unknown:
            raise HostingError("Choose from the listed privileges only.")
        data["grants"] = [g for g in data["grants"] if not (g["database"] == database and g["user"] == user)]
        if privileges:  # an empty list takes the access away
            data["grants"].append({"user": user, "database": database, "privileges": privileges})
        self._save(data)

    def password_matches(self, name, password):
        """For tests only: lets us prove the password is checked against a hash, not kept in the clear."""
        from django.contrib.auth.hashers import check_password as _check

        user = self._load()["users"].get(name)
        return bool(user) and _check(password, user["password_hash"])


# --- Sample tables, so the browser has something to show --------------------------------------------------------

FIRST = ["Ayesha", "Bilal", "Carla", "Daniyal", "Esha", "Farhan", "Gul", "Hamza", "Iram", "Junaid", "Kiran", "Laiba"]
LAST = ["Khan", "Ahmed", "Malik", "Sheikh", "Raza", "Butt", "Qureshi", "Chaudhry"]
ITEMS = ["Hosting plan", "Domain .com", "SSL certificate", "Email pack", "Backup add-on", "Dedicated IP"]


def _rows_wp(table, count):
    if table == "wp_users":
        return ["ID", "user_login", "user_email", "user_registered"], [
            [i + 1, f"member{i + 1}", f"member{i + 1}@example.com", f"2026-0{1 + i % 9}-1{i % 9}"] for i in range(count)]
    if table == "wp_posts":
        return ["ID", "post_title", "post_status", "post_date"], [
            [i + 1, f"Sample post number {i + 1}", ("publish", "draft")[i % 4 == 3], f"2026-0{1 + i % 9}-2{i % 8}"]
            for i in range(count)]
    return ["option_id", "option_name", "option_value"], [
        [i + 1, f"sample_option_{i + 1}", f"value-{i * 7 % 13}"] for i in range(count)]


def _rows_shop(table, count):
    if table == "customers":
        return ["id", "name", "email", "city"], [
            [i + 1, f"{FIRST[i % len(FIRST)]} {LAST[i % len(LAST)]}", f"customer{i + 1}@example.com",
             ("Lahore", "Karachi", "Islamabad")[i % 3]] for i in range(count)]
    if table == "orders":
        return ["id", "customer_id", "total", "status", "created_at"], [
            [i + 1, 1 + i % 17, f"{(i * 37) % 400 + 20}.00", ("paid", "pending", "paid", "refunded")[i % 4],
             f"2026-0{1 + i % 9}-0{1 + i % 8}"] for i in range(count)]
    return ["id", "name", "price", "stock"], [
        [i + 1, ITEMS[i % len(ITEMS)] + f" #{i + 1}", f"{(i * 11) % 90 + 5}.00", (i * 5) % 40] for i in range(count)]


def schema_for(database):
    """The sample tables of a demo database: WordPress-style when the name says so, shop-style otherwise."""
    seed = sum(map(ord, database))
    if "wp" in database:
        names, maker = ["wp_options", "wp_posts", "wp_users"], _rows_wp
    else:
        names, maker = ["customers", "orders", "products"], _rows_shop
    return {name: (maker, 12 + (seed + n * 13) % 40) for n, name in enumerate(names)}


def sample_tables(database):
    """{table: (columns, rows)} for a new demo database: WordPress-style or shop-style sample data."""
    return {name: maker(name, count) for name, (maker, count) in schema_for(database).items()}
