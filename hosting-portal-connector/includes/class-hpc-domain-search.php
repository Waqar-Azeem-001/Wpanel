<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * Domain availability search.
 *
 * Shortcode: [domain_search]
 * Endpoint:  GET /wp-json/hpc/v1/domain-search?name=example.com
 *
 * The browser only ever talks to this site. This site's PHP asks the portal
 * (with the credential, server side) whether the name is free and at what
 * price, then the visitor is sent on to the portal to actually register it.
 */
class HPC_Domain_Search {

    /**
     * Searches allowed per visitor per minute.
     */
    const RATE_LIMIT = 20;

    public function init() {

        add_shortcode(
            'domain_search',
            [$this, 'render']
        );

        add_action(
            'rest_api_init',
            [$this, 'register_route']
        );
    }

    public function register_route() {

        register_rest_route(
            'hpc/v1',
            '/domain-search',
            [
                'methods'             => 'GET',
                'callback'            => [$this, 'handle'],
                // Public on purpose: it only answers "is this name free", and is rate limited.
                'permission_callback' => '__return_true',
                'args'                => [
                    'name' => [
                        'required'          => true,
                        'sanitize_callback' => 'sanitize_text_field',
                    ],
                ],
            ]
        );
    }

    public function render($atts = []) {

        ob_start();

        include HPC_PLUGIN_DIR . 'public/views/domain-search.php';

        return ob_get_clean();
    }

    /**
     * Keep one visitor from hammering the registrar through this site.
     */
    private function too_many_requests() {

        $ip = isset($_SERVER['REMOTE_ADDR']) ? sanitize_text_field(wp_unslash($_SERVER['REMOTE_ADDR'])) : 'unknown';

        $key = 'hpc_ds_' . md5($ip);

        $count = (int) get_transient($key);

        if ($count >= self::RATE_LIMIT) {
            return true;
        }

        set_transient($key, $count + 1, MINUTE_IN_SECONDS);

        return false;
    }

    public function handle($request) {

        if ($this->too_many_requests()) {
            return new WP_REST_Response(['ok' => false, 'message' => 'Too many searches. Please wait a minute.'], 429);
        }

        $name = strtolower(trim((string) $request->get_param('name')));

        // Only a plausible domain name goes any further.
        if (!preg_match('/^(?=.{3,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$/', $name)) {
            return new WP_REST_Response(['ok' => false, 'message' => 'Enter a domain name such as example.com.'], 400);
        }

        $client = new HPC_API_Client();

        $result = $client->search_domain($name);

        if (empty($result['success'])) {
            return new WP_REST_Response(['ok' => false, 'message' => $result['message']], 502);
        }

        $d = $result['data'];

        return new WP_REST_Response([
            'ok'        => true,
            'domain'    => isset($d['domain']) ? $d['domain'] : $name,
            'available' => !empty($d['available']),
            'price'     => isset($d['register_price']) ? $d['register_price'] : '',
            'currency'  => isset($d['currency']) ? $d['currency'] : '',
            'order_url' => isset($d['order_url']) ? esc_url_raw($d['order_url']) : '',
        ], 200);
    }
}
