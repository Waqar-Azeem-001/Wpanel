"""
Looking inside a database on a cPanel server without phpMyAdmin and without opening MySQL to the internet: ONE small PHP file is written into the account's main web folder,
called once with a secret token and removing itself first. It connects from inside the server (where MySQL listens on ``localhost``), does exactly one thing named in its
(hash-protected) configuration, and answers with a small JSON body.

It connects with the WordPress website's own credentials when the database is that website's (read from its ``wp-config.php`` there, so no new user is needed). For any other
database a temporary MySQL user is made, given access to just that database and deleted again straight after, whatever happens.

Only reading and typed SQL are offered; changing single rows is not (the table's key columns are reported empty, so the screen offers no row editing).
"""
import base64
import hashlib
import json
import secrets
import socket
import string

import requests

from .base import HostingError
from .capabilities import ColumnInfo, QueryResult, TableData, TableInfo

PAGE_LIMIT = 100
SQL_ROWS = 200

PHP = r"""<?php
/* WHPanel one-time database tool. It removes itself when it starts. */
@ignore_user_abort(true);
@set_time_limit(60);
header('Content-Type: application/json');
const WHP_TOKEN_HASH = '__TOKEN_HASH__';
$cfg = json_decode(base64_decode('__CONFIG__'), true);

function whp_out($data) {
    echo json_encode($data, JSON_INVALID_UTF8_SUBSTITUTE | JSON_PARTIAL_OUTPUT_ON_ERROR);
    exit;
}
function whp_const($src, $name) {
    if (preg_match("/define\s*\(\s*['\"]" . $name . "['\"]\s*,\s*'((?:[^'\\\\]|\\\\.)*)'/s", $src, $m)) { return stripslashes($m[1]); }
    if (preg_match("/define\s*\(\s*['\"]" . $name . "['\"]\s*,\s*\"((?:[^\"\\\\]|\\\\.)*)\"/s", $src, $m)) { return stripslashes($m[1]); }
    return null;
}
function whp_q($m, $sql) {
    $r = $m->query($sql);
    if ($r === false) { whp_out(array('ok' => false, 'error' => $m->error)); }
    return $r;
}
function whp_cell($v) {
    if ($v === null) { return null; }
    $v = (string)$v;
    return strlen($v) > 2000 ? substr($v, 0, 2000) . '...' : $v;
}

if (!hash_equals(WHP_TOKEN_HASH, hash('sha256', isset($_GET['t']) ? (string)$_GET['t'] : ''))) {
    http_response_code(403);
    echo '{"ok":false}';
    exit;
}
@unlink(__FILE__); // one use

mysqli_report(MYSQLI_REPORT_OFF);
$host = 'localhost';
$user = isset($cfg['user']) ? (string)$cfg['user'] : '';
$pass = isset($cfg['password']) ? (string)$cfg['password'] : '';
if ($cfg['mode'] === 'wpconfig') {
    $file = __DIR__ . '/wp-config.php';
    if (!is_file($file)) { whp_out(array('ok' => false, 'code' => 'no_wpconfig')); }
    $src = (string)@file_get_contents($file);
    if (whp_const($src, 'DB_NAME') !== $cfg['db']) { whp_out(array('ok' => false, 'code' => 'no_wpconfig')); }
    $user = (string)whp_const($src, 'DB_USER');
    $pass = (string)whp_const($src, 'DB_PASSWORD');
    $h = whp_const($src, 'DB_HOST');
    if ($h !== null && $h !== '') { $host = $h; }
}
$db = @new mysqli($host, $user, $pass, $cfg['db']);
if ($db->connect_errno) { whp_out(array('ok' => false, 'code' => 'connect', 'error' => 'The database refused the connection (' . $db->connect_errno . ').')); }
$db->set_charset('utf8mb4');

$action = $cfg['action'];
$names = array();
$res = whp_q($db, 'SHOW TABLES');
while ($row = $res->fetch_row()) { $names[] = $row[0]; }

if ($action === 'tables') {
    $out = array();
    $res = whp_q($db, "SELECT TABLE_NAME, IFNULL(TABLE_ROWS, 0), ROUND((IFNULL(DATA_LENGTH, 0) + IFNULL(INDEX_LENGTH, 0)) / 1024) FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME");
    while ($row = $res->fetch_row()) { $out[] = array('name' => $row[0], 'rows' => (int)$row[1], 'size_kb' => (int)$row[2]); }
    whp_out(array('ok' => true, 'tables' => $out));
}

if ($action === 'query') {
    $r = $db->query((string)$cfg['sql']);
    if ($r === false) { whp_out(array('ok' => false, 'error' => $db->error)); }
    if ($r instanceof mysqli_result) {
        $cols = array();
        foreach ($r->fetch_fields() as $f) { $cols[] = $f->name; }
        $rows = array();
        $truncated = false;
        while ($row = $r->fetch_row()) {
            if (count($rows) >= (int)$cfg['max_rows']) { $truncated = true; break; }
            $rows[] = array_map('whp_cell', $row);
        }
        whp_out(array('ok' => true, 'columns' => $cols, 'rows' => $rows, 'truncated' => $truncated, 'affected' => 0));
    }
    whp_out(array('ok' => true, 'columns' => array(), 'rows' => array(), 'truncated' => false, 'affected' => (int)$db->affected_rows));
}

$table = (string)$cfg['table'];
if (!in_array($table, $names, true)) { whp_out(array('ok' => false, 'error' => 'That table does not exist.')); }
$t = '`' . str_replace('`', '``', $table) . '`';

if ($action === 'columns') {
    $out = array();
    $res = whp_q($db, 'SHOW FULL COLUMNS FROM ' . $t);
    while ($row = $res->fetch_assoc()) {
        $out[] = array('name' => $row['Field'], 'type' => $row['Type'], 'nullable' => $row['Null'] === 'YES', 'primary_key' => $row['Key'] === 'PRI', 'default' => $row['Default']);
    }
    whp_out(array('ok' => true, 'columns' => $out));
}

if ($action === 'browse') {
    $total = (int)whp_q($db, 'SELECT COUNT(*) FROM ' . $t)->fetch_row()[0];
    $res = whp_q($db, 'SELECT * FROM ' . $t . ' LIMIT ' . (int)$cfg['offset'] . ', ' . (int)$cfg['limit']);
    $cols = array();
    foreach ($res->fetch_fields() as $f) { $cols[] = $f->name; }
    $rows = array();
    while ($row = $res->fetch_row()) { $rows[] = array_map('whp_cell', $row); }
    whp_out(array('ok' => true, 'columns' => $cols, 'rows' => $rows, 'total' => $total));
}

whp_out(array('ok' => false, 'error' => 'Unknown action.'));
"""


def _build(config):
    token = secrets.token_urlsafe(32)
    name = f"whp-db-{secrets.token_hex(8)}.php"
    blob = base64.b64encode(json.dumps(config).encode()).decode()
    source = PHP.replace("__TOKEN_HASH__", hashlib.sha256(token.encode()).hexdigest()).replace("__CONFIG__", blob)
    return name, token, source


def _password():
    pool = string.ascii_letters + string.digits
    word = "".join(secrets.choice(pool) for _ in range(22))
    return word + secrets.choice(string.ascii_uppercase) + secrets.choice(string.digits)


class CpanelDbTool:
    """Mixed into the cPanel MySQL adapter: tables, columns, rows and typed SQL of one database."""

    def _dbtool_http(self, username, config):
        domains = self._account_domains(username)
        main = domains[0] if domains else ""
        if not main:
            raise HostingError("The main domain of this account could not be found.")
        name, token, source = _build(config)
        path = f"public_html/{name}"
        self.write_file(username, path, source.encode(), overwrite=False)
        try:
            try:
                address = socket.gethostbyname(self.host)
            except OSError as exc:
                raise HostingError("The hosting server could not be reached.") from exc
            try:
                reply = requests.get("http" + f"://{address}/{name}", params={"t": token}, headers={"Host": main}, timeout=(8, 45), allow_redirects=False)
                data = reply.json()
            except (requests.exceptions.RequestException, ValueError) as exc:
                raise HostingError("The database tool could not be reached on your hosting server.") from exc
            if not isinstance(data, dict):
                raise HostingError("The database tool answered in a way we do not understand.")
            return data
        finally:
            try:
                self.delete_path(username, path)  # the tool removes itself; this is only the tidy-up when it never ran
            except HostingError:
                pass

    def _dbtool(self, username, database, **params):
        base = {"db": database, "max_rows": SQL_ROWS, **params}
        data = self._dbtool_http(username, {**base, "mode": "wpconfig"})
        if data.get("code") == "no_wpconfig":
            data = self._dbtool_as_temporary_user(username, database, base)
        if not data.get("ok"):
            raise HostingError(str(data.get("error") or "The database could not be read.")[:300])
        return data

    def _dbtool_as_temporary_user(self, username, database, base):
        """Another database than the WordPress one: a short-lived MySQL user with access to just this database."""
        suffix = "whp" + secrets.token_hex(2)
        password = _password()
        full = self._full(username, suffix)
        self.create_database_user(username, suffix, password)
        try:
            self.grant_database_access(username, database, full, ["ALL"])
            return self._dbtool_http(username, {**base, "mode": "user", "user": full, "password": password})
        finally:
            try:
                self.delete_database_user(username, full)
            except HostingError:
                pass

    # --- the operations the database screen asks for -----------------------------------------------------------------
    def list_tables(self, username, database):
        data = self._dbtool(username, database, action="tables")
        return [TableInfo(name=t["name"], rows=int(t.get("rows") or 0), size_kb=int(t.get("size_kb") or 0)) for t in data.get("tables", [])]

    def describe_table(self, username, database, table):
        data = self._dbtool(username, database, action="columns", table=table)
        return [ColumnInfo(name=c["name"], type=c.get("type") or "", nullable=bool(c.get("nullable")), primary_key=bool(c.get("primary_key")), default=c.get("default"))
                for c in data.get("columns", [])]

    def browse_table(self, username, database, table, offset, limit):
        data = self._dbtool(username, database, action="browse", table=table, offset=max(0, int(offset)), limit=max(1, min(int(limit), PAGE_LIMIT)))
        return TableData(columns=list(data.get("columns", [])), rows=[list(r) for r in data.get("rows", [])], total=int(data.get("total") or 0), key_columns=[])

    def run_query(self, username, database, sql):
        sql = (sql or "").strip().rstrip(";").strip()
        if not sql:
            raise HostingError("Type one SQL statement.")
        if ";" in sql:
            raise HostingError("Run one statement at a time.")
        data = self._dbtool(username, database, action="query", sql=sql)
        return QueryResult(columns=list(data.get("columns", [])), rows=[list(r) for r in data.get("rows", [])], affected=int(data.get("affected") or 0),
                           truncated=bool(data.get("truncated")), message="" if data.get("columns") else f"{int(data.get('affected') or 0)} row(s) affected.")
