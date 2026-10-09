# ADR 17: Secret rotation

**Status:** Accepted 2026-10-10 (the business asked for all records to be completed; accepted as proposed)  
**Roadmap:** N0 (area 17 of 20)

## Context
Secrets exist at three levels: WHPanel's own, per-node, and per-tenant.

## Options considered
| Option | Security | Isolation | Operational simplicity | Performance | Compatibility | Maintainability | Recovery |
|---|---|---|---|---|---|---|---|
| Short-lived node certificates renewed by the agent, single-use enrolment tokens, per-node database and backup credentials generated at enrolment and stored encrypted by WHPanel (as server secrets are today), a documented rotation for each, and a break-glass procedure | 5 | 4 | 3 | 5 | 4 | 3 | 4 |
| Long-lived shared secrets | 1 | 1 | 5 | 5 | 4 | 3 | 2 |

- **Short-lived node certificates renewed by the agent**: Nothing is long-lived; compromise of one node does not give the keys to another.
- **Long-lived shared secrets**: Rejected.

## Recommendation
Node certificates valid 90 days and renewed automatically from day 60; enrolment tokens valid 15 minutes and single use; per-node database admin and backup-repository credentials unique and rotatable from WHPanel without downtime; WHPanel's own encryption key rotated with re-encryption of stored secrets; tenant passwords are never stored (as now).

## Consequences
A rotation job and a rotation log become part of the product. The existing encrypted-credential design is reused.

## Open questions
Exact rotation schedule for database admin credentials.
