"""
The one-file WordPress copier behind staging: it copies a WordPress website (files and database) from one folder to another and rewrites its address on the way.

* ``create``: live -> staging. A new wp-config.php for the staging database, ``WP_HOME``/``WP_SITEURL`` set to the staging address, search engines asked not to index it.
* ``push``: staging -> live. Files and database tables are replaced by the staging copy, the live wp-config.php (and so the live database) is kept, and the live site's
  "discourage search engines" setting is kept as it was.

Like the installer and the scanner it is ONE small PHP file written into the website's folder and called once with a secret token; it removes itself first and writes a small
result file (no secrets) that the panel reads and deletes. It never receives anything from a visitor except the token. Database passwords for the target are inside its
hash-protected configuration for the seconds between being written and being run.

Addresses are rewritten inside serialized values correctly (lengths are recalculated), and in JSON-escaped form.
"""
import base64
import hashlib
import json
import secrets

from . import php_helpers

PHP = r"""<?php
/* WHPanel one-time WordPress copier. It removes itself when it starts. */
@ignore_user_abort(true);
@set_time_limit(280);
@ini_set('memory_limit', '512M');
header('Content-Type: application/json');
const WHP_TOKEN_HASH = '__TOKEN_HASH__';
const WHP_RESULT = '__RESULT__';
$whp = json_decode(base64_decode('__CONFIG__'), true);

function whp_finish($ok, $extra = array()) {
    $body = json_encode(array_merge(array('ok' => $ok), $extra));
    @file_put_contents(__DIR__ . '/' . WHP_RESULT, $body);
    echo $body;
    exit;
}
function whp_const($php, $name) {
    if (preg_match('/define\(\s*[\'"]' . preg_quote($name, '/') . '[\'"]\s*,\s*([\'"])((?:\\\\.|(?!\1).)*)\1\s*\)/s', $php, $m)) {
        return stripcslashes($m[2]);
    }
    return null;
}
function whp_creds($dir) {
    $file = $dir . '/wp-config.php';
    if (!is_file($file)) { throw new Exception('wp-config.php was not found in ' . basename($dir) . '.'); }
    $php = (string)file_get_contents($file);
    $c = array('name' => whp_const($php, 'DB_NAME'), 'user' => whp_const($php, 'DB_USER'), 'password' => whp_const($php, 'DB_PASSWORD'), 'host' => whp_const($php, 'DB_HOST'));
    if (!preg_match('/\$table_prefix\s*=\s*[\'"]([^\'"]+)[\'"]/', $php, $m)) { throw new Exception('The table prefix could not be read.'); }
    $c['prefix'] = $m[1];
    if ($c['name'] === null || $c['user'] === null || $c['host'] === null) { throw new Exception('The database settings could not be read.'); }
    return $c;
}
function whp_connect($c) {
    mysqli_report(MYSQLI_REPORT_OFF);
    $host = $c['host'];
    $port = null;
    $socket = null;
    if (preg_match('/^(.*):(\d+)$/', $host, $m)) { $host = $m[1]; $port = (int)$m[2]; }
    elseif (preg_match('/^(.*):(\/.*)$/', $host, $m)) { $host = $m[1]; $socket = $m[2]; }
    $db = @new mysqli($host, $c['user'], (string)$c['password'], $c['name'], $port, $socket);
    if ($db->connect_errno) { throw new Exception('Could not connect to the database ' . $c['name'] . '.'); }
    $db->set_charset('utf8mb4');
    return $db;
}
function whp_swap($value, $from, $to) {
    if (is_array($value)) {
        $out = array();
        foreach ($value as $k => $v) { $out[is_string($k) ? whp_swap($k, $from, $to) : $k] = whp_swap($v, $from, $to); }
        return $out;
    }
    if (is_object($value)) {
        foreach (get_object_vars($value) as $k => $v) { $value->$k = whp_swap($v, $from, $to); }
        return $value;
    }
    if (!is_string($value)) { return $value; }
    return str_replace(array($from, str_replace('/', '\/', $from)), array($to, str_replace('/', '\/', $to)), $value);
}
function whp_rewrite($value, $from, $to) {
    if (!is_string($value) || $from === $to) { return $value; }
    if (strpos($value, $from) === false && strpos($value, str_replace('/', '\/', $from)) === false) { return $value; }
    if (preg_match('/^(a|O|s|i|b|d|C):/', $value) || $value === 'N;') {
        $un = @unserialize($value);
        if ($un !== false || $value === 'b:0;') { return serialize(whp_swap($un, $from, $to)); }
        return $value; // looks serialized but is not readable: leave it rather than break its lengths
    }
    return whp_swap($value, $from, $to);
}
function whp_rm($path) {
    if (is_dir($path) && !is_link($path)) {
        foreach (scandir($path) as $n) { if ($n !== '.' && $n !== '..') { whp_rm($path . '/' . $n); } }
        @rmdir($path);
    } else { @unlink($path); }
}
function whp_copy($from, $to, $skipTop, $keepConfig, &$count, $top = true) {
    if (!is_dir($to) && !@mkdir($to, 0755, true)) { throw new Exception('Could not create ' . basename($to) . '.'); }
    foreach (scandir($from) as $name) {
        if ($name === '.' || $name === '..') { continue; }
        if ($top && in_array($name, $skipTop, true)) { continue; }
        if ($name === '.whp-quarantine' || $name === 'error_log' || (strpos($name, 'whp-') === 0 && preg_match('/\.(php|json)$/', $name))) { continue; }
        if ($top && $keepConfig && $name === 'wp-config.php') { continue; }
        $a = $from . '/' . $name;
        $b = $to . '/' . $name;
        if (is_link($a)) { continue; }
        if (is_dir($a)) {
            whp_copy($a, $b, $skipTop, $keepConfig, $count, false);
        } else {
            if (!@copy($a, $b)) { throw new Exception('Could not copy ' . $name . '.'); }
            $count++;
        }
    }
}

if (!hash_equals(WHP_TOKEN_HASH, hash('sha256', isset($_GET['t']) ? (string)$_GET['t'] : ''))) {
    http_response_code(403);
    echo '{"ok":false}';
    exit;
}
@unlink(__FILE__); // one use

try {
    $src = realpath(__DIR__ . '/' . $whp['src']);
    $dst = realpath(__DIR__ . '/' . $whp['dst']);
    if ($src === false || $dst === false || !is_dir($src) || !is_dir($dst) || $src === $dst) { whp_finish(false, array('error' => 'The folders to copy between were not found.')); }
    $push = $whp['action'] === 'push';
    $from = rtrim($whp['from_url'], '/');
    $to = rtrim($whp['to_url'], '/');

    $srcCreds = whp_creds($src);
    $dstCreds = $push ? whp_creds($dst) : array('name' => $whp['dst_db']['name'], 'user' => $whp['dst_db']['user'], 'password' => $whp['dst_db']['password'],
                                                 'host' => $srcCreds['host'], 'prefix' => $srcCreds['prefix']);
    $prefix = $srcCreds['prefix'];
    $in = whp_connect($srcCreds);
    $out = whp_connect($dstCreds);

    // what the target keeps as it was
    $keepPublic = null;
    if ($push) {
        $r = $out->query("SELECT option_value FROM `" . $prefix . "options` WHERE option_name = 'blog_public'");
        if ($r && ($row = $r->fetch_row())) { $keepPublic = $row[0]; }
    }

    // files
    $files = 0;
    if ($push) {
        // clear the live site's own files first (but not its configuration, its staging folder, or the server's own folders)
        foreach (scandir($dst) as $n) {
            if (in_array($n, array('.', '..', 'wp-config.php', 'cgi-bin', '.well-known', 'error_log', '.whp-quarantine'), true) || in_array($n, $whp['exclude'], true)) { continue; }
            if (strpos($n, 'whp-') === 0) { continue; }
            whp_rm($dst . '/' . $n);
        }
    }
    whp_copy($src, $dst, $whp['exclude'], $push, $files);

    // database
    $tables = array();
    $res = $in->query('SHOW TABLES');
    while ($res && ($row = $res->fetch_row())) { if (strpos($row[0], $prefix) === 0) { $tables[] = $row[0]; } }
    if (!$tables) { throw new Exception('No WordPress tables were found.'); }
    $rows = 0;
    $out->query('SET FOREIGN_KEY_CHECKS = 0');
    foreach ($tables as $t) {
        $out->query('DROP TABLE IF EXISTS `' . $t . '`');
        $c = $in->query('SHOW CREATE TABLE `' . $t . '`');
        $create = $c ? $c->fetch_row() : null;
        if (!$create || !$out->query($create[1])) { throw new Exception('Could not create the table ' . $t . '.'); }
        $offset = 0;
        while (true) {
            $r = $in->query('SELECT * FROM `' . $t . '` LIMIT ' . $offset . ', 400');
            if (!$r || $r->num_rows === 0) { break; }
            $fields = $r->fetch_fields();
            $names = array();
            foreach ($fields as $f) { $names[] = '`' . $f->name . '`'; }
            $values = array();
            while ($row = $r->fetch_row()) {
                $cells = array();
                foreach ($row as $i => $v) {
                    if ($v === null) { $cells[] = 'NULL'; continue; }
                    if ($fields[$i]->name !== 'guid') { $v = whp_rewrite($v, $from, $to); }
                    $cells[] = "'" . $out->real_escape_string($v) . "'";
                }
                $values[] = '(' . implode(',', $cells) . ')';
                $rows++;
            }
            if (!$out->query('INSERT INTO `' . $t . '` (' . implode(',', $names) . ') VALUES ' . implode(',', $values))) { throw new Exception('Could not copy rows into ' . $t . '.'); }
            $offset += 400;
        }
    }
    $out->query('SET FOREIGN_KEY_CHECKS = 1');

    $public = $push ? $keepPublic : (empty($whp['index']) ? '0' : null);  // a staging copy asks search engines to stay away; a copy meant to live keeps the source's choice
    if ($public !== null) {
        $out->query("UPDATE `" . $prefix . "options` SET option_value = '" . $out->real_escape_string($public) . "' WHERE option_name = 'blog_public'");
    }

    if (!$push) {
        $php = (string)file_get_contents($src . '/wp-config.php');
        foreach (array('DB_NAME' => $dstCreds['name'], 'DB_USER' => $dstCreds['user'], 'DB_PASSWORD' => $dstCreds['password']) as $k => $v) {
            $php = preg_replace_callback('/define\(\s*[\'"]' . $k . '[\'"]\s*,\s*([\'"])(?:\\\\.|(?!\1).)*\1\s*\)/s',
                function ($m) use ($k, $v) { return "define( '" . $k . "', " . var_export($v, true) . " )"; }, $php, 1);
        }
        $php = preg_replace('/^<\?php/', "<?php\ndefine( 'WP_HOME', " . var_export($to, true) . " );\ndefine( 'WP_SITEURL', " . var_export($to, true) . " );", $php, 1);
        if (@file_put_contents($dst . '/wp-config.php', $php) === false) { throw new Exception('Could not write the staging configuration.'); }
    }
    whp_finish(true, array('files' => $files, 'tables' => count($tables), 'rows' => $rows));
} catch (Exception $e) {
    whp_finish(false, array('error' => substr($e->getMessage(), 0, 250)));
}
"""


def build(*, action, src, dst, from_url, to_url, exclude=(), dst_db=None, index=False):
    """Returns (file name, result file name, token, PHP source). ``src`` and ``dst`` are folders relative to where the file is written."""
    if action not in ("create", "push"):
        raise ValueError("unknown action")
    token = secrets.token_urlsafe(32)
    suffix = secrets.token_hex(8)
    file_name, result_name = f"whp-clone-{suffix}.php", f"whp-clone-{suffix}.json"
    config = {"action": action, "src": src, "dst": dst, "from_url": from_url, "to_url": to_url, "exclude": list(exclude), "dst_db": dst_db or {}, "index": bool(index)}
    blob = base64.b64encode(json.dumps(config).encode()).decode()
    source = php_helpers.fill(PHP).replace("__TOKEN_HASH__", hashlib.sha256(token.encode()).hexdigest()).replace("__RESULT__", result_name).replace("__CONFIG__", blob)
    return file_name, result_name, token, source
