# ADR 03: Tenant isolation

**Status:** Accepted 2026-10-10 (chosen by the business, as recommended)  
**Roadmap:** N0 (area 3 of 20)

## Context
The roadmap's N9 says one compromised or noisy tenant must not materially affect others. The current servers run CloudLinux (CageFS and LVE), which is licensed per server. Isolation is the most important security property of shared hosting.

## Options considered
| Option | Security | Isolation | Operational simplicity | Performance | Compatibility | Maintainability | Recovery |
|---|---|---|---|---|---|---|---|
| Linux user per tenant + systemd sandboxing + cgroup v2 limits (mount namespaces, no shared /tmp, PrivateDevices, ProtectSystem) | 4 | 4 | 3 | 5 | 4 | 3 | 4 |
| CloudLinux (CageFS + LVE) on native nodes too | 5 | 5 | 5 | 4 | 5 | 4 | 4 |
| One container per tenant (Podman or Docker) | 4 | 4 | 2 | 3 | 3 | 2 | 3 |
| One microVM per tenant (Firecracker, Kata) | 5 | 5 | 1 | 2 | 2 | 1 | 3 |
| Plain Linux users only | 2 | 2 | 5 | 5 | 4 | 4 | 4 |

- **Linux user per tenant + systemd sandboxing + cgroup v2 limits**: Built from standard Linux parts, no licence; strong if configured carefully and tested; the team must own the hardening.
- **CloudLinux**: Proven for exactly this purpose; costs a licence per server and keeps a commercial dependency, which the roadmap wants to remove.
- **One container per tenant**: Good isolation but lower density, more moving parts, harder file and mail access, heavier for small plans.
- **One microVM per tenant**: Strongest isolation, far too heavy and complex for low-price shared hosting.
- **Plain Linux users only**: Not enough: tenants can read each other's temporary files and see processes.

## Recommendation
Linux user per tenant plus systemd sandboxing and cgroup v2 limits, with a written hardening baseline and an automated cross-tenant attack test (a tenant tries to read another's files, processes, sockets and temp space) that must pass before a node takes customers. Keep CloudLinux as the fallback if the test cannot be made to pass.

## Consequences
No licence cost; the hardening baseline and its test become permanent maintenance. Slightly weaker than CageFS until proven by the test.

## Open questions
Is the business willing to carry this hardening work, or should native nodes also use CloudLinux until the roadmap's later stages?
