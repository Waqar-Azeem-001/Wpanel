# ADR 13: Monitoring

**Status:** Proposed  
**Roadmap:** N0 (area 13 of 20)

## Context
N10: Healthy, Warning, Critical, Offline for nodes, services, certificates, backups and queues. H13 already shows what a provider can report.

## Options considered
| Option | Security | Isolation | Operational simplicity | Performance | Compatibility | Maintainability | Recovery |
|---|---|---|---|---|---|---|---|
| Agent heartbeat and metrics plus node_exporter scraped by Prometheus with Alertmanager, and an external uptime check | 4 | 4 | 3 | 4 | 4 | 4 | 4 |
| Netdata | 3 | 3 | 4 | 3 | 4 | 4 | 3 |
| Zabbix | 4 | 4 | 2 | 3 | 4 | 3 | 3 |
| Hosted monitoring only (UptimeRobot, Better Stack) | 4 | 5 | 5 | 4 | 3 | 5 | 5 |

- **Agent heartbeat and metrics plus node_exporter scraped by Prometheus with Alertmanager**: Standard, open source; WHPanel shows state from the agent and the external check, Grafana for staff.
- **Netdata**: Very quick to start, richer UI than needed, per-node dashboards.
- **Zabbix**: Complete and heavier.
- **Hosted monitoring only**: Good for 'is it up', not for resources and certificates.

## Recommendation
Start with the agent's own heartbeat (the same states H13 shows) plus one external uptime service; add Prometheus and node_exporter when there is more than a handful of nodes. States are computed in one place in WHPanel so the dashboard, alerts and self-healing agree.

## Consequences
No monitoring server is needed for the first nodes. Self-healing (N10) may only restart services on a short, written allow-list.

## Open questions
Alert channel (email, Telegram, WhatsApp) and who is on call.
