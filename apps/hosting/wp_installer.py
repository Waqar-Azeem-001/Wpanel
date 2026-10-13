"""
The one-file WordPress installer for servers that cannot run our agent (cPanel/WHM today).

WHPanel makes the database and user through the hosting-control layer, then writes ONE small PHP file into the website's folder that contains everything it needs (database name and
password, site title, administrator). The panel calls it once with a secret token; it removes itself first, downloads WordPress from wordpress.org, **checks the download against the
checksum wordpress.org publishes**, unpacks it, writes ``wp-config.php`` with fresh keys, installs WordPress with the administrator the customer chose, and writes a small result file
(no secrets) that WHPanel reads and deletes. If anything fails it removes what it unpacked.

Safety: the file only runs when called with the token (compared by hash); it refuses to run when the folder holds anything but a placeholder page; it deletes itself before doing
anything else, so it cannot be run twice; it contains secrets only for the seconds between being written and being run.
"""
import base64
import hashlib
import json
import secrets

SCHEME = "https"
WP_ZIP = SCHEME + "://wordpress.org/latest.zip"
WP_TAR = SCHEME + "://wordpress.org/latest.tar.gz"

PHP = r"""<?php
/* WHPanel one-time WordPress installer. It removes itself when it starts. */
@ignore_user_abort(true);
@set_time_limit(280);
header('Content-Type: application/json');
const WHP_TOKEN_HASH = '__TOKEN_HASH__';
const WHP_RESULT = '__RESULT__';
const WHP_ZIP = '__ZIP__';
const WHP_TAR = '__TAR__';
$whp = json_decode(base64_decode('__CONFIG__'), true);
$created = array();

function whp_finish($ok, $extra = array()) {
    $body = json_encode(array_merge(array('ok' => $ok), $extra));
    @file_put_contents(__DIR__ . '/' . WHP_RESULT, $body);
    echo $body;
    exit;
}
function whp_rm($path) {
    if (is_dir($path) && !is_link($path)) {
        foreach (scandir($path) as $name) { if ($name !== '.' && $name !== '..') { whp_rm($path . '/' . $name); } }
        @rmdir($path);
    } else {
        @unlink($path);
    }
}
function whp_undo() {
    global $created;
    foreach (array_reverse($created) as $path) { whp_rm($path); }
}
function whp_get($url) {
    if (function_exists('curl_init')) {
        $c = curl_init($url);
        curl_setopt_array($c, array(CURLOPT_RETURNTRANSFER => true, CURLOPT_FOLLOWLOCATION => true, CURLOPT_TIMEOUT => 150, CURLOPT_CONNECTTIMEOUT => 20,
                                    CURLOPT_SSL_VERIFYPEER => true, CURLOPT_USERAGENT => 'whpanel-installer'));
        $data = curl_exec($c);
        $code = curl_getinfo($c, CURLINFO_HTTP_CODE);
        curl_close($c);
        if ($data !== false && $code === 200) { return $data; }
        throw new Exception('Could not download WordPress. Try again in a few minutes.');
    }
    $data = @file_get_contents($url);
    if ($data === false) { throw new Exception('Could not download WordPress. Try again in a few minutes.'); }
    return $data;
}

if (!hash_equals(WHP_TOKEN_HASH, hash('sha256', isset($_GET['t']) ? (string)$_GET['t'] : ''))) {
    http_response_code(403);
    echo '{"ok":false}';
    exit;
}
@unlink(__FILE__); // one use: it cannot be run again

try {
    $keep = array('index.html', 'index.htm', 'cgi-bin', '.well-known', 'error_log', '.htaccess', '.ftpquota', WHP_RESULT);
    foreach (scandir(__DIR__) as $name) {
        if ($name === '.' || $name === '..' || in_array($name, $keep, true)) { continue; }
        if (strpos($name, 'whp-install-') === 0) { continue; }
        whp_finish(false, array('error' => 'The website folder already holds files, so WordPress was not installed over them.', 'code' => 'not_empty'));
    }
    if (file_exists(__DIR__ . '/wp-config.php')) {
        whp_finish(false, array('error' => 'WordPress is already installed here.', 'code' => 'exists'));
    }

    $useZip = class_exists('ZipArchive');
    $archive = whp_get($useZip ? WHP_ZIP : WHP_TAR);
    $sum = strtolower(trim(strtok(whp_get(($useZip ? WHP_ZIP : WHP_TAR) . '.sha1'), " \t\r\n")));
    if ($sum === '' || sha1($archive) !== $sum) {
        whp_finish(false, array('error' => 'A download did not match its published checksum, so nothing was installed.', 'code' => 'checksum_mismatch'));
    }
    $tmp = tempnam(sys_get_temp_dir(), 'whp');
    file_put_contents($tmp, $archive);
    unset($archive);

    foreach (array('index.html', 'index.htm') as $holder) { if (is_file(__DIR__ . '/' . $holder)) { @unlink(__DIR__ . '/' . $holder); } }

    if ($useZip) {
        $zip = new ZipArchive();
        if ($zip->open($tmp) !== true) { throw new Exception('The WordPress download could not be read.'); }
        for ($i = 0; $i < $zip->numFiles; $i++) {
            $entry = $zip->getNameIndex($i);
            if (strpos($entry, 'wordpress/') !== 0 || strpos($entry, '..') !== false) { continue; }
            $rel = substr($entry, 10);
            if ($rel === '' || $rel === false) { continue; }
            $dest = __DIR__ . '/' . $rel;
            if (substr($entry, -1) === '/') {
                if (!is_dir($dest)) { mkdir($dest, 0755, true); $created[] = $dest; }
                continue;
            }
            if (!is_dir(dirname($dest))) { mkdir(dirname($dest), 0755, true); $created[] = dirname($dest); }
            file_put_contents($dest, $zip->getFromIndex($i));
            $created[] = $dest;
        }
        $zip->close();
    } else {
        $copy = $tmp . '.tar.gz';
        rename($tmp, $copy);
        $tmp = $copy;
        $phar = new PharData($tmp);
        $phar->extractTo(__DIR__ . '/.whp-unpack', null, true);
        $created[] = __DIR__ . '/.whp-unpack';
        $source = __DIR__ . '/.whp-unpack/wordpress';
        foreach (scandir($source) as $name) {
            if ($name === '.' || $name === '..') { continue; }
            rename($source . '/' . $name, __DIR__ . '/' . $name);
            $created[] = __DIR__ . '/' . $name;
        }
        whp_rm(__DIR__ . '/.whp-unpack');
    }
    @unlink($tmp);
    if (!is_file(__DIR__ . '/wp-includes/version.php')) { throw new Exception('The WordPress download did not contain WordPress.'); }

    $salts = '';
    foreach (array('AUTH_KEY', 'SECURE_AUTH_KEY', 'LOGGED_IN_KEY', 'NONCE_KEY', 'AUTH_SALT', 'SECURE_AUTH_SALT', 'LOGGED_IN_SALT', 'NONCE_SALT') as $key) {
        $salts .= "define( '" . $key . "', '" . bin2hex(random_bytes(32)) . "' );\n";
    }
    $config = "<?php\n// written by WHPanel when WordPress was installed. Keep this file private.\n"
        . "define( 'DB_NAME', " . var_export($whp['db'], true) . " );\n"
        . "define( 'DB_USER', " . var_export($whp['user'], true) . " );\n"
        . "define( 'DB_PASSWORD', " . var_export($whp['password'], true) . " );\n"
        . "define( 'DB_HOST', 'localhost' );\ndefine( 'DB_CHARSET', 'utf8mb4' );\ndefine( 'DB_COLLATE', '' );\n\n" . $salts
        . "\n\$table_prefix = 'wp_';\ndefine( 'WP_DEBUG', false );\ndefine( 'FS_METHOD', 'direct' );\n"
        . "if ( ! defined( 'ABSPATH' ) ) { define( 'ABSPATH', __DIR__ . '/' ); }\nrequire_once ABSPATH . 'wp-settings.php';\n";
    file_put_contents(__DIR__ . '/wp-config.php', $config);
    $created[] = __DIR__ . '/wp-config.php';
    @chmod(__DIR__ . '/wp-config.php', 0640);

    $_SERVER['HTTP_HOST'] = $whp['domain'];
    $_SERVER['SERVER_NAME'] = $whp['domain'];
    $_SERVER['REQUEST_URI'] = '/wp-admin/install.php';
    define('WP_INSTALLING', true);
    require_once __DIR__ . '/wp-load.php';
    require_once ABSPATH . 'wp-admin/includes/upgrade.php';
    if (is_blog_installed()) { throw new Exception('WordPress is already installed in this database.'); }
    wp_install($whp['title'], $whp['admin_user'], $whp['admin_email'], true, '', $whp['admin_password']);
    update_option('siteurl', $whp['url']);
    update_option('home', $whp['url']);
    global $wp_version;
    whp_finish(true, array('version' => isset($wp_version) ? $wp_version : '', 'admin_url' => $whp['url'] . '/wp-admin/'));
} catch (Throwable $e) {
    whp_undo();
    whp_finish(false, array('error' => $e->getMessage(), 'code' => 'failed'));
}
"""


def build(*, domain, scheme, db, db_user, db_password, title, admin_user, admin_email, admin_password):
    """The installer file for one installation: (file name, result file name, token, PHP source). The token is secret; only its hash is in the file."""
    ident = secrets.token_hex(8)
    token = secrets.token_urlsafe(32)
    config = {"domain": domain, "url": f"{scheme}://{domain}", "db": db, "user": db_user, "password": db_password, "title": title,
              "admin_user": admin_user, "admin_email": admin_email, "admin_password": admin_password}
    result = f"whp-install-{ident}.json"
    source = (PHP.replace("__TOKEN_HASH__", hashlib.sha256(token.encode()).hexdigest()).replace("__RESULT__", result).replace("__ZIP__", WP_ZIP)
              .replace("__TAR__", WP_TAR).replace("__CONFIG__", base64.b64encode(json.dumps(config).encode()).decode()))
    return f"whp-install-{ident}.php", result, token, source
