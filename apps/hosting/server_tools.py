"""
Two one-file tools that need to run on the website's own server: PHP's information, and putting a website's folder and file permissions back to safe values.

Like the installer and the WordPress tool, each is ONE small PHP file written into the website's folder, called once with a secret token, removing itself first, and answering with a
small JSON body that this module turns into figures for the page. Nothing here changes who owns a file (a customer account cannot), and the permission tool never touches anything
outside the website's own folder or follows a link out of it.
"""
import base64
import hashlib
import json
import secrets

from apps.audit import services as audit
from apps.core.exceptions import ServiceError

from . import care, site_apps
from .adapters.capabilities import Capability

PHP = r"""<?php
/* WHPanel one-time server tool. It removes itself when it starts. */
@ignore_user_abort(true);
@set_time_limit(120);
header('Content-Type: application/json');
const WHP_TOKEN_HASH = '__TOKEN_HASH__';
$cfg = json_decode(base64_decode('__CONFIG__'), true);
function whp_out($d) { echo json_encode($d, JSON_INVALID_UTF8_SUBSTITUTE | JSON_PARTIAL_OUTPUT_ON_ERROR); exit; }
if (!hash_equals(WHP_TOKEN_HASH, hash('sha256', isset($_GET['t']) ? (string)$_GET['t'] : ''))) { http_response_code(403); echo '{"ok":false}'; exit; }
@unlink(__FILE__);

if ($cfg['action'] === 'phpinfo') {
    $keys = array('memory_limit', 'max_execution_time', 'max_input_time', 'max_input_vars', 'post_max_size', 'upload_max_filesize', 'max_file_uploads', 'display_errors', 'error_reporting',
                  'date.timezone', 'default_charset', 'file_uploads', 'allow_url_fopen', 'open_basedir', 'disable_functions', 'session.save_path', 'opcache.enable', 'short_open_tag');
    $ini = array();
    foreach ($keys as $k) { $v = ini_get($k); $ini[$k] = ($v === false) ? '' : (string)$v; }
    $ext = get_loaded_extensions();
    natcasesort($ext);
    whp_out(array('ok' => true, 'version' => PHP_VERSION, 'sapi' => php_sapi_name(), 'os' => PHP_OS, 'zend' => zend_version(), 'ini' => $ini, 'extensions' => array_values($ext),
                  'loaded_ini' => (string)php_ini_loaded_file(), 'user' => function_exists('get_current_user') ? get_current_user() : ''));
}

if ($cfg['action'] === 'permissions') {
    $root = realpath(__DIR__);
    $dirs = 0; $files = 0; $fixed = 0; $skipped = 0; $limit = 200000; $seen = 0;
    $walk = function ($dir) use (&$walk, &$dirs, &$files, &$fixed, &$skipped, &$seen, $limit, $root, $cfg) {
        $items = @scandir($dir);
        if ($items === false) { return; }
        foreach ($items as $name) {
            if ($name === '.' || $name === '..') { continue; }
            if (++$seen > $limit) { return; }
            $path = $dir . '/' . $name;
            if (is_link($path)) { $skipped++; continue; }
            $real = realpath($path);
            if ($real === false || strpos($real, $root) !== 0) { $skipped++; continue; }
            if (is_dir($path)) {
                $dirs++;
                if ((fileperms($path) & 0777) !== 0755 && @chmod($path, 0755)) { $fixed++; }
                $walk($path);
            } elseif (is_file($path)) {
                $files++;
                $want = ($name === 'wp-config.php') ? 0640 : 0644;
                if ((fileperms($path) & 0777) !== $want && @chmod($path, $want)) { $fixed++; }
            }
        }
    };
    $walk($root);
    if ((fileperms($root) & 0777) !== 0755) { @chmod($root, 0755); }
    whp_out(array('ok' => true, 'folders' => $dirs, 'files' => $files, 'fixed' => $fixed, 'skipped' => $skipped, 'complete' => $seen <= $limit));
}
whp_out(array('ok' => false, 'error' => 'Unknown action.'));
"""

ACTIONS = ("phpinfo", "permissions")


def _build(action):
    token = secrets.token_urlsafe(32)
    name = f"whp-tool-{secrets.token_hex(8)}.php"
    blob = base64.b64encode(json.dumps({"action": action}).encode()).decode()
    source = PHP.replace("__TOKEN_HASH__", hashlib.sha256(token.encode()).hexdigest()).replace("__CONFIG__", blob)
    return name, token, source


def run(user, account, action):
    """Run one of ``ACTIONS`` on the plan's own website and return the tool's figures. Raises ServiceError when the server cannot be reached."""
    if action not in ACTIONS:
        raise ServiceError("That tool is not available.", code="unknown_action")
    io = care._Io(user, account)
    folder = site_apps.docroot_path(account)
    name, token, source = _build(action)
    path = f"{folder}/{name}"
    io.call(Capability.FILES, "write_file", path, source.encode(), overwrite=False, audit_metadata={"path": path, "purpose": "one-time server tool", "action": action})
    try:
        data = site_apps.reach(account, name, token, purpose="to run a server tool")
    except ServiceError:
        data = None
        raise
    finally:
        try:
            io.call(Capability.FILES, "delete_path", path)  # the tool removes itself; this is only the tidy-up when it never ran
        except ServiceError:
            pass
    if not isinstance(data, dict) or not data.get("ok"):
        raise ServiceError(str((data or {}).get("error") or "The server tool did not finish. Try again in a moment.")[:300], code="tool_failed")
    audit.record("hosting.server_tool", actor=user, target=account, metadata={"action": action})
    return data
