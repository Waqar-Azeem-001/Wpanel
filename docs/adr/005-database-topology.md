# ADR 05: Database topology

**Status:** Accepted 2026-10-10 (the business asked for all records to be completed; accepted as proposed)  
**Roadmap:** N0 (area 5 of 20)

## Context
H3 and N4 promise databases, users, privileges and usage per tenant, with plan limits enforced by the server.

## Options considered
| Option | Security | Isolation | Operational simplicity | Performance | Compatibility | Maintainability | Recovery |
|---|---|---|---|---|---|---|---|
| MariaDB LTS, one instance per node, per-tenant users with prefixed names and resource limits, the agent able to point at a separate database host | 4 | 3 | 4 | 4 | 5 | 4 | 4 |
| One MariaDB instance per tenant | 5 | 5 | 1 | 2 | 4 | 1 | 3 |
| Separate database nodes from the start | 4 | 4 | 2 | 4 | 5 | 3 | 4 |
| PostgreSQL as well | 4 | 3 | 3 | 4 | 2 | 3 | 4 |

- **MariaDB LTS**: Matches cPanel (MySQL-compatible, prefixed names); `MAX_USER_CONNECTIONS`, query-per-hour limits and per-schema size accounting are available.
- **One MariaDB instance per tenant**: Excellent isolation, far too much memory and management for shared plans; reserved for large plans.
- **Separate database nodes from the start**: Right at scale, extra cost and complexity for the first nodes.
- **PostgreSQL as well**: Some customers want it; cPanel parity is MySQL first.

## Recommendation
MariaDB LTS, one instance per node, with the database host a field of the node so databases can move to their own nodes without changing the customer experience. PostgreSQL is a later add-on.

## Consequences
A noisy query can still hurt neighbours on the same instance: limits (connections, query time, queries per hour) are mandatory. Backups use a consistent dump or physical snapshot (ADR 10).

## Open questions
Version to start on (the current LTS) and the default limits per plan.
