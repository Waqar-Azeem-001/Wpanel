# H6 SSL and H7 PHP management

**Source:** next-generation roadmap, H6 and H7. Local only (demo provider).

## What was built
- **Contract:** `SslStatus` gained `status` (none, active, expiring, expired), `issued_at` and `days_left`; `SslCapability` now has
  list, get, request (install or renew), remove, auto-renew and always-use-HTTPS. `PhpOptions` and `PhpExtension` are new;
  `PhpCapability` has options, extensions list and switch, settings get and set. All new reads are allowlisted as read operations.
- **Demo SSL** (`demo_ssl.py`): a certificate needs a domain on the account whose DNS (A record, or a CNAME that leads to one) points
  at the server; it is valid 90 days; status is worked out from dates (ends soon at 14 days or fewer); with auto-renew on it renews
  itself in the last 30 days; always-use-HTTPS needs a working certificate and goes off when the certificate is removed or expires.
- **Demo PHP** (`demo_php.py`): per domain version, memory limit, largest upload, run time, error display and extensions, accepted
  only from the plan's lists (8.0 and 7.4 flagged as no longer supported); the upload limit cannot exceed the memory limit; `mysqli`
  and `mbstring` are required and stay on. No raw configuration is ever exposed.
- **Screens:** SSL (status pills, issuer, valid-until with days left, auto-renew and HTTPS switches, install/renew/remove with
  confirmation, warnings for expired or ending certificates) and PHP (domain tabs, version and limits form, error-display switch
  with a live-site warning, extensions table).
- **Sample data:** the demo accounts show an active, an ending-soon, an expired and a no-certificate state, and one account on PHP 8.0.
- Audit records domain and operation (and PHP version), never anything secret.

## Verified
17 new tests (`test_ssl_php.py`): provider rules, both screens end to end, refusals shown as messages, audit, strangers get 404, a
provider without these capabilities shows the not-available page. Real certificates and real PHP need the cPanel/native adapter (H14).

## Deferred
Uploading a custom certificate, per-domain `.htaccess` tools, PHP-FPM pool tuning, and the 375 / 768 px browser pass.
