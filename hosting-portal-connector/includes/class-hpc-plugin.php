<?php

if (!defined('ABSPATH')) {
    exit;
}

class HPC_Plugin {

    /**
     * Start plugin functionality.
     */
    public function run() {

        /*
        |--------------------------------------------------------------------------
        | Admin Area
        |--------------------------------------------------------------------------
        */

        if (is_admin()) {

            $settings = new HPC_Settings();
            $settings->init();

            add_action(
                'admin_enqueue_scripts',
                [$this, 'enqueue_admin_assets']
            );
        }


        /*
        |--------------------------------------------------------------------------
        | Frontend
        |--------------------------------------------------------------------------
        */

        add_action(
            'wp_enqueue_scripts',
            [$this, 'enqueue_public_assets']
        );


        /*
        |--------------------------------------------------------------------------
        | Shortcodes
        |--------------------------------------------------------------------------
        */

        $shortcodes = new HPC_Shortcodes();
        $shortcodes->init();


        /*
        |--------------------------------------------------------------------------
        | Webhook (portal tells us the catalog changed)
        |--------------------------------------------------------------------------
        */

        $webhook = new HPC_Webhook();
        $webhook->init();


        /*
        |--------------------------------------------------------------------------
        | Domain search
        |--------------------------------------------------------------------------
        */

        $domain_search = new HPC_Domain_Search();
        $domain_search->init();
    }


    /**
     * Load frontend CSS and JavaScript.
     */
    public function enqueue_public_assets() {

        $css_file = HPC_PLUGIN_DIR . 'assets/css/frontend.css';
        $js_file  = HPC_PLUGIN_DIR . 'assets/js/frontend.js';

        $css_version = file_exists($css_file)
            ? filemtime($css_file)
            : HPC_VERSION;

        $js_version = file_exists($js_file)
            ? filemtime($js_file)
            : HPC_VERSION;


        wp_enqueue_style(
            'hpc-frontend',
            HPC_PLUGIN_URL . 'assets/css/frontend.css',
            [],
            $css_version
        );


        wp_enqueue_script(
            'hpc-frontend',
            HPC_PLUGIN_URL . 'assets/js/frontend.js',
            [],
            $js_version,
            true
        );
    }


    /**
     * Load admin CSS only on Hosting Portal settings page.
     *
     * @param string $hook
     */
    public function enqueue_admin_assets($hook) {

        if ($hook !== 'toplevel_page_hosting-portal-connector') {
            return;
        }

        $css_file = HPC_PLUGIN_DIR . 'assets/css/admin.css';

        $css_version = file_exists($css_file)
            ? filemtime($css_file)
            : HPC_VERSION;


        wp_enqueue_style(
            'hpc-admin',
            HPC_PLUGIN_URL . 'assets/css/admin.css',
            [],
            $css_version
        );
    }
}