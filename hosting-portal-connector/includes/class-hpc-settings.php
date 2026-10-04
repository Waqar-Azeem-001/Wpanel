<?php

if (!defined('ABSPATH')) {
    exit;
}

class HPC_Settings {

    /**
     * Register plugin hooks.
     */
    public function init() {

        add_action(
            'admin_menu',
            [$this, 'register_admin_menu']
        );

        add_action(
            'admin_init',
            [$this, 'register_settings']
        );

        add_action(
            'admin_post_hpc_test_products',
            [$this, 'test_products']
        );

        add_action(
            'admin_post_hpc_clear_cache',
            [$this, 'clear_cache']
        );

        add_action(
            'admin_post_hpc_refresh_products',
            [$this, 'refresh_products']
        );

        add_action(
            'update_option_hpc_connection_mode',
            [$this, 'clear_plugin_cache'],
            10,
            2
        );

        add_action(
            'update_option_hpc_portal_url',
            [$this, 'clear_plugin_cache'],
            10,
            2
        );

        add_action(
            'update_option_hpc_api_key',
            [$this, 'clear_plugin_cache'],
            10,
            2
        );

        add_action(
            'update_option_hpc_cache_duration',
            [$this, 'clear_plugin_cache'],
            10,
            2
        );
    }


    /**
     * Register WordPress admin menu.
     */
    public function register_admin_menu() {

        add_menu_page(
            __('Hosting Portal', 'hosting-portal-connector'),
            __('Hosting Portal', 'hosting-portal-connector'),
            'manage_options',
            'hosting-portal-connector',
            [$this, 'render_settings_page'],
            'dashicons-cloud',
            56
        );
    }


    /**
     * Register plugin settings.
     */
    public function register_settings() {

        register_setting(
            'hpc_settings_group',
            'hpc_portal_url',
            [
                'type'              => 'string',
                'sanitize_callback' => 'esc_url_raw',
                'default'           => '',
            ]
        );

        register_setting(
            'hpc_settings_group',
            'hpc_api_key',
            [
                'type'              => 'string',
                'sanitize_callback' => 'sanitize_text_field',
                'default'           => '',
            ]
        );

        register_setting(
            'hpc_settings_group',
            'hpc_webhook_secret',
            [
                'type'              => 'string',
                'sanitize_callback' => 'sanitize_text_field',
                'default'           => '',
            ]
        );

        register_setting(
            'hpc_settings_group',
            'hpc_connection_mode',
            [
                'type'              => 'string',
                'sanitize_callback' => [$this, 'sanitize_connection_mode'],
                'default'           => 'mock',
            ]
        );

        register_setting(
            'hpc_settings_group',
            'hpc_cache_duration',
            [
                'type'              => 'integer',
                'sanitize_callback' => 'absint',
                'default'           => 900,
            ]
        );
    }


    /**
     * Sanitize connection mode.
     */
    public function sanitize_connection_mode($value) {

        $allowed = [
            'mock',
            'live',
        ];

        if (!in_array($value, $allowed, true)) {
            return 'mock';
        }

        return $value;
    }


    /**
     * Render admin settings page.
     */
    public function render_settings_page() {

        if (!current_user_can('manage_options')) {
            return;
        }

        include HPC_PLUGIN_DIR . 'admin/views/settings-page.php';
    }


    /**
     * Save connection status information.
     */
    private function update_connection_status(
        $success,
        $message = '',
        $product_count = 0
    ) {

        update_option(
            'hpc_last_connection_status',
            $success ? 'success' : 'failed',
            false
        );

        update_option(
            'hpc_last_connection_message',
            sanitize_text_field($message),
            false
        );

        update_option(
            'hpc_last_connection_time',
            current_time('mysql'),
            false
        );

        update_option(
            'hpc_last_product_count',
            absint($product_count),
            false
        );

        if ($success) {

            update_option(
                'hpc_last_successful_connection',
                current_time('mysql'),
                false
            );
        }
    }


    /**
     * Test current product/API connection.
     */
    public function test_products() {

        if (!current_user_can('manage_options')) {
            wp_die(
                'You are not allowed to perform this action.'
            );
        }

        check_admin_referer(
            'hpc_test_products'
        );

        $client = new HPC_API_Client();

        /*
         * Force fresh request.
         */
        $result = $client->get_products(true);

        $product_count = 0;

        if (
            !empty($result['success']) &&
            isset($result['data']['products']) &&
            is_array($result['data']['products'])
        ) {

            $product_count = count(
                $result['data']['products']
            );
        }

        if (!empty($result['success'])) {

            $source = isset($result['source'])
                ? $result['source']
                : 'unknown';

            $message = sprintf(
                'Connection successful. Source: %s.',
                ucfirst($source)
            );

            $this->update_connection_status(
                true,
                $message,
                $product_count
            );

        } else {

            $message = isset($result['message'])
                ? $result['message']
                : 'Unknown connection error.';

            $this->update_connection_status(
                false,
                $message,
                0
            );
        }

        set_transient(
            'hpc_api_test_result',
            $result,
            60
        );

        wp_safe_redirect(
            admin_url(
                'admin.php?page=hosting-portal-connector&hpc_test=1'
            )
        );

        exit;
    }


    /**
     * Refresh products from the current source.
     */
    public function refresh_products() {

        if (!current_user_can('manage_options')) {
            wp_die(
                'You are not allowed to perform this action.'
            );
        }

        check_admin_referer(
            'hpc_refresh_products'
        );

        $cache = new HPC_Cache();

        $cache->clear_products();

        $client = new HPC_API_Client();

        $result = $client->get_products(true);

        $product_count = 0;

        if (
            !empty($result['success']) &&
            isset($result['data']['products']) &&
            is_array($result['data']['products'])
        ) {

            $product_count = count(
                $result['data']['products']
            );
        }

        if (!empty($result['success'])) {

            $this->update_connection_status(
                true,
                'Products refreshed successfully.',
                $product_count
            );

            $redirect_arg = 'success';

        } else {

            $message = isset($result['message'])
                ? $result['message']
                : 'Unable to refresh products.';

            $this->update_connection_status(
                false,
                $message,
                0
            );

            $redirect_arg = 'failed';
        }

        wp_safe_redirect(
            add_query_arg(
                'hpc_refresh',
                $redirect_arg,
                admin_url(
                    'admin.php?page=hosting-portal-connector'
                )
            )
        );

        exit;
    }


    /**
     * Clear plugin cache after settings change.
     */
    public function clear_plugin_cache(
        $old_value = null,
        $new_value = null
    ) {

        $cache = new HPC_Cache();

        $cache->clear_all();
    }


    /**
     * Manual cache clearing action.
     */
    public function clear_cache() {

        if (!current_user_can('manage_options')) {
            wp_die(
                'You are not allowed to perform this action.'
            );
        }

        check_admin_referer(
            'hpc_clear_cache'
        );

        $cache = new HPC_Cache();

        $cache->clear_all();

        wp_safe_redirect(
            admin_url(
                'admin.php?page=hosting-portal-connector&hpc_cache_cleared=1'
            )
        );

        exit;
    }
}