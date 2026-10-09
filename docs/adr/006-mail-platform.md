# ADR 06: Mail topology and software

**Status:** Proposed  
**Roadmap:** N0 (area 6 of 20)

## Context
N7 says mail is the highest-complexity subsystem: reputation, spam, abuse and deliverability can sink the whole business. It also asks whether mail should be separate from web nodes.

## Options considered
| Option | Security | Isolation | Operational simplicity | Performance | Compatibility | Maintainability | Recovery |
|---|---|---|---|---|---|---|---|
| Separate mail nodes: Postfix + Dovecot + Rspamd (DKIM, spam), Roundcube webmail, virtual users managed by the agent | 4 | 5 | 2 | 4 | 4 | 3 | 4 |
| Mail on every web node (as cPanel does) | 3 | 2 | 3 | 3 | 5 | 3 | 3 |
| Bundled stack (Mailcow, Mailu) per node | 3 | 3 | 4 | 3 | 3 | 3 | 3 |
| Hosted mail provider (outbound relay or full hosting) | 4 | 5 | 5 | 4 | 3 | 5 | 4 |

- **Separate mail nodes**: A web-node compromise or a spammer does not spoil the mail IP reputation; mail can be scaled and recovered on its own.
- **Mail on every web node**: Simple, matches today; one abuser harms the node's reputation for everyone on it.
- **Bundled stack**: Quick start, but Docker-based, built for one organisation, not thousands of tenants.
- **Hosted mail provider**: Least work and best deliverability; recurring cost per mailbox or per message and a third-party dependency.

## Recommendation
Do not build native mail first. Keep mail on cPanel nodes through the dual-provider period (M0), then build separate mail nodes with Postfix, Dovecot and Rspamd. Until then, a relay for outbound mail may be used to protect reputation. Revisit with real numbers (mailboxes, volume) before N7.

## Consequences
Native web hosting can launch (N3) without mail, which keeps the first milestone small. Customers on native nodes keep their mailboxes on the existing mail server meanwhile (MX unchanged).

## Open questions
Does the business want to run mail itself at all, or use a hosted provider long term? This is a business decision as much as a technical one.
