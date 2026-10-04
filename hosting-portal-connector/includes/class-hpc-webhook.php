<?php

if (!defined('ABSPATH')) {
    exit;
}

/**
 * Receives the portal's "the catalog changed" ping.
 *
 * Endpoint: POST /wp-json/hpc/v1/webhook
 *
 * The ping is trusted only after its signature is verified, and even then
 * its body is never used as data: the plugin just clears its cache so the
 * next page view refetches the catalog from the read API.
 */
class HPC_Webhook {

    /**
     * Maximum age of a signed ping, in seconds (replay protection).
     */
    const TOLERANCE = 600;

    public function init() {

        add_action(
            'rest_api_init',
            [$this, 'register_route']
        );
    }

    public function register_route() {

        register_rest_route(
            'hpc/v1',
            '/webhook',
            [
                'methods'             => 'POST',
                'callback'            => [$this, 'handle'],
                // Public on purpose: the HMAC signature is the authentication.
                'permission_callback' => '__return_true',
            ]
        );
    }

    /**
     * Verify "t=<unix>,v1=<hex hmac>" against the raw body.
     */
    public static function signature_is_valid($header, $raw_body, $secret, $now = null) {

        if ($secret === '' || $header === '') {
            return false;
        }

        $now = $now === null ? time() : (int) $now;

        $timestamp = null;
        $signature = null;

        foreach (explode(',', $header) as $part) {

            $pair = explode('=', trim($part), 2);

            if (count($pair) !== 2) {
                continue;
            }

            if ($pair[0] === 't') {
                $timestamp = $pair[1];
            } elseif ($pair[0] === 'v1') {
                $signature = $pair[1];
            }
        }

        if ($timestamp === null || $signature === null || !ctype_digit($timestamp)) {
            return false;
        }

        if (abs($now - (int) $timestamp) > self::TOLERANCE) {
            return false;
        }

        $expected = hash_hmac('sha256', $timestamp . '.' . $raw_body, $secret);

        return hash_equals($expected, $signature);
    }

    public function handle($request) {

        $secret = (string) get_option('hpc_webhook_secret', '');

        if ($secret === '') {
            return new WP_REST_Response(['ok' => false, 'error' => 'not_configured'], 503);
        }

        $header = (string) $request->get_header('x-storefront-signature');

        if (!self::signature_is_valid($header, $request->get_body(), $secret)) {

            update_option('hpc_last_webhook', [
                'time' => current_time('mysql'),
                'ok'   => false,
            ], false);

            return new WP_REST_Response(['ok' => false, 'error' => 'invalid_signature'], 401);
        }

        $cache = new HPC_Cache();
        $cache->clear_all();

        update_option('hpc_last_webhook', [
            'time' => current_time('mysql'),
            'ok'   => true,
        ], false);

        return new WP_REST_Response(['ok' => true], 200);
    }
}
