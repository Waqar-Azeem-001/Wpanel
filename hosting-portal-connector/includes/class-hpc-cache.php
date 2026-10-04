<?php

if (!defined('ABSPATH')) {
    exit;
}

class HPC_Cache {

    const PRODUCTS_KEY = 'hpc_cached_products';
    const DOMAINS_KEY  = 'hpc_cached_domains';
    const ADDONS_KEY   = 'hpc_cached_addons';

    /**
     * Get configured cache duration.
     *
     * @return int
     */
    public function get_duration() {

        $duration = absint(
            get_option('hpc_cache_duration', 900)
        );

        if ($duration < 60) {
            $duration = 60;
        }

        return $duration;
    }

    /**
     * Get cached products.
     *
     * @return array|false
     */
    public function get_products() {

        return get_transient(
            self::PRODUCTS_KEY
        );
    }

    /**
     * Save products to cache.
     *
     * @param array $data
     * @return bool
     */
    public function set_products($data) {

        if (!is_array($data)) {
            return false;
        }

        return set_transient(
            self::PRODUCTS_KEY,
            $data,
            $this->get_duration()
        );
    }

    /**
     * Delete products cache.
     *
     * @return bool
     */
    public function clear_products() {

        return delete_transient(
            self::PRODUCTS_KEY
        );
    }

    /**
     * Clear all plugin cache.
     *
     * Future cache keys can be added here.
     *
     * @return void
     */
    public function clear_all() {

        $this->clear_products();
        delete_transient(self::DOMAINS_KEY);
        delete_transient(self::ADDONS_KEY);
    }

    /**
     * Domain pricing cache.
     */
    public function get_domains() {
        return get_transient(self::DOMAINS_KEY);
    }

    public function set_domains($data) {
        return is_array($data) && set_transient(self::DOMAINS_KEY, $data, $this->get_duration());
    }

    /**
     * Addons cache.
     */
    public function get_addons() {
        return get_transient(self::ADDONS_KEY);
    }

    public function set_addons($data) {
        return is_array($data) && set_transient(self::ADDONS_KEY, $data, $this->get_duration());
    }
}