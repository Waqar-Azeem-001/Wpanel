# Website care (Care page, overview, websites list)

What a customer gets on each website, and how each part works. Built 2026-10-13.

| Part | How it works | Where it works | Proven |
|---|---|---|---|
| Speed test | The panel fetches the home page like a visitor (public addresses only, redirects re-checked), measures server response and full time, size, HTTPS, compression, cache header; score 0-100 with plain advice. Last 20 kept. | every provider | unit tests; not a browser test (no Core Web Vitals) |
| Malware scan | Provider's own scan where it has one (demo). Otherwise WHPanel writes one PHP file, calls it once with a secret token; it removes itself, reads only, applies the shared rules and compares WordPress system files with wordpress.org checksums, writes a result file the panel reads and deletes. | every provider with file access (cPanel) | PHP run for real with php CLI in tests; not yet on a real cPanel; the checksum step needs internet from the server |
| WordPress updates | A small must-use plugin written to `wp-content/mu-plugins` sets WordPress's own auto-update filters (core: none / security / all; plugins; themes). WordPress applies them on its own cron. | WordPress sites WHPanel set up | rules file syntax-checked with PHP; not observed updating a live site |
| Automatic backups | Where the provider schedules itself (native: off/daily/weekly) the choice goes to it. Where it cannot (cPanel) WHPanel's daily job takes the backup and keeps the newest 3. | native; cPanel path untested on a real server | unit tests on the demo provider |
| Scheduled scans | Customer chooses off/weekly/daily; the daily job (`run_site_care_task`, 03:15) runs the due ones. | all | unit tests |

| Maintenance mode, search-engine visibility | A second must-use plugin (`whp-site-options.php`): 503 "be right back" page for visitors (signed-in editors still see the site); `blog_public` forced to 0. | WordPress sites WHPanel set up | rules file syntax-checked with PHP; not observed on a live site |
| Faster pages | A marked block in `.htaccess` (compression, browser cache); the customer's own rules untouched; removable. | Apache/LiteSpeed (cPanel), not the native stack | merge logic unit-tested |
| Quarantine | Moves a flagged file into `.whp-quarantine` renamed `.quarantined`, with restore; WordPress system files are refused. Nothing is deleted. | any provider with file access | demo provider |
| Traffic | Parses the provider's access log: visitors, page views, top pages, status codes, robots left out. | where the provider can read the access log (not cPanel today) | parser unit-tested |
| Google PageSpeed report | Calls Google's PageSpeed API for the home page (phone/computer). Optional `PAGESPEED_API_KEY` setting for a higher quota. | every provider | mocked; not yet called for real |
| Plugins and themes | One-file tool using WordPress's own functions: list, switch on/off, update plugin/theme/core. | WordPress sites WHPanel set up | PHP syntax and token/self-removal tested; **the WordPress calls were not run against a real WordPress yet** |

Flow: a hosting plan is opened from the Hosting list; "Create website" lives inside the plan. After a website is created the domain is checked: with us / pointing here. A domain not with us gets a transfer link and a hosts-file preview line.

Added after review: **Websites** list (all websites, Dashboard + menu with Staging and Delete), **Delete website** (typed confirmation; removes files and the database WHPanel made, keeps plan/domain/email), **Staging** (subdomain + own folder + own database; publish to live with a backup first). The copier (`wp_clone`) was run for real with PHP and MariaDB (apps/hosting/tests/test_wp_clone.py, needs WHP_TEST_MYSQL).

Not built (compared with Hostinger): CDN, object cache / LiteSpeed cache switch, Git deploy, SSH access page, several websites inside one plan, a temporary preview domain (needs a wildcard domain you own pointed at the servers), automatic repair of modified WordPress files.
