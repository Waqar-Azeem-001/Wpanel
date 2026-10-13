"""
The one-file malware scanner for servers that cannot run our agent (cPanel/WHM today).

Like the WordPress installer, WHPanel writes ONE small PHP file into the website's folder and calls it once with a secret token. It removes itself first, walks the folder (not following
links, at most 5,000 files, 1 MB of each), applies the rules in ``scan_rules`` and, on a WordPress site, compares WordPress's own system files with the checksums wordpress.org publishes
for that exact version. It writes a small result file (paths and rule names only, never file contents) that the panel reads and deletes.

It only reads: it never changes or removes a file of the website.
"""
import base64
import hashlib
import json
import secrets

from . import scan_rules

PHP = r"""<?php
/* WHPanel one-time malware scan. It removes itself when it starts and only reads. */
@ignore_user_abort(true);
@set_time_limit(120);
header('Content-Type: application/json');
const WHP_TOKEN_HASH = '__TOKEN_HASH__';
const WHP_RESULT = '__RESULT__';
const WHP_SELF = '__SELF__';
$whp = json_decode(base64_decode('__CONFIG__'), true);

function whp_finish($ok, $extra = array()) {
    $body = json_encode(array_merge(array('ok' => $ok), $extra));
    @file_put_contents(__DIR__ . '/' . WHP_RESULT, $body);
    echo $body;
    exit;
}
function whp_name($rel) { $p = explode('/', $rel); return strtolower(end($p)); }

if (!hash_equals(WHP_TOKEN_HASH, hash('sha256', isset($_GET['t']) ? (string)$_GET['t'] : ''))) {
    http_response_code(403);
    echo '{"ok":false}';
    exit;
}
@unlink(__FILE__); // one use: it cannot be run again

try {
    $root = __DIR__;
    $findings = array();
    $scanned = 0;
    $seen = array();
    $skipDirs = array('.git' => 1, 'node_modules' => 1, '.trash' => 1, 'cgi-bin' => 0);
    $stack = array('');
    while ($stack && $scanned < $whp['max_files']) {
        $rel = array_pop($stack);
        $dir = $rel === '' ? $root : $root . '/' . $rel;
        $names = @scandir($dir);
        if ($names === false) { continue; }
        foreach ($names as $name) {
            if ($name === '.' || $name === '..') { continue; }
            $childRel = $rel === '' ? $name : $rel . '/' . $name;
            $full = $root . '/' . $childRel;
            if (is_link($full)) { continue; }
            if (is_dir($full)) {
                if (!isset($skipDirs[$name]) || $skipDirs[$name] === 0) { $stack[] = $childRel; }
                continue;
            }
            if ($childRel === WHP_SELF || $childRel === WHP_RESULT) { continue; }
            $scanned++;
            if ($scanned > $whp['max_files']) { break 2; }
            $lower = strtolower($name);
            $suffix = strtolower(strrchr($name, '.') ?: '');
            $parts = array_map('strtolower', explode('/', $rel));
            if (in_array($lower, $whp['shell_names'], true)) {
                $findings[] = array('path' => $childRel, 'rule' => 'known-shell-name', 'severity' => 'high', 'detail' => $whp['names']['known-shell-name'][1]);
            }
            if (preg_match('~\.(php\d?|phtml)\.(jpe?g|png|gif|txt|ico)$~', $lower)) {
                $findings[] = array('path' => $childRel, 'rule' => 'double-extension', 'severity' => 'medium', 'detail' => $whp['names']['double-extension'][1]);
            }
            if (in_array($suffix, array('.php', '.phtml', '.php5'), true) && array_intersect($parts, $whp['upload_dirs'])) {
                $findings[] = array('path' => $childRel, 'rule' => 'script-in-uploads', 'severity' => 'medium', 'detail' => $whp['names']['script-in-uploads'][1]);
            }
            if ($name[0] === '.' && in_array($suffix, array('.php', '.phtml'), true)) {
                $findings[] = array('path' => $childRel, 'rule' => 'hidden-script-file', 'severity' => 'medium', 'detail' => $whp['names']['hidden-script-file'][1]);
            }
            if (in_array($suffix, $whp['web_ext'], true) || $lower === '.htaccess' || strpos($lower, '.php') !== false) {
                $data = @file_get_contents($full, false, null, 0, $whp['max_bytes']);
                if ($data !== false) {
                    foreach ($whp['rules'] as $rule) {
                        if (@preg_match('~' . $rule[2] . '~i', $data) === 1) {
                            $findings[] = array('path' => $childRel, 'rule' => $rule[0], 'severity' => $rule[1], 'detail' => $rule[3]);
                        }
                    }
                }
            }
        }
    }

    $wpVersion = '';
    $wpChecked = 0;
    $versionFile = $root . '/wp-includes/version.php';
    if (is_file($versionFile)) {
        if (preg_match('/\$wp_version\s*=\s*\'([0-9][0-9a-zA-Z.\-]*)\'/', (string)@file_get_contents($versionFile, false, null, 0, 20000), $m)) { $wpVersion = $m[1]; }
        if ($wpVersion !== '') {
            $raw = @file_get_contents('__CHECKSUMS__' . rawurlencode($wpVersion) . '&locale=en_US', false,
                                      stream_context_create(array('http' => array('timeout' => 15, 'user_agent' => 'whpanel-scan'))));
            $official = json_decode((string)$raw, true);
            if (is_array($official) && isset($official['checksums']) && is_array($official['checksums'])) {
                foreach ($official['checksums'] as $file => $sum) {
                    if (strpos($file, 'wp-content/') === 0 || strpos($file, '..') !== false) { continue; }
                    $path = $root . '/' . $file;
                    if (!is_file($path)) { continue; }
                    $wpChecked++;
                    if (md5_file($path) !== $sum) {
                        $findings[] = array('path' => $file, 'rule' => 'core-file-modified', 'severity' => 'high', 'detail' => $whp['names']['core-file-modified'][1]);
                    }
                }
                foreach (array('wp-admin', 'wp-includes') as $core) {
                    $it = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($root . '/' . $core, FilesystemIterator::SKIP_DOTS));
                    foreach ($it as $file) {
                        if (!$file->isFile() || $file->isLink() || strtolower($file->getExtension()) !== 'php') { continue; }
                        $rel = str_replace('\\', '/', substr($file->getPathname(), strlen($root) + 1));
                        if (!isset($official['checksums'][$rel])) {
                            $findings[] = array('path' => $rel, 'rule' => 'unknown-file-in-core', 'severity' => 'medium', 'detail' => $whp['names']['unknown-file-in-core'][1]);
                        }
                    }
                }
            }
        }
    }
    usort($findings, function ($a, $b) {
        $o = array('high' => 0, 'medium' => 1);
        return array($o[$a['severity']], $a['path'], $a['rule']) <=> array($o[$b['severity']], $b['path'], $b['rule']);
    });
    whp_finish(true, array('files_scanned' => $scanned, 'findings' => array_slice($findings, 0, 200), 'wp_version' => $wpVersion, 'wp_checked' => $wpChecked,
                           'truncated' => $scanned >= $whp['max_files']));
} catch (Exception $e) {
    whp_finish(false, array('error' => 'The scan could not finish.'));
}
"""

CHECKSUMS = "https" + "://api.wordpress.org/core/checksums/1.0/?version="


def build():
    """Returns (file name, result file name, token, PHP source). The token is only in the panel's hands; the file holds its hash."""
    token = secrets.token_urlsafe(32)
    suffix = secrets.token_hex(8)
    file_name, result_name = f"whp-scan-{suffix}.php", f"whp-scan-{suffix}.json"
    config = {
        "max_files": scan_rules.MAX_SCAN_FILES, "max_bytes": scan_rules.MAX_SCAN_BYTES, "web_ext": list(scan_rules.WEB_EXTENSIONS),
        "shell_names": list(scan_rules.SHELL_NAMES), "upload_dirs": list(scan_rules.UPLOAD_DIRS),
        "rules": [list(rule) for rule in scan_rules.RULES],
        "names": {key: list(value) for key, value in scan_rules.NAME_RULES.items()},
    }
    source = (PHP.replace("__TOKEN_HASH__", hashlib.sha256(token.encode()).hexdigest()).replace("__RESULT__", result_name).replace("__SELF__", file_name)
              .replace("__CHECKSUMS__", CHECKSUMS).replace("__CONFIG__", base64.b64encode(json.dumps(config).encode()).decode()))
    return file_name, result_name, token, source
