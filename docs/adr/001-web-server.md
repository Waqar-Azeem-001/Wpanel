# ADR 01: Web server

**Status:** Accepted 2026-10-10 (chosen by the business, as recommended)  
**Roadmap:** N0 (area 1 of 20)

## Context
Every native website is served by this. Existing customers come from cPanel, where the web server is Apache and sites depend on `.htaccess` (WordPress permalinks, caching and security plugins, redirects). The choice sets how much of a migrated site works untouched.

## Options considered
| Option | Security | Isolation | Operational simplicity | Performance | Compatibility | Maintainability | Recovery |
|---|---|---|---|---|---|---|---|
| Apache 2.4 (event MPM) + PHP-FPM | 4 | 4 | 4 | 3 | 5 | 4 | 4 |
| Nginx + PHP-FPM | 4 | 4 | 3 | 5 | 2 | 4 | 4 |
| OpenLiteSpeed / LiteSpeed | 3 | 4 | 3 | 5 | 4 | 3 | 3 |
| Caddy + PHP-FPM | 4 | 4 | 4 | 4 | 1 | 4 | 4 |

- **Apache 2.4**: Reads `.htaccess` as cPanel sites expect; mod_security available; most documentation and staff know-how; slower than Nginx for static files and many connections.
- **Nginx + PHP-FPM**: Fastest and lightest, but has no `.htaccess`: every rewrite rule of a migrated site must be translated, and plugins that write `.htaccess` stop working.
- **OpenLiteSpeed / LiteSpeed**: Reads most `.htaccess`, very fast PHP; OpenLiteSpeed is free but has a smaller community and a less complete `.htaccess` feature set; LiteSpeed Enterprise is licensed per server.
- **Caddy + PHP-FPM**: Simplest TLS, but no `.htaccess` and a different config model; poor fit for migrated cPanel sites.

## Recommendation
Apache 2.4 with the event MPM, handing PHP to PHP-FPM (ADR 02), for the first native nodes. Nginx may later sit in front for TLS and static files (a reverse proxy) without changing what customers see.

## Consequences
Migrated sites work with little or no change. Higher memory per connection than Nginx: size nodes accordingly. mod_security with a maintained rule set is available as a protection layer.

## Open questions
Decide whether `.htaccess` compatibility is worth the performance cost for new, non-migrated sites. If the business expects mostly new WordPress sites and few migrations, Nginx becomes more attractive.
