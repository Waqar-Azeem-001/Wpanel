# Disaster recovery runbook (native hosting)

**Status: written, never practised.** Targets are proposals for the business to accept or change (ADR 18): **data loss up to 24 hours (RPO), a lost web node back within 4 hours (RTO)**.
Do a drill at least once a year and after any big change; record what actually happened and how long it took. Until a drill has been done, treat these times as hopes, not promises.

## Before anything goes wrong (check monthly)
1. *Staff → Native nodes → Backup storage*: every node says **Ready**, shows a recent "restore test: passed", and the object-lock box is ticked.
2. `python manage.py backup_panel` ran last night and the file was copied off the server. `python manage.py backup_panel --verify <file>` reads it.
3. Offline and separate from the servers: the backup password of every repository, `CREDENTIALS_ENCRYPTION_KEY` (or `SECRET_KEY`), `AGENT_SIGNING_KEY`, the storage keys, DNS provider login, registrar login, the server provider's console login.
4. A fresh server image or install script exists and has been tried (N10 will provide it).

## A web node is lost (disk, hardware, hacked)
1. Decide: repair or replace. If hacked, treat it as replace; revoke it first (*Native nodes → Revoke*) so its certificate and token stop working.
2. Create a new server, install the agent, enrol it (new enrolment token). Add the same backup storage folder to it (*Backup storage → Apply to node*): **same bucket, same folder, same password** so it can read the old backups.
3. For each account that was on the lost node (the staff list shows them): assign the new node, **provision the website again** (this recreates user, PHP pool, virtual host and limits), then restore its newest good
   backup (*the customer's Backups screen, or staff*). Files and databases return; settings (SSL certificates, cron, SFTP logins) must be set up again: certificates re-issue once DNS points at the new node.
4. Point DNS at the new node's address (the zones are in PowerDNS: change the A records).
5. Tell customers what was lost: anything after the last backup.

## A mail node is lost
1. Revoke it; enrol a replacement with capability `mail`; apply the same backup storage.
2. For each affected account set `mail_server` to the new node (staff, admin screen) and run the mail setup (open its Email screen) then restore the **email** backup. The DKIM key is regenerated: publish the new DKIM
   record (done automatically with the setup). MX/SPF are updated automatically for the new address.
3. Mail sent to the old address in the meantime is lost unless the sender retries (most do for several days).
4. Check the new node's reputation (*Native nodes → Mail reputation → Check now*) before sending volume.

## WHPanel itself is lost
1. Build a new WHPanel server, restore the code and settings, put back `CREDENTIALS_ENCRYPTION_KEY`/`SECRET_KEY`, `AGENT_SIGNING_KEY`.
2. Restore the database from the newest `panel-*.bak.enc` (decrypt with the same key; SQLite: copy the decrypted file in place; PostgreSQL: `pg_restore` the decrypted dump).
3. Nodes keep running meanwhile; their agents reconnect when the address answers again. Jobs queued before the loss may be missing: check accounts that were being provisioned.

## The backup storage is lost or locked
Nodes keep serving; only new backups stop. Create new storage with a **new folder name**, apply it on each node, run a full backup of every account at once. Old backups are gone if the old storage cannot be recovered:
this is why a second copy at another provider is worth considering (not built).

## A node's backup keys are stolen
Revoke the node, rotate the storage keys at the provider, set the new keys in WHPanel and apply them to the remaining nodes. Object lock limits the damage to new writes.

## After every recovery
Write down what happened, how long each step took, and what the runbook got wrong. Fix this document.
