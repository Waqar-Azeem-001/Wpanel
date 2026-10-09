# ADR 18: Disaster recovery

**Status:** Accepted 2026-10-10 (the business asked for all records to be completed; accepted as proposed)  
**Roadmap:** N0 (area 18 of 20)

## Context
What happens when a node, a data centre, WHPanel itself or the backup storage is lost.

## Options considered
| Option | Security | Isolation | Operational simplicity | Performance | Compatibility | Maintainability | Recovery |
|---|---|---|---|---|---|---|---|
| Targets (to be agreed): RPO 24 hours for websites and databases, RTO 4 hours for a node; node rebuilt from Ansible onto a new machine and tenants restored from backup, DNS repointed, WHPanel's own database backed up separately, a runbook for each failure and a yearly recovery drill | 4 | 4 | 3 | 4 | 4 | 4 | 5 |
| High availability pairs with replication | 4 | 4 | 1 | 4 | 3 | 1 | 5 |

- **Targets**: Honest and testable; costs a drill every year.
- **High availability pairs with replication**: Faster recovery, doubles cost and complexity: not for the first nodes.

## Recommendation
Backup-and-rebuild recovery with written targets and a yearly drill, WHPanel's database and secrets backed up separately from node backups, break-glass access documented, and the provider's console as the last resort.

## Consequences
The first real native restore test is itself a deliverable, not an assumption.

## Open questions
The targets: are 24 hours of possible data loss and 4 hours of downtime acceptable to customers and to the business?
