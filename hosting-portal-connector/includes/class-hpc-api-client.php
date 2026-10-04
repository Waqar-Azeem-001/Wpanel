<?php

if (!defined('ABSPATH')) {
    exit;
}

class HPC_API_Client {

    private $portal_url;
    private $api_key;
    private $connection_mode;

    private static $runtime_products = null;
    private static $runtime_categories = null;
    private static $runtime_domain_prices = null;
    private static $runtime_addons = null;

    public function __construct() {

        $this->portal_url = untrailingslashit(
            get_option('hpc_portal_url', '')
        );

        $this->api_key = get_option('hpc_api_key', '');

        $this->connection_mode = get_option(
            'hpc_connection_mode',
            'mock'
        );
    }

    /**
     * Ask the portal whether a domain name is free to register.
     *
     * Never cached: availability changes. In Mock mode a name starting with
     * "taken" is reported unavailable, so both outcomes can be styled.
     *
     * @return array
     */
    public function search_domain($name) {

        if ($this->connection_mode === 'mock') {

            return [
                'success' => true,
                'message' => '',
                'data'    => [
                    'domain'         => $name,
                    'available'      => strpos($name, 'taken') !== 0,
                    'register_price' => '12.99',
                    'currency'       => 'USD',
                    'order_url'      => 'https://portal.example.com/domains/?domain=' . rawurlencode($name) . '&years=1',
                ],
            ];
        }

        return $this->request(
            '/api/v1/storefront/domains/search/?name=' . rawurlencode($name)
        );
    }

    /**
     * Find one product by slug (or numeric id) in the loaded catalog.
     *
     * @return array|null
     */
    public function get_product($slug) {

        $result = $this->get_products();

        if (empty($result['data']['products']) || !is_array($result['data']['products'])) {
            return null;
        }

        foreach ($result['data']['products'] as $product) {

            if (
                (isset($product['slug']) && $product['slug'] === $slug) ||
                (isset($product['id']) && (string) $product['id'] === (string) $slug)
            ) {
                return $product;
            }
        }

        return null;
    }

    /**
     * Get hosting products.
     *
     * @return array
     */
public function get_products($force_refresh = false) {

    /*
     * Same WordPress page request mein products pehle
     * load ho chuke hain to database/transient ko bhi
     * dobara hit na karein.
     */
    if (
        !$force_refresh &&
        self::$runtime_products !== null
    ) {
        return self::$runtime_products;
    }

    $cache = new HPC_Cache();

    /*
     * WordPress persistent/transient cache.
     */
    if (!$force_refresh) {

        $cached = $cache->get_products();

        if ($cached !== false) {

            $result = [
                'success' => true,
                'message' => '',
                'source'  => 'cache',
                'data'    => $cached,
            ];

            self::$runtime_products = $result;

            return $result;
        }
    }

    /*
     * No cache: fetch from selected source.
     */
    if ($this->connection_mode === 'mock') {

        $result = $this->get_mock_products();

    } else {

        $result = $this->request(
            '/api/v1/storefront/products/',
            'products'
        );
    }

    /*
     * Cache successful response.
     */
    if (
        !empty($result['success']) &&
        isset($result['data']) &&
        is_array($result['data'])
    ) {

        $cache->set_products(
            $result['data']
        );

        $result['source'] = (
            $this->connection_mode === 'mock'
        )
            ? 'mock'
            : 'live';

        update_option(
            'hpc_last_sync_time',
            current_time('mysql'),
            false
        );

        $product_count = 0;

        if (
            isset($result['data']['products']) &&
            is_array($result['data']['products'])
        ) {
            $product_count = count(
                $result['data']['products']
            );
        }

        update_option(
            'hpc_last_product_count',
            $product_count,
            false
        );
    }

    /*
     * Keep result in memory for the rest
     * of this WordPress request.
     */
    self::$runtime_products = $result;

    return $result;
}



    /**
     * Make a GET request to Django Portal API.
     *
     * @param string $endpoint
     * @return array
     */
    private function request($endpoint, $list_key = null) {

        if (empty($this->portal_url)) {
            return [
                'success' => false,
                'message' => 'Portal URL is not configured.',
                'data'    => [],
            ];
        }

        if (empty($this->api_key)) {
            return [
                'success' => false,
                'message' => 'API Key is not configured.',
                'data'    => [],
            ];
        }

        if (strpos($this->api_key, ':') === false) {
            return [
                'success' => false,
                'message' => 'The credential must be written as public_id:secret (both parts from the portal).',
                'data'    => [],
            ];
        }

        $url = $this->portal_url . $endpoint;

        $started = microtime(true);

        /*
         * The credential is sent only from this server-side PHP request,
         * never to the browser. The portal expects its own scheme keyword.
         */
        $response = wp_remote_get(
            $url,
            [
                'timeout' => 15,

                'headers' => [
                    'Accept'        => 'application/json',
                    'Authorization' => 'Storefront ' . $this->api_key,
                ],
            ]
        );

        if (is_wp_error($response)) {

            return $this->finish_request(
                $endpoint,
                $started,
                false,
                $response->get_error_message()
            );
        }

        $status_code = wp_remote_retrieve_response_code($response);

        $body = wp_remote_retrieve_body($response);

        $data = json_decode($body, true);

        if ($status_code === 401 || $status_code === 403) {

            return $this->finish_request(
                $endpoint,
                $started,
                false,
                'The portal rejected the credential (HTTP ' . $status_code . '). Check the public ID and secret.'
            );
        }

        if ($status_code < 200 || $status_code >= 300) {

            return $this->finish_request(
                $endpoint,
                $started,
                false,
                'Portal API returned HTTP ' . $status_code . '.'
            );
        }

        if (json_last_error() !== JSON_ERROR_NONE) {

            return $this->finish_request(
                $endpoint,
                $started,
                false,
                'Portal API returned invalid JSON.'
            );
        }

        /*
         * The portal returns a plain JSON list. The rest of the plugin
         * reads it under a named key, so wrap it once here.
         */
        if (
            $list_key !== null &&
            is_array($data) &&
            array_values($data) === $data
        ) {
            $data = [$list_key => $data];
        }

        return $this->finish_request(
            $endpoint,
            $started,
            true,
            '',
            $data
        );
    }

    /**
     * Record request diagnostics (latency, last error) and build the result.
     */
    private function finish_request($endpoint, $started, $ok, $message, $data = []) {

        update_option(
            'hpc_last_request',
            [
                'endpoint' => $endpoint,
                'ms'       => (int) round((microtime(true) - $started) * 1000),
                'ok'       => (bool) $ok,
                'message'  => $message,
                'time'     => current_time('mysql'),
            ],
            false
        );

        if (!$ok) {
            update_option('hpc_last_error', $message, false);
        }

        return [
            'success' => (bool) $ok,
            'message' => $message,
            'data'    => $ok ? $data : [],
        ];
    }

    /**
     * Map the portal's domain price rows to the shape the views read.
     */
    private function map_domain_rows($rows) {

        $mapped = [];

        if (!is_array($rows)) {
            return $mapped;
        }

        foreach ($rows as $row) {

            if (empty($row['tld'])) {
                continue;
            }

            $currency = isset($row['currency']) ? $row['currency'] : '';

            $mapped[] = [
                'tld'      => $row['tld'],
                'register' => [
                    'amount'   => isset($row['register_price']) ? $row['register_price'] : '',
                    'currency' => $currency,
                ],
                'renew'    => [
                    'amount'   => isset($row['renew_price']) ? $row['renew_price'] : '',
                    'currency' => $currency,
                ],
                'transfer' => [
                    'amount'   => isset($row['transfer_price']) ? $row['transfer_price'] : '',
                    'currency' => $currency,
                ],
            ];
        }

        return $mapped;
    }

    /**
     * Temporary mock data.
     *
     * This should match the planned Django API contract.
     *
     * @return array
     */
   private function get_mock_products() {

    return [
        'success' => true,

        'message' => '',

        'data' => [

            'products' => [

                /*
                |--------------------------------------------------------------------------
                | Shared Hosting
                |--------------------------------------------------------------------------
                */

                [
                    'id'          => 1,
                    'slug'        => 'starter-hosting',
                    'name'        => 'Starter Hosting',
                    'description' => 'Ideal for personal websites and small business websites.',

                    'category' => [
                        'slug' => 'shared-hosting',
                        'name' => 'Shared Hosting',
                    ],

                    'pricing' => [

                        'monthly' => [
                            'amount'   => '4.99',
                            'currency' => 'USD',
                        ],

                        'annual' => [
                            'amount'   => '49.99',
                            'currency' => 'USD',
                        ],
                    ],

                    'features' => [
                        '10 GB SSD Storage',
                        '5 Email Accounts',
                        'Free SSL Certificate',
                        '1 Website',
                    ],

                    'featured' => false,

                    'order_url' => 'https://portal.example.com/store/starter-hosting/',
                ],


                [
                    'id'          => 2,
                    'slug'        => 'business-hosting',
                    'name'        => 'Business Hosting',
                    'description' => 'Designed for growing businesses and higher traffic websites.',

                    'category' => [
                        'slug' => 'shared-hosting',
                        'name' => 'Shared Hosting',
                    ],

                    'pricing' => [

                        'monthly' => [
                            'amount'   => '9.99',
                            'currency' => 'USD',
                        ],

                        'annual' => [
                            'amount'   => '99.99',
                            'currency' => 'USD',
                        ],
                    ],

                    'features' => [
                        '50 GB SSD Storage',
                        'Unlimited Email Accounts',
                        'Free SSL Certificate',
                        '10 Websites',
                        'Daily Backups',
                    ],

                    'featured' => true,

                    'order_url' => 'https://portal.example.com/store/business-hosting/',
                ],


                /*
                |--------------------------------------------------------------------------
                | VPS Hosting
                |--------------------------------------------------------------------------
                */

                [
                    'id'          => 3,
                    'slug'        => 'vps-starter',
                    'name'        => 'VPS Starter',
                    'description' => 'Entry-level VPS hosting for applications and growing websites.',

                    'category' => [
                        'slug' => 'vps-hosting',
                        'name' => 'VPS Hosting',
                    ],

                    'pricing' => [

                        'monthly' => [
                            'amount'   => '24.99',
                            'currency' => 'USD',
                        ],

                        'annual' => [
                            'amount'   => '249.99',
                            'currency' => 'USD',
                        ],
                    ],

                    'features' => [
                        '2 vCPU',
                        '4 GB RAM',
                        '80 GB SSD Storage',
                        '1 Dedicated IP',
                        'Root Access',
                    ],

                    'featured' => false,

                    'order_url' => 'https://portal.example.com/store/vps-starter/',
                ],


                /*
                |--------------------------------------------------------------------------
                | Reseller Hosting
                |--------------------------------------------------------------------------
                */

                [
                    'id'          => 4,
                    'slug'        => 'reseller-business',
                    'name'        => 'Reseller Business',
                    'description' => 'Hosting package for agencies and hosting resellers.',

                    'category' => [
                        'slug' => 'reseller-hosting',
                        'name' => 'Reseller Hosting',
                    ],

                    'pricing' => [

                        'monthly' => [
                            'amount'   => '34.99',
                            'currency' => 'USD',
                        ],

                        'annual' => [
                            'amount'   => '349.99',
                            'currency' => 'USD',
                        ],
                    ],

                    'features' => [
                        '100 GB SSD Storage',
                        'WHM Access',
                        'Multiple cPanel Accounts',
                        'Free SSL',
                        'Private Nameservers',
                    ],

                    'featured' => false,

                    'order_url' => 'https://portal.example.com/store/reseller-business/',
                ],


                /*
                |--------------------------------------------------------------------------
                | WordPress Hosting
                |--------------------------------------------------------------------------
                */

                [
                    'id'          => 5,
                    'slug'        => 'wordpress-pro',
                    'name'        => 'WordPress Pro',
                    'description' => 'Optimised hosting for WordPress websites.',

                    'category' => [
                        'slug' => 'wordpress-hosting',
                        'name' => 'WordPress Hosting',
                    ],

                    'pricing' => [

                        'monthly' => [
                            'amount'   => '14.99',
                            'currency' => 'USD',
                        ],

                        'annual' => [
                            'amount'   => '149.99',
                            'currency' => 'USD',
                        ],
                    ],

                    'features' => [
                        '30 GB SSD Storage',
                        'Managed WordPress',
                        'Daily Backups',
                        'Free SSL Certificate',
                        'WordPress Security',
                    ],

                    'featured' => false,

                    'order_url' => 'https://portal.example.com/store/wordpress-pro/',
                ],

            ],
        ],
    ];
}


/**
 * Get categories from available products.
 *
 * Categories are currently derived from the product response.
 * This avoids creating a second category source inside WordPress.
 *
 * @return array
 */

public function get_categories() {

    if (self::$runtime_categories !== null) {

        return [
            'success' => true,
            'message' => '',
            'data'    => [
                'categories' => self::$runtime_categories,
            ],
        ];
    }

    $result = $this->get_products();

    if (
        empty($result['success']) ||
        empty($result['data']['products']) ||
        !is_array($result['data']['products'])
    ) {

        return [
            'success' => false,
            'message' => 'Unable to load product categories.',
            'data'    => [
                'categories' => [],
            ],
        ];
    }

    $categories = [];

    foreach ($result['data']['products'] as $product) {

        if (
            empty($product['category']) ||
            empty($product['category']['slug']) ||
            empty($product['category']['name'])
        ) {
            continue;
        }

        $slug = sanitize_title(
            $product['category']['slug']
        );

        if (isset($categories[$slug])) {

            $categories[$slug]['product_count']++;

            continue;
        }

        $categories[$slug] = [
            'slug' => $slug,

            'name' => sanitize_text_field(
                $product['category']['name']
            ),

            'product_count' => 1,
        ];
    }

    self::$runtime_categories = array_values(
        $categories
    );

    return [
        'success' => true,
        'message' => '',
        'data'    => [
            'categories' => self::$runtime_categories,
        ],
    ];
}




public function get_domain_prices($force_refresh = false) {

    if (
        !$force_refresh &&
        self::$runtime_domain_prices !== null
    ) {
        return self::$runtime_domain_prices;
    }

    /*
     * Mock mode
     */
    if ($this->connection_mode === 'mock') {

        $result = $this->get_mock_domain_prices();

        self::$runtime_domain_prices = $result;

        return $result;
    }

    /*
     * Live Django API (persistent cache first)
     */
    $cache = new HPC_Cache();

    if (!$force_refresh) {

        $cached = $cache->get_domains();

        if ($cached !== false) {

            $result = [
                'success' => true,
                'message' => '',
                'source'  => 'cache',
                'data'    => $cached,
            ];

            self::$runtime_domain_prices = $result;

            return $result;
        }
    }

    $result = $this->request(
        '/api/v1/storefront/domains/pricing/',
        'domains'
    );

    if (!empty($result['success'])) {

        $result['data'] = [
            'domains' => $this->map_domain_rows(
                isset($result['data']['domains']) ? $result['data']['domains'] : []
            ),
        ];

        $result['source'] = 'live';

        $cache->set_domains($result['data']);
    }

    self::$runtime_domain_prices = $result;

    return $result;
}




private function get_mock_domain_prices() {

    return [
        'success' => true,
        'message' => '',
        'source'  => 'mock',

        'data' => [

            'domains' => [

                [
                    'tld' => '.com',

                    'register' => [
                        'amount'   => '12.99',
                        'currency' => 'USD',
                    ],

                    'renew' => [
                        'amount'   => '14.99',
                        'currency' => 'USD',
                    ],

                    'transfer' => [
                        'amount'   => '11.99',
                        'currency' => 'USD',
                    ],
                ],

                [
                    'tld' => '.net',

                    'register' => [
                        'amount'   => '14.99',
                        'currency' => 'USD',
                    ],

                    'renew' => [
                        'amount'   => '16.99',
                        'currency' => 'USD',
                    ],

                    'transfer' => [
                        'amount'   => '13.99',
                        'currency' => 'USD',
                    ],
                ],

                [
                    'tld' => '.org',

                    'register' => [
                        'amount'   => '13.99',
                        'currency' => 'USD',
                    ],

                    'renew' => [
                        'amount'   => '15.99',
                        'currency' => 'USD',
                    ],

                    'transfer' => [
                        'amount'   => '12.99',
                        'currency' => 'USD',
                    ],
                ],

                [
                    'tld' => '.co.uk',

                    'register' => [
                        'amount'   => '10.99',
                        'currency' => 'USD',
                    ],

                    'renew' => [
                        'amount'   => '12.99',
                        'currency' => 'USD',
                    ],

                    'transfer' => [
                        'amount'   => '9.99',
                        'currency' => 'USD',
                    ],
                ],

            ],
        ],
    ];
}




public function get_addons($force_refresh = false) {

    if (
        !$force_refresh &&
        self::$runtime_addons !== null
    ) {
        return self::$runtime_addons;
    }

    if ($this->connection_mode === 'mock') {

        $result = $this->get_mock_addons();

        self::$runtime_addons = $result;

        return $result;
    }

    $cache = new HPC_Cache();

    if (!$force_refresh) {

        $cached = $cache->get_addons();

        if ($cached !== false) {

            $result = [
                'success' => true,
                'message' => '',
                'source'  => 'cache',
                'data'    => $cached,
            ];

            self::$runtime_addons = $result;

            return $result;
        }
    }

    $result = $this->request(
        '/api/v1/storefront/addons/',
        'addons'
    );

    if (!empty($result['success'])) {

        $result['source'] = 'live';

        $cache->set_addons($result['data']);
    }

    self::$runtime_addons = $result;

    return $result;
}



private function get_mock_addons() {

    return [
        'success' => true,
        'message' => '',
        'source'  => 'mock',

        'data' => [

            'addons' => [

                [
                    'id'          => 1,
                    'slug'        => 'ssl-certificate',
                    'name'        => 'SSL Certificate',
                    'description' => 'Secure your website with an SSL certificate.',

                    'pricing' => [
                        'annual' => [
                            'amount'   => '19.99',
                            'currency' => 'USD',
                        ],
                    ],
                ],

                [
                    'id'          => 2,
                    'slug'        => 'dedicated-ip',
                    'name'        => 'Dedicated IP',
                    'description' => 'Add a dedicated IP address to your hosting service.',

                    'pricing' => [
                        'monthly' => [
                            'amount'   => '3.99',
                            'currency' => 'USD',
                        ],
                    ],
                ],

                [
                    'id'          => 3,
                    'slug'        => 'daily-backup',
                    'name'        => 'Daily Backup',
                    'description' => 'Automatic daily backups for your website.',

                    'pricing' => [
                        'monthly' => [
                            'amount'   => '4.99',
                            'currency' => 'USD',
                        ],

                        'annual' => [
                            'amount'   => '49.99',
                            'currency' => 'USD',
                        ],
                    ],
                ],

                [
                    'id'          => 4,
                    'slug'        => 'malware-protection',
                    'name'        => 'Malware Protection',
                    'description' => 'Website malware monitoring and protection.',

                    'pricing' => [
                        'monthly' => [
                            'amount'   => '6.99',
                            'currency' => 'USD',
                        ],
                    ],
                ],

                [
                    'id'          => 5,
                    'slug'        => 'website-migration',
                    'name'        => 'Website Migration',
                    'description' => 'Professional website migration service.',

                    'pricing' => [
                        'one_time' => [
                            'amount'   => '29.99',
                            'currency' => 'USD',
                        ],
                    ],
                ],

                [
                    'id'          => 6,
                    'slug'        => 'extra-storage',
                    'name'        => 'Extra Storage',
                    'description' => 'Increase your hosting storage capacity.',

                    'pricing' => [
                        'monthly' => [
                            'amount'   => '5.99',
                            'currency' => 'USD',
                        ],
                    ],
                ],

            ],
        ],
    ];
}



}