# ADR 19: cPanel migration compatibility

**Status:** Accepted 2026-10-10 (the business asked for all records to be completed; accepted as proposed)  
**Roadmap:** N0 (area 19 of 20)

## Context
M-track moves existing accounts from cPanel to native nodes without breaking sites.

## Options considered
| Option | Security | Isolation | Operational simplicity | Performance | Compatibility | Maintainability | Recovery |
|---|---|---|---|---|---|---|---|
| Import from cPanel's own account backup (`pkgacct`): home folder, MySQL dumps and grants, mail in Maildir, DNS zones, cron, SSL, domains, PHP version mapped to the nearest FPM version; a dry run per account, then cutover, with the cPanel account kept until the customer confirms | 4 | 4 | 3 | 4 | 5 | 3 | 5 |
| File-by-file copy plus manual database export | 3 | 3 | 2 | 3 | 3 | 2 | 4 |

- **Import from cPanel's own account backup**: The format is documented and complete; Maildir is what Dovecot reads; `.htaccess` keeps working because ADR 01 keeps Apache.
- **File-by-file copy plus manual database export**: Works for a few accounts, does not scale.

## Recommendation
Build the importer around `pkgacct` backups, keep Apache for `.htaccess`, keep MariaDB for MySQL compatibility and Maildir for mail, and make every migration a dry-run-first, reversible job with a verification step (the site answers, the database opens, a test email is delivered).

## Consequences
These choices were made partly to keep migration cheap; changing the web server or mail format later would make M-track harder.

## Open questions
Which accounts go first (the quietest ones) and how a rollback is communicated to the customer.

## Follow-up (M0-M4)
Built as recommended (`docs/m-cpanel-migration.md`): `pkgacct` archive importer on the node, dry-run report first, reversible cutover with a rollback window. **Different from the plan:** the backup is made and copied by an operator (the WHM API cannot make a `pkgacct` archive); addon domains are a manual
decision because a native account serves one domain; cron and certificates are not moved. Never tried against a real archive.
