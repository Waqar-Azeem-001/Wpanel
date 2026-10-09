# N3: Native website provisioning

Goal: WHPanel provisions and manages a website on a native node **without cPanel**.

## What is built and what is not (read this first)

| Part | State |
|---|---|
| WHPanel side: `NativeAdapter`, queued signed commands, outcome hook, placement, welcome email without a password | Built and tested |
| Node side: the logic that makes, suspends, changes and deletes a hosting account (`apps/agent/node/`) | Built in **Python** and tested against `FakeSystem` (a pretend Linux machine) |
| **Running on a real AlmaLinux node** | **Never done.** `RealSystem` and every generated config file (Apache, PHP-FPM, systemd, XFS quota commands) are untested on real hardware. Expect fixes on first contact: package paths, SELinux contexts, the Apache include line, the XFS project setup |
| Go agent | Still an uncompiled skeleton. It does **not** implement these handlers; the Python code in `apps/agent/node/` is the specification to port |
| SSL, DNS, mail, databases, backups, file transfer on the node | Not part of N3 (N4+ and later) |

So the milestone "WHPanel can provision a website on a native node" is met **in the control plane and in simulation**, not yet on a server.

## The path of one order

1. `request_hosting` → `complete_provisioning` (staff, or the paid-order flow) calls `NativeAdapter.create_account`.
2. The adapter queues `CREATE_WEBSITE` (account, domain, PHP 8.3, plus the plan's `disk_mb`, `memory_mb`, `processes`, `cpu_percent` if the product defines them) and returns
   `pending`. The account stays **Pending**. Pressing the button again while the job is open queues nothing new; a retry after a failure is a new job.
3. The node's agent fetches the signed command and carries it out. WHPanel hears the result through `AgentJob.subject = "hosting:<id>"`:
   success → **Active** + welcome email (username only; there is no separate control panel login); failure → **Failed** with the node's message, retry allowed.
4. Suspend, unsuspend, terminate (always with a final archive), backup and plan change queue `SUSPEND_WEBSITE`, `UNSUSPEND_WEBSITE`, `DELETE_WEBSITE`, `CREATE_BACKUP`, `SET_LIMITS`.
   These are recorded in WHPanel straight away; **if the node later reports failure, the account's last error and the audit trail show it, but its status is not rolled back.**
   (Making every operation wait for the node needs a status model for "changing"; not done.)

## What a node does for CREATE_WEBSITE

Per account (ADR 01, 02, 03, 11):

* a Linux user and group, home `/home/<account>` (mode 0711), no login shell, folders `sites/<domain>/public_html`, `logs`, `tmp`;
* an XFS project quota for the plan's disk size;
* **its own PHP-FPM master** (`whp-php-<account>.service`, binary of the chosen version) running as that user, with `open_basedir`, `disable_functions`, `NoNewPrivileges`,
  `ProtectSystem`, in **its own systemd slice** (`MemoryMax`, `CPUQuota`, `TasksMax`) so the limits really bound the PHP processes;
* an Apache virtual host in `/etc/httpd/conf.d/whp/` handing `.php` to that account's socket, `AllowOverride All` (customers' `.htaccess` keeps working), no directory listing;
* `httpd -t` before every reload, so a bad config cannot take every site down.

Safe to repeat (a state file records what is done), and if a step fails part-way the earlier steps are **undone**. Deleting makes the archive first; if the archive fails nothing is removed.
Every program is run from an argument list, never a shell; paths must be inside `/home`, `/etc`, `/var/lib|log|backups/whp`, `/run/whp`; every value is validated again on the node.

Prerequisites on a node that the installer (N2/N10) must still provide: Apache with `IncludeOptional conf.d/whp/*.conf` and `mod_proxy_fcgi`; `/var/www/whp-suspended/index.html`;
Remi PHP packages for each offered version; `/home` on XFS mounted with `prjquota`; `/etc/whp/php`, `/var/lib/whp/accounts`, `/var/backups/whp` folders.

## Choosing a node

`apps/agent/placement.pick_node(region="", among=None)`: enrolled, active, `web` capability, healthy, below its capacity; the least full wins. When a product is mapped to **several native
nodes** and no server is given, `request_hosting` uses it automatically. Mapping a product to one server, or to cPanel servers, behaves as before.

## Commands added to the allow-list

`SET_LIMITS`, `SUSPEND_WEBSITE`, `UNSUSPEND_WEBSITE`; `CREATE_WEBSITE` gained optional limits. `adapter.get_usage` reports "not reported" (no usage command yet), never zero.

## Tests

`apps/agent/tests/test_native_website.py`: every generated file, quota and reload order, defaults, argument-list-only commands, repeat safety, conflicts, injection attempts, rollback after a mid-way
failure, suspend/PHP/limits/delete, archive-first and archive-failure, path refusals, the order-to-active flow with email, double-press, failure and retry, later-command failure, placement rules.
