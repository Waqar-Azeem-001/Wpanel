# ADR 14: Operating-system and server updates

**Status:** Accepted 2026-10-10 (the business asked for all records to be completed; accepted as proposed)  
**Roadmap:** N0 (area 14 of 20)

## Context
Security patches must land fast without taking customers down.

## Options considered
| Option | Security | Isolation | Operational simplicity | Performance | Compatibility | Maintainability | Recovery |
|---|---|---|---|---|---|---|---|
| Automatic security updates (dnf-automatic or unattended-upgrades), kernel live patching optional, a monthly reboot window with the node drained of new accounts, all configuration applied by Ansible from version control | 5 | 4 | 4 | 4 | 4 | 4 | 5 |
| Manual patching | 2 | 3 | 2 | 4 | 4 | 1 | 3 |
| Rebuild nodes from images and replace them | 5 | 4 | 2 | 4 | 4 | 3 | 5 |

- **Automatic security updates**: Fast patching with predictable reboots; every node is reproducible from code.
- **Manual patching**: Patches get forgotten.
- **Rebuild nodes from images and replace them**: Cleanest, needs shared storage or fast restores to make sense.

## Recommendation
Automatic security updates, a monthly maintenance window, nodes marked 'maintenance' in WHPanel before a reboot (H13 already shows that state), and Ansible for every node's configuration. The agent is upgraded separately and safely (N1).

## Consequences
Every change to a node goes through the playbooks, so a replaced node is the same as the old one.

## Open questions
Whether to buy a live-patching service.
