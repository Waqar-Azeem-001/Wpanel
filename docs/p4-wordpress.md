# Create website: WordPress and custom websites (P4, first part)

After a hosting plan is active the customer opens **Create website** (Websites page, or the callout on the plan's dashboard):

1. **Type**: WordPress, or a custom website (empty, for their own HTML or PHP files).
2. **Details** (WordPress): website name, administrator username, email and password (a strong password is suggested).
3. **Install**: a progress page; then the plan's dashboard shows WordPress, its version, the administrator and a **WP Admin** button.

## What the server does for WordPress (`INSTALL_WORDPRESS`, `apps/agent/node/wordpress.py` and `agent/internal/handlers/wordpress.go`)
Refuses to install over files that are not the placeholder page; makes `<account>_wp` database and user through the Databases handlers (plan limit and name rule apply); downloads WordPress from
wordpress.org and **checks it against the published SHA-1** before unpacking; unpacks with the safe reader; writes `wp-config.php` with fresh random keys; downloads WP-CLI once (checked against its
published SHA-512) and completes the installation as the account's own user inside its slice, the administrator's password on standard input; removes everything it made if any step fails.
The administrator's password is never stored by WHPanel, never in the audit trail, hidden in the job record, and never in a command line. The database password exists only in `wp-config.php`.

## What is and is not proven
| | |
|---|---|
| Python and Go give the same answers, files, database objects and programs on 6 scenarios (install, repeat, tampered download, network down, folder not empty, hostile archive, no database allowed) | Yes (golden test) |
| Whole chain through WHPanel on a pretend node: request, signed job, result, screen | Yes |
| A real download from wordpress.org, real WP-CLI, `systemd-run`, a real WordPress answering | **Never run.** The two download addresses for WP-CLI (`raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar` and its `.sha512`) are written from memory of how WP-CLI publishes and must be checked first |
| cPanel servers | WordPress is **not offered** there (the option shows as not available); the custom website works |
| More than one website in a plan | **Not built.** One website per hosting account until multiple domains per native account exist (roadmap N12) |

## Not built (asked for, honest list)
Staging, cloning, auto-updates, caching, speed test, migration from another WordPress host, WooCommerce presets, and choosing the WordPress language.
