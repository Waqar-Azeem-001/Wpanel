# Native infrastructure: architecture decision records (N0)

Roadmap N0: *do not silently choose infrastructure technologies; evaluate twenty areas on security, isolation, operational complexity, performance,
compatibility, maintainability and recovery; produce architecture decision records before production native hosting.*

Each record lists the options considered, scores them against those seven criteria, states a recommendation and what it costs, and names
what is still open. **A record is `Proposed` until the business accepts it** (`Accepted`) or changes it (`Changed`); nothing below is built on a
proposal alone. Scores run from 1 (poor) to 5 (best for that criterion; for "operational simplicity" 5 means least work to run), are the author's
informal judgement, and are meant to be challenged. The text rests on general engineering knowledge, not on tests in this project.

Context that shaped the choices: a very small team; the existing servers are AlmaLinux-family with CloudLinux and cPanel, on rented virtual servers; the
customers' sites came from cPanel and depend on `.htaccess`; the roadmap wants no licensed control panel at the end; the agent runs as root on every node.

| # | Decision area | Status | Recommendation in one line |
|---|---|---|---|
| 01 | [Web server](001-web-server.md) | Accepted 2026-10-10 (chosen by the business, as recommended) | Apache 2 |
| 02 | [PHP process and runtime model](002-php-runtime.md) | Accepted 2026-10-10 (the business asked for all records to be completed; accepted as proposed) | PHP-FPM with one pool per tenant, each version in its own FPM master, pool settings written by the agent from the allowlist H7 already enforces (memor |
| 03 | [Tenant isolation](003-tenant-isolation.md) | Accepted 2026-10-10 (chosen by the business, as recommended) | Linux user per tenant plus systemd sandboxing and cgroup v2 limits, with a written hardening baseline and an automated cross-tenant attack test (a ten |
| 04 | [Linux account and storage model](004-account-model.md) | Accepted 2026-10-10 (the business asked for all records to be completed; accepted as proposed) | One system user and group per hosting account (numeric IDs allocated from a reserved range and recorded in WHPanel), a fixed layout under /srv/tenants |
| 05 | [Database topology](005-database-topology.md) | Accepted 2026-10-10 (the business asked for all records to be completed; accepted as proposed) | MariaDB LTS, one instance per node, with the database host a field of the node so databases can move to their own nodes without changing the customer  |
| 06 | [Mail topology and software](006-mail-platform.md) | Accepted 2026-10-10 as proposed: no native mail until N7; mail stays on cPanel nodes; N7 re-decides with real numbers | Do not build native mail first |
| 07 | [DNS architecture](007-dns-architecture.md) | Accepted 2026-10-10 (the business asked for all records to be completed; accepted as proposed) | PowerDNS Authoritative with a database backend and the HTTP API, on two nameservers separate from web nodes, managed by WHPanel directly (not through  |
| 08 | [SSL and ACME](008-ssl-acme.md) | Accepted 2026-10-10 (the business asked for all records to be completed; accepted as proposed) | Let's Encrypt with a second free authority as fallback, HTTP-01 per domain, DNS-01 for wildcards |
| 09 | [SFTP and FTP](009-sftp-ftp.md) | Accepted 2026-10-10 (the business asked for all records to be completed; accepted as proposed) | SFTP in an OpenSSH chroot for every tenant from the start (extra FTP logins map to chrooted SFTP users limited to a folder, as H8 already models) |
| 10 | [Backup storage and retention](010-backup-storage.md) | Accepted 2026-10-10 as proposed: restic to S3-compatible storage; the vendor is chosen when N8 starts (it does not block N1 to N7) | restic to S3-compatible object storage in a different provider or region from the node, with a repository per node, keys held by WHPanel's secret stor |
| 11 | [Resource enforcement](011-resource-enforcement.md) | Accepted 2026-10-10 (the business asked for all records to be completed; accepted as proposed) | cgroup v2 slices, XFS project quotas, PHP-FPM and MariaDB limits and nftables accounting, written by the agent from the plan's limits and verified by  |
| 12 | [Firewall and network model](012-firewall-network.md) | Accepted 2026-10-10 (the business asked for all records to be completed; accepted as proposed) | nftables default-deny with the provider's firewall as a second layer, SSH only on the VPN with keys (no password login, no root login), the agent conn |
| 13 | [Monitoring](013-monitoring.md) | Accepted 2026-10-10 (the business asked for all records to be completed; accepted as proposed) | Start with the agent's own heartbeat (the same states H13 shows) plus one external uptime service |
| 14 | [Operating-system and server updates](014-os-updates.md) | Accepted 2026-10-10 (the business asked for all records to be completed; accepted as proposed) | Automatic security updates, a monthly maintenance window, nodes marked 'maintenance' in WHPanel before a reboot (H13 already shows that state), and An |
| 15 | [Agent language and runtime](015-agent-language.md) | Accepted 2026-10-10 (chosen by the business, as recommended) | Go for the agent, with the command definitions (names, parameters, validation rules) written once in a shared specification that both WHPanel's tests  |
| 16 | [Node authentication and transport](016-node-authentication.md) | Accepted 2026-10-10 (the business asked for all records to be completed; accepted as proposed) | Agent-initiated mutual TLS, per-node certificates from an internal CA, one-time enrolment tokens (N2), signed command envelopes with nonce, expiry and |
| 17 | [Secret rotation](017-secret-rotation.md) | Accepted 2026-10-10 (the business asked for all records to be completed; accepted as proposed) | Node certificates valid 90 days and renewed automatically from day 60 |
| 18 | [Disaster recovery](018-disaster-recovery.md) | Accepted 2026-10-10 (the business asked for all records to be completed; accepted as proposed) | Backup-and-rebuild recovery with written targets and a yearly drill, WHPanel's database and secrets backed up separately from node backups, break-glas |
| 19 | [cPanel migration compatibility](019-cpanel-migration.md) | Accepted 2026-10-10 (the business asked for all records to be completed; accepted as proposed) | Build the importer around `pkgacct` backups, keep Apache for ` |
| 20 | [Supported Linux distributions](020-linux-distributions.md) | Accepted 2026-10-10 (chosen by the business, as recommended) | One distribution at launch: AlmaLinux 9 for all native nodes, so staff already know its tools and the migration path from cPanel (AlmaLinux-family) is |

## How a record becomes Accepted
1. The business reads the record, especially *Open questions*.
2. Its **Status** line is changed to `Accepted` with the date and who decided, and the same word is put in the table above.
3. Later phases (N1 onward) may rely only on Accepted records.

## Decisions that need the business, not just engineers
Web server (01), tenant isolation (03), mail (06), backup vendor (10), agent language (15) and Linux distribution (20): each trades cost, risk and
team skill, which only the business can weigh. The rest follow from those and can be accepted as proposed unless someone objects.
