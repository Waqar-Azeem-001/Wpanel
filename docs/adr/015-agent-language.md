# ADR 15: Agent language and runtime

**Status:** Accepted 2026-10-10 (chosen by the business, as recommended)  
**Roadmap:** N0 (area 15 of 20)

## Context
The WH Agent runs as root on every node and executes the allow-listed commands. Its safety matters more than anything else in N1.

## Options considered
| Option | Security | Isolation | Operational simplicity | Performance | Compatibility | Maintainability | Recovery |
|---|---|---|---|---|---|---|---|
| Go: one static binary, strong standard library for TLS and HTTP, easy cross-compilation, good systemd and cgroup libraries | 4 | 4 | 5 | 5 | 4 | 4 | 4 |
| Python (the same language as WHPanel) | 3 | 3 | 3 | 3 | 4 | 5 | 3 |
| Rust | 5 | 4 | 5 | 5 | 3 | 2 | 4 |
| Shell scripts driven over SSH | 1 | 1 | 2 | 3 | 3 | 1 | 2 |

- **Go**: Nothing to install on the node but the binary; memory-safe; a second language beside Django.
- **Python**: Fastest to write and shares code and tests with WHPanel; needs an interpreter and dependencies on every node, a larger attack surface.
- **Rust**: Safest and fastest, steepest learning curve and slowest development.
- **Shell scripts driven over SSH**: Rejected: it would be the arbitrary remote shell the roadmap forbids.

## Recommendation
Go for the agent, with the command definitions (names, parameters, validation rules) written once in a shared specification that both WHPanel's tests and the agent's tests read, so the two cannot drift. Python remains the choice for WHPanel.

## Consequences
The team needs some Go skill, or the agent is built with outside help and reviewed carefully. Static binaries make safe upgrades and rollbacks simple (N1).

## Open questions
Whether the team is willing to maintain a Go codebase; if not, Python with a locked-down virtual environment is the fallback.
