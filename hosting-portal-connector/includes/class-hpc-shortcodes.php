<?php

if (!defined('ABSPATH')) {
    exit;
}

class HPC_Shortcodes {

    public function init() {

    add_shortcode(
        'hosting_plans',
        [$this, 'render_hosting_plans']
    );

    add_shortcode(
    'domain_prices',
    [$this, 'render_domain_prices']
);

    add_shortcode(
        'hosting_categories',
        [$this, 'render_hosting_categories']
    );
    add_shortcode(
    'hosting_addons',
    [$this, 'render_hosting_addons']
);

    add_shortcode(
        'hosting_plan',
        [$this, 'render_hosting_plan']
    );

    add_shortcode(
        'hosting_store',
        [$this, 'render_hosting_store']
    );


}

/**
 * Render a store page: a category menu, then that category's plans.
 *
 * Shortcode: [hosting_store columns="3"]
 *
 * The chosen category travels in the address (?hpc_category=slug), so every
 * category is a real, shareable link and no JavaScript is needed.
 */
public function render_hosting_store($atts = []) {

    $atts = shortcode_atts(
        [
            'columns' => '3',
        ],
        $atts,
        'hosting_store'
    );

    $client = new HPC_API_Client();

    $categories = $client->get_categories();

    if (empty($categories['success']) || empty($categories['data']['categories'])) {
        return '<div class="hpc-message">No hosting plans available.</div>';
    }

    $selected = isset($_GET['hpc_category'])
        ? sanitize_title(wp_unslash($_GET['hpc_category']))
        : '';

    $html  = '<nav class="hpc-store-menu" aria-label="' . esc_attr__('Hosting categories', 'hosting-portal-connector') . '"><ul>';

    $html .= sprintf(
        '<li><a href="%s"%s>%s</a></li>',
        esc_url(remove_query_arg('hpc_category')),
        $selected === '' ? ' aria-current="page" class="is-active"' : '',
        esc_html__('All', 'hosting-portal-connector')
    );

    foreach ($categories['data']['categories'] as $category) {

        $html .= sprintf(
            '<li><a href="%s"%s>%s</a></li>',
            esc_url(add_query_arg('hpc_category', $category['slug'])),
            $selected === $category['slug'] ? ' aria-current="page" class="is-active"' : '',
            esc_html($category['name'])
        );
    }

    $html .= '</ul></nav>';

    $html .= $this->render_hosting_plans([
        'category' => $selected,
        'columns'  => $atts['columns'],
        'orderby'  => 'sort_order',
    ]);

    return $html;
}

/**
 * Render one plan.
 *
 * Shortcode: [hosting_plan slug="business-hosting"]
 */
public function render_hosting_plan($atts = []) {

    $atts = shortcode_atts(
        [
            'slug' => '',
        ],
        $atts,
        'hosting_plan'
    );

    $slug = sanitize_title($atts['slug']);

    if ($slug === '') {
        return '<div class="hpc-message">Choose a plan with [hosting_plan slug="..."].</div>';
    }

    $client = new HPC_API_Client();

    $product = $client->get_product($slug);

    // Unpublished, switched off or renamed: say so quietly, never a technical error.
    if (empty($product)) {
        return '<div class="hpc-message">This plan is not available.</div>';
    }

    $products = [$product];
    $columns  = 1;

    ob_start();

    include HPC_PLUGIN_DIR . 'public/views/plans-grid.php';

    return ob_get_clean();
}

public function render_hosting_plans($atts = []) {

    $atts = shortcode_atts(
        [
            'category' => '',
            'columns'  => '3',
            'limit'    => '0',
            'featured' => '',
            'order'    => 'asc',
            'orderby'  => 'name',
        ],
        $atts,
        'hosting_plans'
    );

    $client = new HPC_API_Client();

    $result = $client->get_products();

    if (
        empty($result['success']) ||
        empty($result['data']['products']) ||
        !is_array($result['data']['products'])
    ) {
        return '<div class="hpc-message">No hosting plans available.</div>';
    }

    $products = $result['data']['products'];


    /*
    |--------------------------------------------------------------------------
    | Category Filter
    |--------------------------------------------------------------------------
    */

    if (!empty($atts['category'])) {

        $category = sanitize_title(
            $atts['category']
        );

        $products = array_filter(
            $products,
            function ($product) use ($category) {

                if (
                    empty($product['category']) ||
                    empty($product['category']['slug'])
                ) {
                    return false;
                }

                return sanitize_title(
                    $product['category']['slug']
                ) === $category;
            }
        );
    }


    /*
    |--------------------------------------------------------------------------
    | Featured Filter
    |--------------------------------------------------------------------------
    */

    if ($atts['featured'] === 'yes') {

        $products = array_filter(
            $products,
            function ($product) {

                return !empty(
                    $product['featured']
                );
            }
        );
    }


    /*
    |--------------------------------------------------------------------------
    | Sorting
    |--------------------------------------------------------------------------
    */

    $orderby = sanitize_key(
        $atts['orderby']
    );

    $order = strtolower(
        sanitize_text_field(
            $atts['order']
        )
    );

    if (!in_array($order, ['asc', 'desc'], true)) {
        $order = 'asc';
    }

    usort(
        $products,
        function ($a, $b) use ($orderby, $order) {

            $value_a = '';
            $value_b = '';

            if ($orderby === 'sort_order') {

                $value_a = isset($a['sort_order'])
                    ? intval($a['sort_order'])
                    : 100;

                $value_b = isset($b['sort_order'])
                    ? intval($b['sort_order'])
                    : 100;

            } elseif ($orderby === 'id') {

                $value_a = isset($a['id'])
                    ? intval($a['id'])
                    : 0;

                $value_b = isset($b['id'])
                    ? intval($b['id'])
                    : 0;

            } else {

                $value_a = isset($a['name'])
                    ? strtolower($a['name'])
                    : '';

                $value_b = isset($b['name'])
                    ? strtolower($b['name'])
                    : '';
            }

            if ($value_a == $value_b) {
                return 0;
            }

            $result = ($value_a < $value_b)
                ? -1
                : 1;

            if ($order === 'desc') {
                $result *= -1;
            }

            return $result;
        }
    );


    /*
    |--------------------------------------------------------------------------
    | Limit
    |--------------------------------------------------------------------------
    */

    $limit = absint(
        $atts['limit']
    );

    if ($limit > 0) {

        $products = array_slice(
            $products,
            0,
            $limit
        );
    }


    /*
    |--------------------------------------------------------------------------
    | Columns
    |--------------------------------------------------------------------------
    */

    $columns = absint(
        $atts['columns']
    );

    if ($columns < 1) {
        $columns = 1;
    }

    if ($columns > 4) {
        $columns = 4;
    }


    if (empty($products)) {

        return '<div class="hpc-message">No hosting plans found.</div>';
    }


    ob_start();

    include HPC_PLUGIN_DIR . 'public/views/plans-grid.php';

    return ob_get_clean();
}



public static function format_cycle_name($cycle) {

    $labels = [
    'monthly'     => 'Monthly',
    'quarterly'   => 'Quarterly',
    'semi_annual' => 'Semi-Annual',
    'annual'      => 'Annual',
    'biennial'    => 'Biennial',
    'triennial'   => 'Triennial',
    'one_time'    => 'One Time',
    ];

    if (isset($labels[$cycle])) {
        return $labels[$cycle];
    }

    return ucwords(
        str_replace(
            ['_', '-'],
            ' ',
            $cycle
        )
    );
}

public static function format_cycle_label($cycle) {

    $labels = [
        'monthly'      => 'mo',
        'quarterly'    => '3 mo',
        'semi_annual'  => '6 mo',
        'annual'       => 'yr',
        'biennial'     => '2 yrs',
        'triennial'    => '3 yrs',
    ];

    if (isset($labels[$cycle])) {
        return $labels[$cycle];
    }

    return self::format_cycle_name($cycle);
}
    


/**
 * Render available hosting categories.
 *
 * Shortcode:
 *
 * [hosting_categories]
 *
 * @return string
 */
public function render_hosting_categories() {

    $client = new HPC_API_Client();

    $result = $client->get_categories();

    if (
        empty($result['success']) ||
        empty($result['data']['categories']) ||
        !is_array($result['data']['categories'])
    ) {

        return '<div class="hpc-message">No hosting categories found.</div>';
    }

    $categories = $result['data']['categories'];

    ob_start();

    include HPC_PLUGIN_DIR . 'public/views/categories-grid.php';

    return ob_get_clean();
}




public function render_domain_prices($atts = []) {

    $atts = shortcode_atts(
        [
            'limit' => '0',
        ],
        $atts,
        'domain_prices'
    );

    $client = new HPC_API_Client();

    $result = $client->get_domain_prices();

    if (
        empty($result['success']) ||
        empty($result['data']['domains']) ||
        !is_array($result['data']['domains'])
    ) {
        return '<div class="hpc-message">No domain pricing available.</div>';
    }

    $domains = $result['data']['domains'];

    $limit = absint(
        $atts['limit']
    );

    if ($limit > 0) {

        $domains = array_slice(
            $domains,
            0,
            $limit
        );
    }

    if (empty($domains)) {

        return '<div class="hpc-message">No domain pricing found.</div>';
    }

    ob_start();

    include HPC_PLUGIN_DIR . 'public/views/domain-prices.php';

    return ob_get_clean();
}







public function render_hosting_addons($atts = []) {

    $atts = shortcode_atts(
        [
            'columns' => '3',
            'limit'   => '0',
        ],
        $atts,
        'hosting_addons'
    );

    $client = new HPC_API_Client();

    $result = $client->get_addons();

    if (
        empty($result['success']) ||
        empty($result['data']['addons']) ||
        !is_array($result['data']['addons'])
    ) {
        return '<div class="hpc-message">No hosting addons available.</div>';
    }

    $addons = $result['data']['addons'];


    /*
    |--------------------------------------------------------------------------
    | Limit
    |--------------------------------------------------------------------------
    */

    $limit = absint(
        $atts['limit']
    );

    if ($limit > 0) {

        $addons = array_slice(
            $addons,
            0,
            $limit
        );
    }


    /*
    |--------------------------------------------------------------------------
    | Columns
    |--------------------------------------------------------------------------
    */

    $columns = absint(
        $atts['columns']
    );

    if ($columns < 1) {
        $columns = 1;
    }

    if ($columns > 4) {
        $columns = 4;
    }


    if (empty($addons)) {

        return '<div class="hpc-message">No hosting addons found.</div>';
    }


    ob_start();

    include HPC_PLUGIN_DIR . 'public/views/addons-grid.php';

    return ob_get_clean();
}



}