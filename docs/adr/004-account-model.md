# ADR 04: Linux account and storage model

**Status:** Accepted 2026-10-10 (the business asked for all records to be completed; accepted as proposed)  
**Roadmap:** N0 (area 4 of 20)

## Context
Where a tenant's files live, who owns them and how disk and inodes are limited.

## Options considered
| Option | Security | Isolation | Operational simplicity | Performance | Compatibility | Maintainability | Recovery |
|---|---|---|---|---|---|---|---|
| One system user and group per hosting account, home under /srv/tenants/<id>, XFS project quotas for disk and inodes | 4 | 4 | 4 | 5 | 4 | 4 | 4 |
| Shared user per plan | 1 | 1 | 5 | 5 | 3 | 3 | 3 |
| Container volumes with quotas | 4 | 4 | 2 | 4 | 3 | 2 | 3 |

- **One system user and group per hosting account**: Quotas are enforced by the filesystem; the folder layout is the same for every tenant (public_html, logs, tmp, mail).
- **Shared user per plan**: No isolation.
- **Container volumes with quotas**: Works but needs the container model rejected in ADR 03.

## Recommendation
One system user and group per hosting account (numeric IDs allocated from a reserved range and recorded in WHPanel), a fixed layout under /srv/tenants/<id>, XFS with project quotas for disk and inodes, no login shell (SFTP only, ADR 09). File Manager paths stay relative to that folder, exactly as the sandbox does today.

## Consequences
Nodes must use XFS (or ext4 with quotas) for tenant storage. IDs are never reused so a deleted account's files can never be inherited by a new one.

## Open questions
Home on local disk or on network storage later (affects node replacement time, ADR 18).
