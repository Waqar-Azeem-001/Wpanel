"""
The one-file WordPress tool for servers where WHPanel cannot run commands (cPanel/WHM today): list what is installed and what has an update, switch a plugin or theme on or off, and
install an update, using WordPress's own functions.

Like the installer and the scanner it is ONE small PHP file written into the website's folder and called once with a secret token; it removes itself first, does exactly one action
named in its (hash-protected) configuration, and writes a small result file the panel reads and deletes. It never receives anything from the visitor except the token.
"""
import base64
import hashlib
import json
import secrets

from . import php_helpers

ACTIONS = ("inventory", "activate_plugin", "deactivate_plugin", "update_plugin", "activate_theme", "update_theme", "update_core", "flush_cache", "set_password", "install_plugin", "install_theme")

PHP = r"""<?php
/* WHPanel one-time WordPress tool. It removes itself when it starts. */
@ignore_user_abort(true);
@set_time_limit(240);
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

if (!hash_equals(WHP_TOKEN_HASH, hash('sha256', isset($_GET['t']) ? (string)$_GET['t'] : ''))) {
    http_response_code(403);
    echo '{"ok":false}';
    exit;
}
@unlink(__FILE__); // one use

try {
    define('WP_USE_THEMES', false);
    if (!is_file(__DIR__ . '/wp-load.php')) { whp_finish(false, array('error' => 'WordPress was not found in this folder.')); }
    require_once __DIR__ . '/wp-load.php';
    require_once ABSPATH . 'wp-admin/includes/plugin.php';
    require_once ABSPATH . 'wp-admin/includes/file.php';
    require_once ABSPATH . 'wp-admin/includes/misc.php';
    require_once ABSPATH . 'wp-admin/includes/theme.php';
    require_once ABSPATH . 'wp-admin/includes/update.php';
    require_once ABSPATH . 'wp-admin/includes/class-wp-upgrader.php';
    require_once ABSPATH . 'wp-includes/update.php';
    $action = $whp['action'];
    $target = isset($whp['target']) ? (string)$whp['target'] : '';

    $all = get_plugins();
    $themes = wp_get_themes();
    $knownPlugin = function ($file) use ($all) { return isset($all[$file]); };
    $knownTheme = function ($slug) use ($themes) { return isset($themes[$slug]); };

    $upgrade = function ($upgrader, $what) {
        $result = $upgrader->upgrade($what);
        if (is_wp_error($result)) { return $result->get_error_message(); }
        if ($result === false || $result === null) { return 'The update could not be installed.'; }
        return '';
    };

    if ($action === 'activate_plugin' || $action === 'deactivate_plugin' || $action === 'update_plugin') {
        if (!$knownPlugin($target)) { whp_finish(false, array('error' => 'That plugin is not installed.')); }
        if ($action === 'activate_plugin') {
            $r = activate_plugin($target);
            if (is_wp_error($r)) { whp_finish(false, array('error' => $r->get_error_message())); }
        } elseif ($action === 'deactivate_plugin') {
            deactivate_plugins($target);
        } else {
            $err = $upgrade(new Plugin_Upgrader(new Automatic_Upgrader_Skin()), $target);
            if ($err !== '') { whp_finish(false, array('error' => $err)); }
        }
    } elseif ($action === 'activate_theme' || $action === 'update_theme') {
        if (!$knownTheme($target)) { whp_finish(false, array('error' => 'That theme is not installed.')); }
        if ($action === 'activate_theme') {
            switch_theme($target);
        } else {
            $err = $upgrade(new Theme_Upgrader(new Automatic_Upgrader_Skin()), $target);
            if ($err !== '') { whp_finish(false, array('error' => $err)); }
        }
    } elseif ($action === 'update_core') {
        wp_version_check(array(), true);
        $updates = get_core_updates();
        if (!is_array($updates) || !isset($updates[0]) || $updates[0]->response !== 'upgrade') { whp_finish(false, array('error' => 'WordPress is already up to date.')); }
        $r = (new Core_Upgrader(new Automatic_Upgrader_Skin()))->upgrade($updates[0]);
        if (is_wp_error($r)) { whp_finish(false, array('error' => $r->get_error_message())); }
    } elseif ($action === 'install_plugin' || $action === 'install_theme') {
        if (!preg_match('/^[a-z0-9-]{1,60}$/', $target)) { whp_finish(false, array('error' => 'That name is not valid.')); }
        require_once ABSPATH . 'wp-admin/includes/plugin-install.php';
        if ($action === 'install_plugin') {
            $api = plugins_api('plugin_information', array('slug' => $target, 'fields' => array('sections' => false)));
            if (is_wp_error($api)) { whp_finish(false, array('error' => 'WordPress.org does not list that plugin.')); }
            $installer = new Plugin_Upgrader(new Automatic_Upgrader_Skin());
            $result = $installer->install($api->download_link);
            if (is_wp_error($result)) { whp_finish(false, array('error' => $result->get_error_message())); }
            if (!$result) { whp_finish(false, array('error' => 'The plugin could not be installed (it may already be installed).')); }
            $file = $installer->plugin_info();
            if ($file) {
                $on = activate_plugin($file);
                if (is_wp_error($on)) { whp_finish(false, array('error' => 'Installed, but it could not be switched on: ' . $on->get_error_message())); }
            }
        } else {
            $api = themes_api('theme_information', array('slug' => $target, 'fields' => array('sections' => false)));
            if (is_wp_error($api)) { whp_finish(false, array('error' => 'WordPress.org does not list that theme.')); }
            $installer = new Theme_Upgrader(new Automatic_Upgrader_Skin());
            $result = $installer->install($api->download_link);
            if (is_wp_error($result)) { whp_finish(false, array('error' => $result->get_error_message())); }
            if (!$result) { whp_finish(false, array('error' => 'The theme could not be installed (it may already be installed).')); }
        }
    } elseif ($action === 'set_password') {
        $secret = isset($whp['secret']) ? (string)$whp['secret'] : '';
        $who = get_user_by('login', $target);
        if (!$who) { whp_finish(false, array('error' => 'That user does not exist.')); }
        if (strlen($secret) < 12) { whp_finish(false, array('error' => 'The new password is too short.')); }
        wp_set_password($secret, $who->ID);
    } elseif ($action === 'flush_cache') {
        // WordPress's own object cache, then the well-known page-cache plugins when they are present
        if (function_exists('wp_cache_flush')) { wp_cache_flush(); }
        if (function_exists('w3tc_flush_all')) { w3tc_flush_all(); }
        if (function_exists('rocket_clean_domain')) { rocket_clean_domain(); }
        if (function_exists('wp_cache_clear_cache')) { wp_cache_clear_cache(); }
        if (function_exists('sg_cachepress_purge_cache')) { sg_cachepress_purge_cache(); }
        do_action('litespeed_purge_all');
        do_action('wpfc_clear_all_cache');
    } elseif ($action !== 'inventory') {
        whp_finish(false, array('error' => 'Unknown action.'));
    }

    // What is there now (after the action), with what has an update.
    wp_clean_plugins_cache(true);
    wp_clean_themes_cache(true);
    wp_update_plugins();
    wp_update_themes();
    wp_version_check(array(), true);
    $all = get_plugins();
    $themes = wp_get_themes();
    $active = (array)get_option('active_plugins', array());
    $pluginUpdates = get_site_transient('update_plugins');
    $themeUpdates = get_site_transient('update_themes');
    $plugins = array();
    foreach ($all as $file => $data) {
        $plugins[] = array('file' => $file, 'name' => $data['Name'], 'version' => $data['Version'], 'active' => in_array($file, $active, true),
                           'update' => isset($pluginUpdates->response[$file]) ? (string)$pluginUpdates->response[$file]->new_version : '');
    }
    $themeRows = array();
    $current = get_stylesheet();
    foreach ($themes as $slug => $theme) {
        $themeRows[] = array('slug' => $slug, 'name' => $theme->get('Name'), 'version' => $theme->get('Version'), 'active' => $slug === $current,
                             'update' => isset($themeUpdates->response[$slug]) ? (string)$themeUpdates->response[$slug]['new_version'] : '');
    }
    $core = get_core_updates();
    global $wp_version;
    whp_finish(true, array('action' => $action, 'core' => array('version' => $wp_version,
                           'update' => (is_array($core) && isset($core[0]) && $core[0]->response === 'upgrade') ? (string)$core[0]->current : ''),
                           'plugins' => $plugins, 'themes' => $themeRows));
} catch (Exception $e) {
    whp_finish(false, array('error' => 'The action could not finish.'));
}
"""


def build(action, target="", secret=""):
    """Returns (file name, result file name, token, PHP source). ``action`` must be one of ``ACTIONS``."""
    if action not in ACTIONS:
        raise ValueError("unknown action")
    token = secrets.token_urlsafe(32)
    suffix = secrets.token_hex(8)
    file_name, result_name = f"whp-wp-{suffix}.php", f"whp-wp-{suffix}.json"
    config = base64.b64encode(json.dumps({"action": action, "target": str(target)[:200], "secret": str(secret)[:200]}).encode()).decode()
    source = php_helpers.fill(PHP).replace("__TOKEN_HASH__", hashlib.sha256(token.encode()).hexdigest()).replace("__RESULT__", result_name).replace("__CONFIG__", config)
    return file_name, result_name, token, source
