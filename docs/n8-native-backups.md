
# N8: Native backup platform

Roadmap: native node → encrypted backup → remote backup storage, for websites, databases, email where hosted and recovery configuration; with encryption, retention, integrity checks, restore tests, access controls
and disaster-recovery procedures.

## What is built and what is not

| Part | State |
|---|---|
| The customer's Backups screen through `NativeBackups` (the same `BackupsCapability`): start a backup, list, verify, restore with a safety copy, delete, schedule | Built, tested through `control.perform` |
| Node handlers: configure storage, back up (files / databases / email / full), verify, restore, forget, test restore, back up the node's own configuration | Built in Python, tested on real temporary folders |
| Encryption, wrong-key refusal, tamper detection, retention, safety copy, restore test | **Tested for real** with `FakeRestic`: AES-256-GCM, key from the repository password (scrypt), a SHA-256 list of every file, tags, checked on decrypt |
| **`RealRestic` (the `restic` program, S3-compatible bucket)** | **Never run.** Its output parsing (snapshot ids, sizes) and the way it checks a restored snapshot are untested; expect fixes |
| Backup storage vendor | **Not chosen.** Everything is vendor-neutral (endpoint, bucket, keys). You choose and create the bucket (ADR 10 open question) |
| Object lock / append-only so a taken-over node cannot delete its own backups | **Not enforceable from here.** Staff must set it at the storage provider and tick a box; the screen warns until they do. Without it a compromised node holding the keys can destroy its backups |
| Scheduled backups (daily 02:30) and the monthly restore test (1st of the month, 04:00) | Built as Celery beat tasks; **they only run if Celery beat runs** |
| WHPanel's own database backup (`manage.py backup_panel`) | Built, SQLite tested (encrypted, verifiable, pruned). PostgreSQL path (`pg_dump`) **untested**. Copying the file off the server is not done by the command |
| Restoring a whole node from backup (rebuild) | A runbook (below), **never practised**. The node-configuration snapshot is for reference and rebuilding; there is no automatic rebuild |
| Restore of configuration (virtual hosts, units, state) | **Not automatic.** A "full" backup holds it for inspection; a restore puts back files and databases only |
| Go agent | Has none of this |

## How it works

* **Repository per node**: a folder (prefix) in an S3-compatible bucket, one encryption password per repository. WHPanel keeps the keys encrypted, staff can replace but never read them, the password cannot be
  changed once set (existing backups would become unreadable). **Lose the password and every backup is gone: keep an offline copy.**
* **A backup** copies the account's data to a staging folder on the node, **skipping every symbolic link** (a link to `/etc/shadow` must never end up in a customer's backup), stores it as one encrypted snapshot tagged
  with the account and kind, removes the staging copy, and reports the snapshot id and size. "Full" = files + databases + settings of the account on the web node, plus the mail node's part when the account uses mail.
* **WHPanel's register** (`NativeBackup`) is the list customers see, so backups stay known if a node is lost. A backup shows as *running* until the node reports; a failed one stays listed with the reason.
* **Verify** decrypts and compares every file with the list made at backup time. **A restore is only done after a fresh check passes.** It first takes a safety copy of what it replaces (listed as "before restore", and
  restorable), then replaces exactly what the backup holds. It runs in the background: the screen cannot show progress, and a failure is recorded on the account (last error) and in the audit trail.
* **Access**: every snapshot command refuses a snapshot that does not belong to the asking account; staff need the hosting-management permission to change storage; the keys never appear in the audit trail or in finished jobs.
* **Retention**: the plan keeps the newest `resource_limits.backups` backups (default 7); older ones are forgotten (queued); safety copies from restores are not counted.
* **Restore tests**: monthly, a random recent backup of a few accounts is restored into a scratch folder on the node and compared; nothing live is touched. A failure goes to the audit trail and the staff Backup storage
  page shows "FAILED". A node that has never been tested shows "never restore-tested".
* **Heavy accounts**: backing up copies data to staging first, so the node needs free disk space about the size of the largest account. Streaming straight into the repository would remove that need; not built.

## Settings and operation

Staff: *Native nodes → Backup storage* → enter the endpoint, bucket, folder and keys, **Apply to node**, then tick the object-lock box once it is true. Run Celery beat. Run `python manage.py backup_panel` nightly
and copy `PANEL_BACKUP_DIR` away. Keep `CREDENTIALS_ENCRYPTION_KEY` (or `SECRET_KEY`), `AGENT_SIGNING_KEY` and every repository password somewhere offline: none of them is inside the backups.

## Disaster recovery

`docs/runbooks/disaster-recovery.md` has the proposed targets (RPO 24 hours, RTO 4 hours) and a step-by-step procedure for each loss. **None of it has been practised**; the first real drill is itself a deliverable (ADR 18).

## Tests

`apps/agent/tests/test_native_backups.py` (24): keys stored encrypted and reaching the node only as a root-only file; settings checks and the fixed password; a refusing node; encryption at rest; wrong key and tampering; the
customer's flow (run, list, verify); full backup and restore with the safety copy and undoing a mistake; a damaged backup is never restored; links never backed up; another account's snapshot refused (4 commands);
no storage → clear failure; retention; deletion from the storage; schedules; monthly restore test passing and catching a damaged backup; node configuration without keys; failed backup shown to staff; staff page never shows
secrets; typed commands; the panel's own database backup, verification and pruning.
