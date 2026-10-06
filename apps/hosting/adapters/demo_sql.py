"""
A real, sandboxed SQL engine for the demo provider's databases: each demo database is its own SQLite file. SQLite is not
MySQL (the dialect differs a little, and the screen says so), but queries, inserts, edits and deletes genuinely run, so
the database manager can be built and tested without any server.

What keeps it safe (a customer may type any SQL):
* one file per database, opened with an authorizer that refuses ATTACH, DETACH, PRAGMA and virtual tables (and SQLite itself
  refuses direct changes to its own catalogue) - the SQL can
  never reach another database, another account's files, or the settings of the engine itself;
* one statement per run; a time limit (it is aborted after a few seconds); a size cap on the file; results capped at
  ``MAX_RESULT_ROWS`` rows; binary values shown as a size, never dumped;
* row editing (insert, update, delete) is built from VALIDATED names and bound parameters - never by gluing text into SQL;
* the account state and the database files live outside the website's folder.
"""
import sqlite3
import time
from pathlib import Path

from .base import HostingError
from .capabilities import ColumnInfo, QueryResult, TableData, TableInfo

MAX_RESULT_ROWS = 200
MAX_PAGE_ROWS = 100
MAX_SQL_LENGTH = 20_000
MAX_DB_BYTES = 50_000_000
TIME_LIMIT_SECONDS = 3.0

_DENIED = {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA, sqlite3.SQLITE_CREATE_VTABLE,
           sqlite3.SQLITE_DROP_VTABLE}


def _authorizer(action, arg1, arg2, db_name, source):
    if action in _DENIED:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and str(arg2).lower() in ("load_extension", "readfile", "writefile"):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def _show(value):
    if value is None:
        return "NULL"
    if isinstance(value, bytes):
        return f"[binary {len(value)} bytes]"
    return str(value)


def _cell(value):
    """A value for the data grid and the edit form: NULL stays None (so it can be told from the text "NULL")."""
    if value is None:
        return None
    if isinstance(value, bytes):
        return f"[binary {len(value)} bytes]"
    return str(value)


class SqlDatabase:
    def __init__(self, path):
        self.path = Path(path)

    # --- connections --------------------------------------------------------------------------------------------

    def _open(self, *, restricted):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5)
        pages = max(1, MAX_DB_BYTES // max(1, conn.execute("PRAGMA page_size").fetchone()[0]))
        conn.execute(f"PRAGMA max_page_count = {int(pages)}")
        if restricted:
            conn.set_authorizer(_authorizer)
            started = time.monotonic()
            conn.set_progress_handler(lambda: 1 if time.monotonic() - started > TIME_LIMIT_SECONDS else 0, 20_000)
        return conn

    def exists(self):
        return self.path.exists()

    def size_mb(self):
        return round(self.path.stat().st_size / 1_000_000, 2) if self.path.exists() else 0.0

    def delete_file(self):
        self.path.unlink(missing_ok=True)

    # --- seeding --------------------------------------------------------------------------------------------------

    def seed(self, tables):
        """``tables``: {name: (columns, rows)}; the first column is the primary key."""
        conn = self._open(restricted=False)
        try:
            for name, (columns, rows) in tables.items():
                defs = []
                for i, col in enumerate(columns):
                    kind = "INTEGER PRIMARY KEY" if i == 0 else (
                        "NUMERIC" if col in ("price", "total", "stock", "customer_id") else "TEXT")
                    defs.append(f"{quote(col)} {kind}")
                conn.execute(f"CREATE TABLE IF NOT EXISTS {quote(name)} ({', '.join(defs)})")
                marks = ", ".join("?" for _ in columns)
                conn.executemany(f"INSERT INTO {quote(name)} VALUES ({marks})", rows)
            conn.commit()
        finally:
            conn.close()

    # --- reading ---------------------------------------------------------------------------------------------------

    def _table_names(self, conn):
        return [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]

    def _known_table(self, conn, table):
        if table not in self._table_names(conn):
            raise HostingError("That table does not exist.")
        return table

    def tables(self):
        conn = self._open(restricted=False)
        try:
            return [TableInfo(name=t, rows=conn.execute(f"SELECT COUNT(*) FROM {quote(t)}").fetchone()[0], size_kb=0)
                    for t in self._table_names(conn)]
        finally:
            conn.close()

    def describe(self, table):
        conn = self._open(restricted=False)
        try:
            self._known_table(conn, table)
            info = conn.execute(f"PRAGMA table_info({quote(table)})").fetchall()
            return [ColumnInfo(name=r[1], type=r[2] or "TEXT", nullable=not r[3] and not r[5], primary_key=bool(r[5]),
                               default=r[4]) for r in info]
        finally:
            conn.close()

    def browse(self, table, offset, limit):
        try:
            offset, limit = int(offset), int(limit)
        except (TypeError, ValueError):
            raise HostingError("That page does not exist.")
        if offset < 0 or limit < 1:
            raise HostingError("That page does not exist.")
        limit = min(limit, MAX_PAGE_ROWS)
        conn = self._open(restricted=False)
        try:
            self._known_table(conn, table)
            columns = self.describe(table)
            total = conn.execute(f"SELECT COUNT(*) FROM {quote(table)}").fetchone()[0]
            cur = conn.execute(f"SELECT * FROM {quote(table)} ORDER BY rowid LIMIT ? OFFSET ?", (limit, offset))
            return TableData(columns=[c.name for c in columns], rows=[[_cell(v) for v in row] for row in cur.fetchall()],
                             total=total, key_columns=[c.name for c in columns if c.primary_key])
        finally:
            conn.close()

    # --- the SQL console -------------------------------------------------------------------------------------------

    def run(self, sql):
        sql = (sql or "").strip().rstrip(";").strip()
        if not sql:
            raise HostingError("Type a SQL statement to run.")
        if len(sql) > MAX_SQL_LENGTH:
            raise HostingError("That statement is too long.")
        conn = self._open(restricted=True)
        try:
            try:
                cur = conn.execute(sql)
                if cur.description:
                    fetched = cur.fetchmany(MAX_RESULT_ROWS + 1)
                    conn.commit()
                    return QueryResult(columns=[d[0] for d in cur.description],
                                       rows=[[_show(v) for v in row] for row in fetched[:MAX_RESULT_ROWS]],
                                       affected=0, truncated=len(fetched) > MAX_RESULT_ROWS,
                                       message=f"{min(len(fetched), MAX_RESULT_ROWS)} row(s) returned.")
                conn.commit()
                return QueryResult(columns=[], rows=[], affected=max(cur.rowcount, 0), truncated=False,
                                   message=f"Done. {max(cur.rowcount, 0)} row(s) affected.")
            except (sqlite3.Warning, sqlite3.ProgrammingError) as exc:
                if "one statement" in str(exc):
                    raise HostingError("Run one statement at a time.")
                raise HostingError(f"SQL error: {exc}")
            except sqlite3.OperationalError as exc:
                if "interrupted" in str(exc):
                    raise HostingError("That query took too long and was stopped.")
                if "database or disk is full" in str(exc):
                    raise HostingError("Your database has reached its size limit.")
                raise HostingError(f"SQL error: {exc}")
            except sqlite3.DatabaseError as exc:
                raise HostingError(f"SQL error: {exc}")
        finally:
            conn.close()

    # --- row editing (names validated, values bound) ---------------------------------------------------------------

    def _columns(self, conn, table):
        self._known_table(conn, table)
        return {r[1]: r for r in conn.execute(f"PRAGMA table_info({quote(table)})").fetchall()}

    def _clean_values(self, columns, values, *, for_insert):
        unknown = [c for c in values if c not in columns]
        if unknown:
            raise HostingError("That column does not exist.")
        cleaned = {}
        for name, raw in values.items():
            col = columns[name]
            if raw is None or (raw == "" and (col[2] or "TEXT").upper() != "TEXT"):
                cleaned[name] = None
            else:
                cleaned[name] = raw
        if for_insert:
            for name, col in columns.items():
                if name in cleaned and cleaned[name] is None and col[3] and not col[5] and col[4] is None:
                    raise HostingError(f"'{name}' cannot be empty.")
        return cleaned

    def _where(self, columns, key):
        pk = [name for name, col in columns.items() if col[5]]
        if not pk:
            raise HostingError("This table has no primary key, so its rows cannot be edited one by one. Use SQL.")
        if set(key) != set(pk):
            raise HostingError("That row could not be identified.")
        return " AND ".join(f"{quote(n)} = ?" for n in pk), [key[n] for n in pk]

    def insert_row(self, table, values):
        conn = self._open(restricted=False)
        try:
            columns = self._columns(conn, table)
            cleaned = self._clean_values(columns, values, for_insert=True)
            if not cleaned:
                raise HostingError("Fill in at least one value.")
            names = ", ".join(quote(n) for n in cleaned)
            marks = ", ".join("?" for _ in cleaned)
            try:
                conn.execute(f"INSERT INTO {quote(table)} ({names}) VALUES ({marks})", list(cleaned.values()))
                conn.commit()
            except sqlite3.DatabaseError as exc:
                raise HostingError(f"The row could not be added: {exc}")
        finally:
            conn.close()

    def update_row(self, table, key, values):
        conn = self._open(restricted=False)
        try:
            columns = self._columns(conn, table)
            where, params = self._where(columns, key)
            cleaned = self._clean_values(columns, values, for_insert=False)
            if not cleaned:
                raise HostingError("Nothing to change.")
            sets = ", ".join(f"{quote(n)} = ?" for n in cleaned)
            try:
                cur = conn.execute(f"UPDATE {quote(table)} SET {sets} WHERE {where}", list(cleaned.values()) + params)
                conn.commit()
            except sqlite3.DatabaseError as exc:
                raise HostingError(f"The row could not be changed: {exc}")
            if cur.rowcount == 0:
                raise HostingError("That row no longer exists.")
        finally:
            conn.close()

    def delete_row(self, table, key):
        conn = self._open(restricted=False)
        try:
            columns = self._columns(conn, table)
            where, params = self._where(columns, key)
            cur = conn.execute(f"DELETE FROM {quote(table)} WHERE {where}", params)
            conn.commit()
            if cur.rowcount == 0:
                raise HostingError("That row no longer exists.")
        finally:
            conn.close()
