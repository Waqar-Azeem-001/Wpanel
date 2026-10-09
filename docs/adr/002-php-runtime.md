# ADR 02: PHP process and runtime model

**Status:** Proposed  
**Roadmap:** N0 (area 2 of 20)

## Context
H7 promises customers a choice of PHP version and a few safe settings per domain. The model decides whether one customer's PHP can see or starve another's.

## Options considered
| Option | Security | Isolation | Operational simplicity | Performance | Compatibility | Maintainability | Recovery |
|---|---|---|---|---|---|---|---|
| PHP-FPM, one pool per tenant, several versions side by side | 5 | 5 | 3 | 4 | 5 | 4 | 4 |
| mod_php | 1 | 1 | 5 | 3 | 4 | 3 | 3 |
| suPHP / CGI | 4 | 4 | 3 | 1 | 4 | 2 | 3 |
| LSAPI (with LiteSpeed) | 4 | 4 | 3 | 5 | 4 | 3 | 3 |

- **PHP-FPM**: Each pool runs as its own Linux user with its own limits and its own `php.ini` values; versions come from a maintained repository (Remi on RHEL family, Sury on Debian family).
- **mod_php**: All sites run as the web server user: unacceptable for shared hosting.
- **suPHP / CGI**: Per-user but spawns a process per request: very slow; obsolete.
- **LSAPI**: Excellent but ties PHP to the LiteSpeed decision (ADR 01).

## Recommendation
PHP-FPM with one pool per tenant, each version in its own FPM master, pool settings written by the agent from the allowlist H7 already enforces (memory limit, upload size, execution time, error display, a fixed extension set per version).

## Consequences
Idle pools cost memory: use `pm = ondemand` for small plans. A version reaching end of life is retired on a published schedule. Extension choice stays server-owned, as on cPanel.

## Open questions
Which versions at launch (8.2, 8.3, 8.4?) and how long old ones are kept.
