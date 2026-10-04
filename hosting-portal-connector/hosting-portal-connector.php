<?php
/**
 * Plugin Name: Hosting Portal Connector
 * Plugin URI: https://webhostera.pk/
 * Description: Connects WordPress with an external hosting management portal to display hosting plans, pricing, domains and related storefront data.
 * Version: 2.0.0
 * Author: WHE
 * Text Domain: hosting-portal-connector
 */

if (!defined('ABSPATH')) {
    exit;
}

define('HPC_VERSION', '2.0.0');
define('HPC_PLUGIN_FILE', __FILE__);
define('HPC_PLUGIN_DIR', plugin_dir_path(__FILE__));
define('HPC_PLUGIN_URL', plugin_dir_url(__FILE__));

require_once HPC_PLUGIN_DIR . 'includes/class-hpc-plugin.php';
require_once HPC_PLUGIN_DIR . 'includes/class-hpc-settings.php';
require_once HPC_PLUGIN_DIR . 'includes/class-hpc-api-client.php';
require_once HPC_PLUGIN_DIR . 'includes/class-hpc-cache.php';
require_once HPC_PLUGIN_DIR . 'includes/class-hpc-shortcodes.php';
require_once HPC_PLUGIN_DIR . 'includes/class-hpc-webhook.php';
require_once HPC_PLUGIN_DIR . 'includes/class-hpc-domain-search.php';

function hpc_run_plugin() {

    $plugin = new HPC_Plugin();

    $plugin->run();
}

hpc_run_plugin();