# H13: Infrastructure dashboard

**Source:** next-generation roadmap, H13. Local only. Staff page: `Staff > Hosting > Infrastructure`
(`/staff/hosting/infrastructure/`, linked from the Servers page).

## What was built
- **A card per server (node)**, worst first: name, health (healthy, degraded, down, unreachable, offline, maintenance, unknown) with the
  reason, hostname, IP address, **region** (new field on a server, editable on the server form), provider (who rents the machine, such
  as Contabo) and server type, **hosting account count** against the server's capacity, software version, uptime, **CPU, memory,
  disk**, **load**, **network**, and the services: **web, database, mail, DNS, node agent, backups**. Summary tiles on top: nodes,
  healthy, need attention, in maintenance, unknown, hosting accounts.
- **Provider-neutral contract:** `NodeCapability.node_status()` (machine-level, read-only) returning `NodeStatus` and `NodeService`.
  A figure a server type cannot report is `None` and the page says "Not reported by this server type"; nothing is invented.
- **cPanel/WHM nodes** report only what WHM can: version, load average, and whether the web (httpd), database (mysql), mail (exim and
  dovecot) and DNS (named) services run. CPU, memory, disk, network, a node agent and backups show as not reported, as the roadmap
  asks. (Written against WHM's documented API; not yet run against a live server, like the rest of that adapter until H14.)
- **Demo nodes** report every figure from sample values that differ per machine and are steady between page loads (one in three runs
  hotter with a service in trouble). `demo_hosting_data` now also adds two sample machines (one in maintenance); `--remove` deletes them.
- **Health rules:** staff status first (offline, maintenance), then a failed connection (unreachable, with the reason), then a service
  down, then a degraded service or any of CPU, memory or disk at 90% or more (degraded), then healthy. A server type that reports
  nothing is "unknown". Servers in maintenance or offline are not asked for figures.
- A server that cannot be reached or whose saved details are broken becomes a red card; it never takes the page down.
- Needs the view-hosting permission (support, technical, manager, admin); the page only reads.

## Verified
23 tests (`test_infrastructure.py`): who can report, demo figures in bounds and steady, WHM reporting only its own fields, service
states (down, degraded, unavailable), both load shapes, unreachable errors, the health rules, cards for cPanel, broken, manual and
maintenance servers, the page with all nodes, region, provider and services, ordering, the not-reported wording, permissions, region
saved from the server form, sample removal. Hosting, core and products suites pass.

## Deferred
Live checks of the rented machine through Contabo (instance state, region lookup), history graphs per node, alerts when a node turns
red, and a real node agent (N track). The WHM service and load parsing needs a check against a real server.
