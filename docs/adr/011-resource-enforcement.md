# ADR 11: Resource enforcement

**Status:** Proposed  
**Roadmap:** N0 (area 11 of 20)

## Context
N9: CPU, RAM, disk, processes, I/O, bandwidth, database use, email use, inodes and process isolation.

## Options considered
| Option | Security | Isolation | Operational simplicity | Performance | Compatibility | Maintainability | Recovery |
|---|---|---|---|---|---|---|---|
| cgroup v2 through systemd slices per tenant (CPU, memory, tasks, I/O), XFS project quotas (disk, inodes), PHP-FPM and MariaDB limits, nftables counters for bandwidth | 4 | 4 | 3 | 5 | 4 | 3 | 4 |
| CloudLinux LVE | 5 | 5 | 5 | 4 | 5 | 4 | 4 |
| Per-tenant containers with limits | 4 | 4 | 2 | 3 | 3 | 2 | 3 |

- **cgroup v2 through systemd slices per tenant**: All standard kernel features; limits are applied by the agent from the plan and can be read back for the usage screens.
- **CloudLinux LVE**: Simplest and best tested; licensed (ADR 03).
- **Per-tenant containers with limits**: Works, heavier (ADR 03).

## Recommendation
cgroup v2 slices, XFS project quotas, PHP-FPM and MariaDB limits and nftables accounting, written by the agent from the plan's limits and verified by a load test (a tenant that burns CPU, memory, disk or processes must not slow another). Bandwidth is measured per tenant and enforced by plan, not shaped in real time.

## Consequences
The 'what the plan allows' numbers the gateway already shows (H11) become real limits. Needs an automated noisy-neighbour test before launch.

## Open questions
Per-plan numbers and what happens when a limit is hit (throttle, suspend, notify).
