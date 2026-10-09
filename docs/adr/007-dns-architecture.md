# ADR 07: DNS architecture

**Status:** Accepted 2026-10-10 (the business asked for all records to be completed; accepted as proposed)  
**Roadmap:** N0 (area 7 of 20)

## Context
H5 and N5 need customers to manage zones, and certificates to be validated through DNS.

## Options considered
| Option | Security | Isolation | Operational simplicity | Performance | Compatibility | Maintainability | Recovery |
|---|---|---|---|---|---|---|---|
| PowerDNS Authoritative with a database backend and its HTTP API, on two dedicated nameservers | 4 | 4 | 3 | 4 | 5 | 4 | 4 |
| BIND with zone files and rndc | 3 | 3 | 2 | 4 | 5 | 3 | 3 |
| Knot DNS | 4 | 4 | 3 | 5 | 4 | 3 | 4 |
| Hosted DNS (Cloudflare, Route 53) | 4 | 5 | 5 | 5 | 3 | 5 | 5 |

- **PowerDNS Authoritative with a database backend and its HTTP API**: API-driven (a good fit for WHPanel), supports ALIAS, DNSSEC, replication; standard record types as H5.
- **BIND with zone files and rndc**: Universal but file and reload based; harder to drive safely from an application.
- **Knot DNS**: Fast and modern, smaller ecosystem and API options.
- **Hosted DNS**: Least work and global anycast; customers must move nameservers to a third party and a per-zone cost applies.

## Recommendation
PowerDNS Authoritative with a database backend and the HTTP API, on two nameservers separate from web nodes, managed by WHPanel directly (not through the web-node agent). Hosted DNS stays an option if running nameservers proves too costly.

## Consequences
WHPanel talks to the DNS API with its own credentials; per-zone ownership checks live in WHPanel. Two nameservers in different data centres are required for resilience.

## Open questions
Where the second nameserver lives and whether to offer DNSSEC from the start.
