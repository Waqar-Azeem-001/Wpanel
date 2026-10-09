# ADR 12: Firewall and network model

**Status:** Proposed  
**Roadmap:** N0 (area 12 of 20)

## Context
Public web, mail and SFTP ports must be open; management must not be.

## Options considered
| Option | Security | Isolation | Operational simplicity | Performance | Compatibility | Maintainability | Recovery |
|---|---|---|---|---|---|---|---|
| nftables default-deny on every node, public 80/443 and SFTP only, agent traffic over outbound mutual TLS (ADR 16), administrators through a WireGuard VPN, CrowdSec or fail2ban for abuse | 5 | 4 | 3 | 5 | 4 | 4 | 4 |
| A control panel firewall (CSF) as on cPanel nodes | 3 | 3 | 4 | 4 | 4 | 3 | 3 |
| Cloud provider firewall only | 3 | 3 | 5 | 5 | 4 | 4 | 4 |

- **nftables default-deny on every node**: Nothing management-related is reachable from the internet; the 15,200 failed root logins seen on the live server show why.
- **A control panel firewall**: Familiar, ties the node to the old tooling.
- **Cloud provider firewall only**: A useful extra layer, not a substitute for a host firewall.

## Recommendation
nftables default-deny with the provider's firewall as a second layer, SSH only on the VPN with keys (no password login, no root login), the agent connecting out to WHPanel (no inbound agent port), and automatic blocking of repeated failures.

## Consequences
Locking the VPN or the keys out means console access (the provider's VNC) to recover: documented in the runbook (ADR 18).

## Open questions
Whether to put nodes in a private network with a load balancer or keep each node public.
