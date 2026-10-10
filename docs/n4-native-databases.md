# N4: Native database platform

Goal: databases, users, credentials, privileges, quotas/usage, backups and restores on a native node, with **the customer's existing database screens working unchanged**
through the native provider.

## What is built and what is not

| Part | State |
|---|---|
| `NativeDatabases` (the same `DatabasesCapability` as cPanel/demo) so the H3 screens work through `NativeAdapter` | Built, tested (including the real customer page) |
| Node handlers for create/drop database, create/drop user, password change, grants, inventory with sizes, table list/columns/paged rows, backup, restore (`apps/agent/node/databases.py`) | Built in Python, tested against `FakeMariadb` |
| Waiting for the node (`agent.call`, long-polling) so screens get an answer now | Built, tested with an in-process agent |
| **`RealMariadb`** (the `mariadb` client, `mariadb-dump`, `zstd`) | **Written, never run against a real MariaDB.** Known gap: `grants_of` does not yet report which privileges each grant holds |
| Go agent | Written in Go (`agent/`) for every command of this stage, checked against the Python reference by golden scenarios and against live Django end to end on a pretend machine. **The real-machine paths have never run on a real node** (see agent/README.md) |
| Editing rows, free SQL (`run_query`, `insert_row`, `update_row`, `delete_row`) | **Not offered on native nodes.** They answer "not available yet". They would need a free-text command, which the agent deliberately does not have |
| Backup/restore screens for a single database | Node commands and adapter methods exist (`backup_database`, `restore_database`); no screen calls them until the backup platform (N8) |
| Per-account database disk usage in the usage page | Sizes are shown per database; not yet summed into the usage page |

## How it works

* Every database and user belongs to one account and its name starts with `<account>_`. The adapter adds the prefix; **the node refuses any name without it and any database or user not
  owned by the account**, so asking for another account's database fails whatever the request says.
* The plan's database count (`resource_limits.databases`: a number, `0` for none, blank for unlimited) is sent with `CREATE_WEBSITE`/`SET_LIMITS` and enforced **on the node**, where the databases are.
* Passwords travel in the signed command (never in the audit trail; replaced by `***` once the job ends), reach the engine **only as a `mysql_native_password` hash**, and never appear on a command line.
* Names are validated four times: by WHPanel, by the agent's catalogue, by the node handler and by the engine wrapper before they enter SQL.
* Browsing is read-only: typed database/table names, a page of at most 100 rows, values cut at 500 characters. **Row data is wiped from WHPanel's job record as soon as the screen has read it** (commands marked
  `ephemeral`: `DB_INVENTORY`, `DB_TABLES`, `DB_DESCRIBE`, `DB_BROWSE`); it is never in the audit trail.
* Restoring first takes a **safety backup** of the current contents (its id is returned), so a wrong restore can be undone. Only dumps that finished (a `.ok` marker) can be restored.

## Waiting for the node

Agents poll. For a screen that needs an answer now, `agent.call(server, command, params, wait=15)` queues the job and waits. The agent can ask the poll endpoint to hold the request open
(`POST /api/agent/v1/poll/?wait=20`, at most 25 s), so the pickup delay is small. If nothing picks the job up in time it is **cancelled** ("Nothing was changed"); if the node already took it, the caller
is told the change "may still be made". Each waiting request holds a web worker for up to `wait` seconds; run enough workers or threads before putting many customers on native nodes. WHPanel's read cache (20 s,
cleared after any change) keeps repeat views cheap.

## Tests

`apps/agent/tests/test_native_databases.py`: lifecycle, hashes only, limits on the node, cross-account refusal (8 commands), injection and bad values (10), read-only paging and truncation, backup/restore/safety
backup/unfinished dump, the screens through `control.perform` and the real page, limit message, other customers refused, no row data or passwords kept, timeouts (cancelled vs already started), long polling.
