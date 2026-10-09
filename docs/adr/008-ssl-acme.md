# ADR 08: SSL and ACME

**Status:** Accepted 2026-10-10 (the business asked for all records to be completed; accepted as proposed)  
**Roadmap:** N0 (area 8 of 20)

## Context
N5: domain, DNS, validation, certificate, HTTPS and automatic renewal, with H6's customer view unchanged.

## Options considered
| Option | Security | Isolation | Operational simplicity | Performance | Compatibility | Maintainability | Recovery |
|---|---|---|---|---|---|---|---|
| Let's Encrypt (ZeroSSL as fallback) through an ACME client run by the agent: HTTP-01 per domain, DNS-01 through the PowerDNS API for wildcards | 4 | 4 | 4 | 5 | 5 | 4 | 4 |
| Caddy's built-in automatic HTTPS | 4 | 4 | 4 | 4 | 2 | 4 | 4 |
| Paid certificates | 4 | 4 | 3 | 5 | 5 | 3 | 4 |

- **Let's Encrypt**: Free, automatic, standard; renewal at 30 days left; must respect rate limits and handle domains that do not yet point at the node.
- **Caddy's built-in automatic HTTPS**: Neat but only if Caddy is the web server (rejected in ADR 01).
- **Paid certificates**: Needed only for extended features customers can buy as an add-on.

## Recommendation
Let's Encrypt with a second free authority as fallback, HTTP-01 per domain, DNS-01 for wildcards; the agent issues, installs and renews, WHPanel records status and expiry and warns early (the H6 and N10 screens). Certificate and key files are owned by root and readable only by the web server, never by tenants.

## Consequences
A new domain gets HTTPS only once its DNS points at the node (the screen already says so). Rate limits mean issuing is queued and spread.

## Open questions
Whether customers may upload their own certificates on native nodes (H6 deferred item).
