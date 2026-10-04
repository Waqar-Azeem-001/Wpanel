<?php
// Minimal WordPress stand-ins so the plugin classes can run under plain PHP CLI.
define('ABSPATH', __DIR__);
$GLOBALS['opts'] = [
    'hpc_portal_url'      => 'https://portal.example.com/',
    'hpc_api_key'         => 'sf_ABC:secretvalue',
    'hpc_connection_mode' => 'live',
];
$GLOBALS['transients'] = [];
$GLOBALS['last_request'] = null;
$GLOBALS['responses'] = [];

function untrailingslashit($s) { return rtrim($s, '/'); }
function get_option($k, $d = '') { return $GLOBALS['opts'][$k] ?? $d; }
function update_option($k, $v, $a = null) { $GLOBALS['opts'][$k] = $v; return true; }
function get_transient($k) { return $GLOBALS['transients'][$k] ?? false; }
function set_transient($k, $v, $t) { $GLOBALS['transients'][$k] = $v; return true; }
function delete_transient($k) { unset($GLOBALS['transients'][$k]); return true; }
function absint($v) { return abs((int) $v); }
function current_time($t) { return date('Y-m-d H:i:s'); }
function is_wp_error($r) { return false; }
function sanitize_title($s) { return strtolower(trim(preg_replace('/[^A-Za-z0-9-]+/', '-', $s), '-')); }
function sanitize_text_field($s) { return trim(strip_tags($s)); }
function wp_remote_get($url, $args) {
    $GLOBALS['last_request'] = ['url' => $url, 'args' => $args];
    foreach ($GLOBALS['responses'] as $suffix => $resp) {
        if (substr($url, -strlen($suffix)) === $suffix) { return $resp; }
    }
    return ['code' => 404, 'body' => ''];
}
function wp_remote_retrieve_response_code($r) { return $r['code']; }
function wp_remote_retrieve_body($r) { return $r['body']; }

$dir = $argv[1];
require $dir . '/includes/class-hpc-cache.php';
require $dir . '/includes/class-hpc-api-client.php';
class WP_REST_Response { function __construct($d, $s) { $this->data = $d; $this->status = $s; } }
function add_action() {}
function register_rest_route() {}
require $dir . '/includes/class-hpc-webhook.php';
function add_shortcode() {}
function wp_unslash($v) { return $v; }
define('MINUTE_IN_SECONDS', 60);
function esc_url_raw($u) { return $u; }
require $dir . '/includes/class-hpc-domain-search.php';

$fail = 0;
function check($name, $cond) { global $fail; echo ($cond ? 'PASS ' : 'FAIL ') . $name . "\n"; if (!$cond) { $fail++; } }

// --- Real shapes copied from the Django serializers (docs/e13-storefront-integration.md) ---
$GLOBALS['responses']['/api/v1/storefront/products/'] = ['code' => 200, 'body' => json_encode([[
    'id' => 10, 'slug' => 'live-plan', 'type' => 'shared_hosting', 'name' => 'Live Plan', 'description' => '',
    'category' => ['slug' => 'shared', 'name' => 'Shared hosting', 'group' => 'Hosting'],
    'pricing' => ['monthly' => ['billing_cycle' => 'monthly', 'months' => 1, 'amount' => '9.99', 'setup_fee' => '0.00', 'currency' => 'USD']],
    'features' => ['Disk space: 10 GB'], 'featured' => true, 'sort_order' => 100,
    'order_url' => 'https://portal.example.com/products/live-plan/', 'updated_at' => '2026-10-02T18:29:02Z',
]])];
$GLOBALS['responses']['/api/v1/storefront/domains/pricing/'] = ['code' => 200, 'body' => json_encode([[
    'tld' => '.com', 'register_price' => '12.99', 'renew_price' => '14.99', 'transfer_price' => '11.99',
    'min_years' => 1, 'max_years' => 10, 'currency' => 'USD',
]])];
$GLOBALS['responses']['/api/v1/storefront/addons/'] = ['code' => 200, 'body' => json_encode([[
    'id' => 1, 'slug' => 'ssl', 'name' => 'SSL', 'description' => 'd',
    'pricing' => ['annual' => ['billing_cycle' => 'annual', 'months' => 12, 'amount' => '19.99', 'setup_fee' => '0.00', 'currency' => 'USD']],
]])];

$client = new HPC_API_Client();
$r = $client->get_products(true);
check('products: success', !empty($r['success']));
check('products: wrapped under data.products', isset($r['data']['products'][0]['slug']) && $r['data']['products'][0]['slug'] === 'live-plan');
check('products: nested price readable', $r['data']['products'][0]['pricing']['monthly']['amount'] === '9.99');
check('auth header uses Storefront scheme', $GLOBALS['last_request']['args']['headers']['Authorization'] === 'Storefront sf_ABC:secretvalue');
check('url joins without double slash', $GLOBALS['last_request']['url'] === 'https://portal.example.com/api/v1/storefront/products/');
check('single product lookup by slug', $client->get_product('live-plan')['id'] === 10);
check('single product lookup by id', $client->get_product('10')['slug'] === 'live-plan');
check('unknown product is null', $client->get_product('nope') === null);

$d = $client->get_domain_prices(true);
check('domains: mapped to register/renew/transfer', $d['data']['domains'][0]['register']['amount'] === '12.99' && $d['data']['domains'][0]['transfer']['currency'] === 'USD');
$a = $client->get_addons(true);
check('addons: wrapped under data.addons', $a['data']['addons'][0]['slug'] === 'ssl');
check('domains + addons now in persistent cache', isset($GLOBALS['transients']['hpc_cached_domains'], $GLOBALS['transients']['hpc_cached_addons']));
check('diagnostics recorded', !empty($GLOBALS['opts']['hpc_last_request']['ms']) || $GLOBALS['opts']['hpc_last_request']['ms'] === 0);

// Failure paths: bad credential and wrong format
$GLOBALS['responses']['/api/v1/storefront/products/'] = ['code' => 401, 'body' => '{"detail":"x"}'];
$bad = (new HPC_API_Client())->get_products(true);
check('401 gives a clear message, not data', empty($bad['success']) && strpos($bad['message'], 'rejected the credential') !== false);
check('last error recorded', strpos($GLOBALS['opts']['hpc_last_error'], 'rejected') !== false);
$GLOBALS['opts']['hpc_api_key'] = 'justonepart';
$fmt = (new HPC_API_Client())->get_products(true);
check('credential without colon is refused locally', empty($fmt['success']) && strpos($fmt['message'], 'public_id:secret') !== false);

// Domain search (live) and its mock
$GLOBALS['opts']['hpc_api_key'] = 'sf_ABC:secretvalue';
$GLOBALS['responses']['/api/v1/storefront/domains/search/?name=example.com'] = ['code' => 200, 'body' => json_encode([
    'domain' => 'example.com', 'available' => true, 'tld' => '.com', 'register_price' => '12.99', 'renew_price' => '14.99',
    'currency' => 'USD', 'order_url' => 'https://portal.example.com/domains/?domain=example.com&years=1']) ];
$s = (new HPC_API_Client())->search_domain('example.com');
check('domain search: live answer passes through', !empty($s['success']) && $s['data']['available'] === true && $s['data']['register_price'] === '12.99');
check('domain search: name is url-encoded in the request', strpos($GLOBALS['last_request']['url'], 'domains/search/?name=example.com') !== false);
$GLOBALS['opts']['hpc_connection_mode'] = 'mock';
check('domain search: mock reports taken names', (new HPC_API_Client())->search_domain('taken.com')['data']['available'] === false);
check('domain search: mock reports free names', (new HPC_API_Client())->search_domain('free.com')['data']['available'] === true);
$GLOBALS['opts']['hpc_connection_mode'] = 'live';

class FakeReq { function __construct($n) { $this->n = $n; } function get_param($k) { return $this->n; } }
$ds = new HPC_Domain_Search();
$_SERVER['REMOTE_ADDR'] = '203.0.113.9';
check('domain search route: rejects a non-domain', $ds->handle(new FakeReq('not a domain'))->status === 400);
check('domain search route: rejects an injection attempt', $ds->handle(new FakeReq('a.com/../x?y=1'))->status === 400);
$ok = $ds->handle(new FakeReq('example.com'));
check('domain search route: answers a valid name', $ok->status === 200 && $ok->data['available'] === true && $ok->data['price'] === '12.99');
$GLOBALS['transients'] = [];
for ($i = 0; $i < 20; $i++) { $ds->handle(new FakeReq('example.com')); }
check('domain search route: rate limited after 20 a minute', $ds->handle(new FakeReq('example.com'))->status === 429);

// Webhook signature, computed by Django's own sign() (argv[2] = "t=..,v1=..", argv[3] = raw body, argv[4] = secret)
[$hdr, $body, $secret] = [$argv[2], $argv[3], $argv[4]];
$t = (int) explode(',', $hdr)[0] === 0 ? (int) substr(explode(',', $hdr)[0], 2) : 0;
check('webhook: Django-signed ping verifies', HPC_Webhook::signature_is_valid($hdr, $body, $secret, $t));
check('webhook: wrong secret rejected', !HPC_Webhook::signature_is_valid($hdr, $body, 'other', $t));
check('webhook: tampered body rejected', !HPC_Webhook::signature_is_valid($hdr, $body . 'x', $secret, $t));
check('webhook: stale ping rejected', !HPC_Webhook::signature_is_valid($hdr, $body, $secret, $t + 100000));
check('webhook: empty header rejected', !HPC_Webhook::signature_is_valid('', $body, $secret, $t));

echo $fail === 0 ? "ALL PASSED\n" : "$fail FAILED\n";
exit($fail === 0 ? 0 : 1);
