# Website care (Care page, overview, websites list)

What a customer gets on each website, and how each part works. Built 2026-10-13.

| Part | How it works | Where it works | Proven |
|---|---|---|---|
| Speed test | The panel fetches the home page like a visitor (public addresses only, redirects re-checked), measures server response and full time, size, HTTPS, compression, cache header; score 0-100 with plain advice. Last 20 kept. | every provider | unit tests; not a browser test (no Core Web Vitals) |
| Malware scan | Provider's own scan where it has one (demo). Otherwise WHPanel writes one PHP file, calls it once with a secret token; it removes itself, reads only, applies the shared rules and compares WordPress system files with wordpress.org checksums, writes a result file the panel reads and deletes. | every provider with file access (cPanel) | PHP run for real with php CLI in tests; not yet on a real cPanel; the checksum step needs internet from the server |
| WordPress updates | A small must-use plugin written to `wp-content/mu-plugins` sets WordPress's own auto-update filters (core: none / security / all; plugins; themes). WordPress applies them on its own cron. | WordPress sites WHPanel set up | rules file syntax-checked with PHP; not observed updating a live site |
| Automatic backups | Where the provider schedules itself (native: off/daily/weekly) the choice goes to it. Where it cannot (cPanel) WHPanel's daily job takes the backup and keeps the newest 3. | native; cPanel path untested on a real server | unit tests on the demo provider |
| Scheduled scans | Customer chooses off/weekly/daily; the daily job (`run_site_care_task`, 03:15) runs the due ones. | all | unit tests |

Not built (compared with Hostinger): staging site, CDN, object cache / LiteSpeed cache switch, WordPress plugin/theme manager, maintenance mode toggle, search-engine visibility toggle, visitor analytics, Git deploy, SSH access page, PageSpeed (Core Web Vitals) report, malware auto-clean.
