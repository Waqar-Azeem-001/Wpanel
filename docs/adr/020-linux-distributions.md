# ADR 20: Supported Linux distributions

**Status:** Accepted 2026-10-10 (chosen by the business, as recommended)  
**Roadmap:** N0 (area 20 of 20)

## Context
Fewer distributions means less to test. The current cPanel servers run AlmaLinux-family (with CloudLinux).

## Options considered
| Option | Security | Isolation | Operational simplicity | Performance | Compatibility | Maintainability | Recovery |
|---|---|---|---|---|---|---|---|
| AlmaLinux 9 (RHEL-compatible, supported to 2032, SELinux, PHP from Remi) | 5 | 4 | 4 | 4 | 5 | 4 | 4 |
| Rocky Linux 9 | 5 | 4 | 4 | 4 | 5 | 4 | 4 |
| Ubuntu Server 24.04 LTS | 4 | 4 | 5 | 4 | 4 | 5 | 4 |
| Debian 12 | 4 | 4 | 4 | 4 | 4 | 4 | 4 |

- **AlmaLinux 9**: Closest to what runs today; long support; SELinux adds a layer for tenant isolation at the cost of learning.
- **Rocky Linux 9**: Equivalent to AlmaLinux; pick one, not both.
- **Ubuntu Server 24.04 LTS**: Most familiar and best documented, AppArmor instead of SELinux, PHP from Sury, a five-year standard support window.
- **Debian 12**: Stable and lean, shorter overall window than the RHEL family.

## Recommendation
One distribution at launch: AlmaLinux 9 for all native nodes, so staff already know its tools and the migration path from cPanel (AlmaLinux-family) is shortest. Ubuntu 24.04 LTS is the alternative if the team prefers its tooling.

## Consequences
Playbooks, agent builds and tests target one OS; adding another later is a planned project.

## Open questions
Team familiarity decides this one.
