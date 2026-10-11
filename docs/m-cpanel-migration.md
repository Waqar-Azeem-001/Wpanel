# M0-M4: migration and cPanel retirement

Roadmap: operate cPanel and native side by side (M0), make new orders native-first (M1), move accounts with discovery, pre-flight, files, databases, mail, validation, cutover and monitoring (M2), show a migration dashboard (M3), and retire cPanel only when every condition is proven (M4). ADR 19 chose "import from the account's own `pkgacct` backup".

## What is built and what is not

| Part | State |
|---|---|
| **M0** both providers at once: a server is `WHM API` or `Native`, the customer screens are the same | Already true since N3; nothing new needed |
| **M1** native-first: `NEW_ACCOUNTS_NATIVE_ONLY=true` refuses to create a new account on cPanel (or on a product that maps no native node) | Built, tested. **Off by default**: turn it on when native hosting is trusted |
| **M2** the importer on the node (`apps/agent/node/cpanel_import.py`, Go twin `agent/internal/handlers/migrate.go`): reads a `pkgacct` archive safely, reports what it holds, copies website files, imports databases (users keep their password hashes) and mailboxes (keep their hashes, messages, forwarders), checks the result | Built in Python and Go; the two give identical answers on a synthetic archive (golden test). **Never run on a real cPanel archive** |
| 4 new agent commands: `MIGRATE_ANALYZE`, `MIGRATE_IMPORT` (files / databases / mail), `MIGRATE_VALIDATE`, `MIGRATE_DISCARD` | Built, tested, in the catalogue (80 commands now) |
| **M2** the control plane (`apps/migration`): one `Migration` per account, stages, history, approvals, cutover, rollback window, completion | Built, tested end to end with pretend nodes |
| **M3** staff dashboard: totals (total, migrated, in progress, pending, failed, manual review), per-account stage tracker, history, actions | Built, tested |
| **M4** retirement checklist: measured conditions plus named sign-offs, nothing ticked for things only a real node can show | Built, tested. **Nothing is removed from the code**: the cPanel provider, "open cPanel" link and WHM credentials stay until every line is green and a person removes them |
| Making the cPanel backup and copying it to the node | **Manual** (two commands, `docs/runbooks/cpanel-migration.md`). The WHM API has no way to make a `pkgacct` archive, and the cPanel server is not an agent node |
| Cron jobs | **Not imported.** They are listed in the report to be recreated by hand (the platform has no scheduled-jobs feature yet) |
| Addon, sub and parked domains | **Blocked for a person**: a native account serves one domain. Give each its own account, or leave it out |
| Certificates | Not copied; a new one is issued after the cutover |
| DNS | Changed at cutover **only when WHPanel hosts the zone** (the A record is set to the node, and the old value saved for rollback). Otherwise the new address is shown for someone to set. cPanel's zone file is listed in the report but not imported |
| Mail on a separate mail node | Needs the same archive copied to the mail node too; mailbox passwords keep working only where the archive holds a usable hash |
| Retiring (deleting) the cPanel account after the window | **Not automated**: staff do it by hand once they are satisfied |

## The path of one account

```
awaiting_archive -> analysing -> ready -> importing -> validated -> window -> complete
                        |           |                      |           |
                  manual_review   failed                 failed     rolled_back
```

1. **Start** (staff): pick a live cPanel account, a native web node, optionally a mail node, the rollback window. Nothing changes anywhere.
2. **Backup and transfer** (operator): run `pkgacct` on the cPanel server and copy the archive to the node's `/var/lib/whp/stage/migrations/`.
3. **Check backup**: the node reads it (`MIGRATE_ANALYZE`) and WHPanel stores the report. Problems are *blockers* (stop for a person) or *warnings* (read and accept). A checksum can be given so a damaged copy is refused.
4. **Approve and import** (staff): `CREATE_WEBSITE` on the node with the plan's limits and the mapped PHP version, then files, databases, mail domain and mailboxes, then `MIGRATE_VALIDATE`. Each step waits for the node's answer; a failure stops the chain and **Retry** carries on from that step.
5. **Check the site** (optional button): WHPanel asks the node's address for the site with the right Host header.
6. **Cut over** (staff, with confirmation): the account is pointed at the new node (and mail node); DNS is switched when WHPanel hosts it. The rollback window opens.
7. **Roll back** (any time in the window): the account points at cPanel again and DNS is put back. The cPanel account was never touched, so this is switching pointers, not restoring data. **Changes made on the new node after the cutover are not copied back.**
8. **Complete**: after the window (or earlier when forced) the staged archive is deleted from the nodes. The cPanel account is left for staff.

## Safety rules in the importer

* The archive is never trusted: absolute paths and `..` are refused, links and devices are skipped, nothing from it is executed.
* SQL only goes to this account's own databases (name must start with `<account>_`); a database that exists is **never replaced**; a half-loaded database is dropped.
* An existing file is kept unless a replace is asked for; nothing is written through a link already on the node.
* Files end up owned by the account with group `apache` (the agent runs as root, so it hands them over); mail by the mail user.
* The same step run twice gives the same result.

## Not proven (honest list)

* The archive layout follows the documented `pkgacct` format and **has not been tried against a real archive**; cPanel versions differ. Try the report on a few real archives before trusting it.
* MariaDB loading of large dumps, restic, certbot, `chown`/`chmod` on a real node: never run.
* The mail import relies on the N7 mail platform, which has never run for real (including file ownership of new mailboxes, which N7 does not set either).
* `userdata` is read with a small purpose-built reader, not a YAML library: unusual layouts will show up as "needs a person", not as a wrong import.

See the runbook: `docs/runbooks/cpanel-migration.md`.
