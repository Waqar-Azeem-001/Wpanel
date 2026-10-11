# ADR 21: How the agent itself is updated

**Status:** Proposed 2026-10-12 (found missing while checking security non-negotiable "secure updates"; nothing is built on it)
**Roadmap:** N1 "safe upgrades", section 13 "secure updates"

## Context
ADR 14 covers the operating system. Nothing covers the agent program. Today a node's agent is a binary placed by the installer; WHPanel can only answer 426 "too old, upgrade it" and queue commands until it is
upgraded (`apps/agent/services.py`). The agent runs as root on every node and holds the key that verifies WHPanel's commands, so whoever can replace its binary owns the node, and a bad release
can take every node's control plane down at once.

## Options considered
| Option | Security | Isolation | Operational simplicity | Performance | Compatibility | Maintainability | Recovery |
|---|---|---|---|---|---|---|---|
| A. A signed RPM from the team's own package repository; nodes update with `dnf` (the same path as ADR 14), the agent only reports its version | 5 | 4 | 4 | 5 | 5 | 4 | 4 |
| B. The agent downloads a new binary itself, verifies a signature from a release key it has pinned, swaps it and restarts, keeping the old one for rollback | 4 | 4 | 3 | 5 | 4 | 3 | 5 |
| C. A configuration-management run (Ansible, ADR 14) copies the binary | 3 | 4 | 3 | 5 | 5 | 4 | 3 |

- **A**: one mechanism for everything on the node, signatures checked by the package manager, staged roll-outs by repository channel. Needs a repository to run.
- **B**: works with no repository and rolls back fast, but the agent needs a second key, a downloader and a restart path of its own: more code in the most privileged program.
- **C**: simple but gives Ansible's credentials the same power as the agent's key.

## Recommendation (to be decided by the business)
**A**, with these rules whichever option is chosen:
1. A release is signed by a key that is **not** on any production server or on WHPanel (offline or in a build service's secret store); nodes trust only that key.
2. A roll-out goes to **one node first**, then waits for its heartbeat to be healthy for a day before the rest; WHPanel shows each node's agent version (it already does) and refuses to queue a command that needs a newer version than the node reports (`min_agent_version` already exists).
3. The previous version stays installed and the update is reversible with one command.
4. A command-catalogue change that adds a command ships in an agent release **before** WHPanel starts queueing it; WHPanel never relies on a new command until the node reports the version that has it.
5. The key WHPanel signs commands with and the key that signs releases are different and never share a machine.

## Consequences
Until this is decided, **updating the agent on a node is a manual, per-node job** done by someone with root access, and a security fix to the agent reaches nodes only as fast as that person.

## Open questions
Which option; where the release key lives; who may publish a release; whether nodes may update unattended or only in the monthly maintenance window (ADR 14).
