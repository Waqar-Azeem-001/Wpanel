
# N9: Resource isolation and limits

Roadmap: production shared hosting must enforce CPU, RAM, disk, processes, I/O, bandwidth, database usage, email usage, inodes and runtime/process isolation, so that one compromised or noisy tenant does not
materially affect the others. *This is a production-security milestone.*

## The honest summary

**The design is in place and the logic around it is tested. Nothing has been proven on a real machine.** Isolation is only real when a tenant on a real node has tried to break out and failed, and when a noisy tenant has
burned its limits while a quiet one was measured. The tools to do both are here (`probe.py`, `loadtest.py`), their pass/fail judging is tested, but **no node has ever been probed or load-tested**. Until that is done and
passes, **do not put customers on a native node** (ADR 03 says the same; CloudLinux remains the stated fallback).

## What each resource is held to, and by what

| Resource | Held by | Built | Run on a real node |
|---|---|---|---|
| CPU | systemd slice `CPUQuota` per account | yes (N3, kept) | no |
| Memory | slice `MemoryMax`, soft limit `MemoryHigh` at 90% (slows before it kills), no swap | yes | no |
| Processes | slice `TasksMax`; PHP `pm.max_children` | yes | no |
| Disk I/O | slice `IOWeight` (a share against other accounts; **not** a fixed rate) | yes | no |
| Disk space | XFS project quota | yes | no |
| **Files and folders (inodes)** | the same project quota (`isoft`/`ihard`) | **new** | no |
| Database size | counted against the disk allowance in the usage figures; **not** itself capped by the database server | measured only | no |
| **Database connections** | `MAX_USER_CONNECTIONS 20` per database user | **new** | no |
| Mail | mailbox quotas, mailbox count and an hourly sending limit (N7) | yes | no |
| **Bandwidth** | **measured** from the web server's access log per day; the consequence is WHPanel's choice (below). **Not shaped in real time** (ADR 11) | **new** | no |
| Runtime and process isolation | own PHP service per account **running as that account's user (not root)**, `ProtectHome=tmpfs` + `BindPaths` (the account sees only its own home), `PrivateTmp`, `ProtectSystem=strict`, no capabilities, syscall filter, namespaces/realtime/SUID restricted, own socket with a per-user ACL | **new / hardened** | no |
| Seeing and touching others | folders: home is `root:apache 0710`, the account's folders `account:apache 2750` (setgid) with files `0640`, tmp `0700`; the node baseline (`hidepid=2`, link protection, ptrace restriction) | **new** | no |

**Known holes and limits**

* **Bandwidth is not enforced in real time**: a tenant can exceed it within a day. The consequence (notify or suspend) is applied when usage is next synced (daily).
* **Database size is not capped** by MariaDB; a tenant can fill the database disk. Its size counts toward the account's disk figure and triggers the warning, nothing stops the writes.
* **Database CPU/memory** are not isolated per tenant (one MariaDB per node, ADR 05): a heavy query affects everyone on that node. Only connections are limited.
* **Disk I/O** is a weight, so it only matters when the disk is contended; there is no hard rate.
* **Usage measuring walks the account's folder** (slow on huge accounts); reading the XFS quota report would be cheap but is not built.
* **The agent runs the daily measurement itself** (`Usage.record_day`); nothing calls it yet, so the usage history has gaps until the Go agent (or a timer) does.
* The PHP service uses `ProtectHome=tmpfs`; **PHP's `mail()` cannot reach a mail program** because web nodes do not run one (mail lives on separate nodes, N7). Sites need SMTP settings.
* `UMask=0027` and the setgid folder make uploads readable by the web server only; **any tool that expects world-readable files will break** and must be adapted.
* File commands (N6) must run as the account's own user (`run_as_account`, untested) or the sandbox is the only protection against links.

## What was added in N9

* **Hardened service units and folders** (see the table), a **richer slice** (accounting on, `MemoryHigh`, `IOWeight` from the plan), **file limits** in the quota. Plan keys: `inodes`, `io_weight` (with `memory_mb`, `processes`, `cpu_percent`, `disk_mb`, `databases`, `ftp_accounts`).
* **`USAGE_REPORT`**: current use, limits, daily history and live figures from the account's cgroup (CPU as a rate between two readings, memory, processes, I/O rate). The customer's Usage page (H11) now works for native accounts, and the daily usage
  sync stores real disk and bandwidth figures.
* **Warnings and bandwidth action** (`apps/hosting/limits.py`): one email at 90% and one when over, for disk space and bandwidth; each state is announced once, recovery resets it, bandwidth resets each month.
  Bandwidth over the allowance: `NATIVE_BANDWIDTH_OVER_ACTION=notify` (default) or `suspend` (offline until next month or staff reactivate; a suspension made this way is lifted automatically, one made by staff never is).
  Only native accounts are checked; cPanel accounts behave as before.
* **`ISOLATION_CHECK`** and the staff **Isolation check** page: the node baseline (cgroup v2 controllers, `hidepid`, XFS project quotas, link and ptrace protection) and, per account, drift from what WHPanel would write
  (a unit edited by hand, a missing sandbox line, limits different from the plan, a missing PHP or web config). A file that cannot be read counts as a failure. Run only against made-up files so far.
* **`probe.py`** (cross-tenant attack test) and **`loadtest.py`** (noisy-neighbour test) with tested judging. `RealAttempt` and `RealLoad` start the real attempts and load, **never run**; the database probe and four of the seven load scenarios
  (fork bomb, disk fill, inode flood, connection flood) have no real driver yet.

## Before a node takes customers (checklist)

1. Staff → Native nodes → **Isolation check** shows no problems.
2. Run the probe between two real test tenants as their real users: every attempt refused.
3. Run all seven load scenarios between two real tenants: the quiet one within 1.5 times its normal response time and error-free, the noisy one visibly held to its limit.
4. Keep the results with the date. Repeat after any change to the unit, slice or kernel.

## Tests

`apps/agent/tests/test_native_isolation.py` (36): hardened unit and pool; slice limits; file quota; folder owners/modes; database connections; read-only `/proc` and cgroup reads; folder measuring without following links; access-log bandwidth; usage
report, daily record, live cgroup and CPU rate; the customer Usage page; plan numbers reaching the node; the isolation check (good, five missing protections, unreadable file, drifted account); the staff page; the probe (pass, one successful attack, attempts that raise);
the load-test judge; the warnings once-only logic, bandwidth notify/suspend/auto-lift, staff suspensions left alone, month rollover; the usage sync; typed limit values.
