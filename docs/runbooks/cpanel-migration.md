# Runbook: moving one cPanel account to a native node

**Status:** written, never followed on real servers. Do the first few on the quietest accounts, with the customer told, and fix this page with what you learn.

## Before you start
* The target native node is enrolled, healthy, has the "web" capability and room (Staff > Providers > Native nodes). For mail, a mail node with the "mail" capability.
* The account is live on a cPanel server in WHPanel (Staff > Providers > cPanel migration > Start). Its username is 3-16 lowercase letters/digits.
* Lower the DNS TTL of the domain to 300 seconds at least a day before the cutover (WHPanel sets 300 itself only when it hosts the zone).
* Tell the customer when, and that email sent during the switch may arrive late.

## 1. Make the backup (on the cPanel server, as root)
```
/scripts/pkgacct USERNAME /root/migrate
sha256sum /root/migrate/cpmove-USERNAME.tar.gz
```
The screen shows these lines for the account. Keep the checksum; it can be entered when starting so a damaged copy is refused.

## 2. Copy it to the node
```
rsync -av /root/migrate/cpmove-USERNAME.tar.gz root@NODE:/var/lib/whp/stage/migrations/
```
For mail, copy the same file to the mail node's `/var/lib/whp/stage/migrations/`. The file holds password hashes: only root should be able to read it (`chmod 600`), and it is deleted when the migration completes.

## 3. In WHPanel
1. **Check backup.** Read the report. A *needs a person* line (addon domains, wrong account, missing files) stops here: decide and start again, or cancel.
2. Read the warnings (cron jobs that need a person, old PHP, certificates, links ignored), tick that you accept them, **Approve and import**.
3. Wait for *Imported and checked*. If a step failed, read the message, fix the cause, **Retry**.
4. Test the site before DNS changes: **Check that the site answers**, and (better) open it with a hosts-file entry pointing the domain at the node.
5. Plain php cron jobs are recreated by the import. Open the account's Cron screen on the new node, compare with the report's list, and recreate by hand anything that was skipped (each skipped line says why; shell commands cannot be scheduled on native nodes).
6. **Cut over.** Confirm. If the DNS note says WHPanel does not host the zone, set the A record (and www) to the address shown.
7. Watch the site, the mail and the node's Monitoring page for the rollback window.

## Rolling back
Press **Roll back** inside the window. The account points at cPanel again and DNS (if WHPanel hosts it) is restored. If DNS is elsewhere, set it back by hand. Anything the customer changed on the new node since the cutover is **not** copied back: tell them, or copy it across first.

## Finishing
After the window press **Complete** (the staged backups are deleted). Retire the cPanel account by hand (suspend first, delete after a further grace period). When no live account is left on cPanel, go to **Is cPanel ready to retire?** and complete the sign-offs.
