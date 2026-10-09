# ADR 16: Node authentication and transport

**Status:** Proposed  
**Roadmap:** N0 (area 16 of 20)

## Context
N1 and N2 require strong node identity, encrypted and authenticated transport, replay protection, authorisation, idempotency and correlation IDs.

## Options considered
| Option | Security | Isolation | Operational simplicity | Performance | Compatibility | Maintainability | Recovery |
|---|---|---|---|---|---|---|---|
| Mutual TLS with a certificate per node from WHPanel's own certificate authority; the agent connects out and fetches jobs; every command is a signed, numbered, time-limited envelope with an idempotency key | 5 | 5 | 3 | 4 | 4 | 3 | 4 |
| WHPanel calls the agent's HTTPS API with a bearer token | 3 | 3 | 4 | 4 | 4 | 4 | 3 |
| SSH with forced commands | 3 | 3 | 3 | 3 | 3 | 2 | 3 |
| WireGuard network plus a token | 4 | 4 | 3 | 4 | 4 | 3 | 4 |

- **Mutual TLS with a certificate per node from WHPanel's own certificate authority; the agent connects out and fetches jobs; every command is a signed**: No inbound agent port, a stolen token alone is useless, a replayed command is rejected, a lost reply can be retried safely.
- **WHPanel calls the agent's HTTPS API with a bearer token**: Simple; an exposed port and a long-lived secret.
- **SSH with forced commands**: Reuses SSH but the command interface is awkward and easily widened into a shell.
- **WireGuard network plus a token**: Good transport layer; still needs per-command authorisation.

## Recommendation
Agent-initiated mutual TLS, per-node certificates from an internal CA, one-time enrolment tokens (N2), signed command envelopes with nonce, expiry and idempotency key, a strict allow-list of command names with typed parameters, and a correlation ID carried from the request to the audit trail.

## Consequences
WHPanel needs a small internal CA and a job queue the agent polls. Commands are slightly delayed (seconds) compared with direct calls.

## Open questions
Long-polling or WebSocket for near-instant jobs.
