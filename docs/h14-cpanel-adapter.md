# H14: Complete transitional cPanel adapter (parts a and b)

**Source:** next-generation roadmap, H14 ("no cPanel-specific logic should leak into generic hosting services or customer UI").
Local only. **Status: written and tested against a fake WHM; NOT verified against a real cPanel/WHM server.**

## How it works
- A customer-level call runs through WHM API 1's `uapi_cpanel` function with the reseller token: `cpanel.user` (the account),
  `cpanel.module`, `cpanel.function` plus the function's own parameters; the answer is read from `data.uapi`. No customer password is
  ever used, and the token never leaves the adapter. Passwords travel in a POST body, not a URL.
- DNS uses WHM's zone functions (`dumpzone`, `addzonerecord`, `editzonerecord`, `removezonerecord`).
- All cPanel knowledge lives in `apps/hosting/adapters/cpanel_*.py`; the screens, gateway and audit are the same as for the demo
  provider. The DNS record rules were moved out of the demo provider into `dns_rules.py` so both providers use the same checks.

## What is implemented
| Tool | cPanel functions | Not offered (clear "not available on cPanel servers yet") |
|---|---|---|
| DNS | dumpzone, addzonerecord, editzonerecord, removezonerecord | - |
| Databases | Mysql: get_restrictions, list_databases, list_users, create/delete database and user, set_password, set/revoke privileges, get_privileges_on_database; plan limit from accountsummary | tables, rows, SQL, phpMyAdmin session |
| Email | Email: list_pops_with_disk, add_pop, delete_pop, passwd_pop, edit_pop_quota, list/add/delete forwarders, auto responders | reading and writing mail in WHPanel, webmail, spam and routing settings |
| PHP | LangPHP: installed and per-domain versions, set version, basic ini directives (memory, upload, run time, errors) | extensions (the server owner picks them on cPanel) |
| Files (`files`) | Fileman list_files, get_file_content, save_file_content (UAPI); mkdir and fileop copy, move, rename, chmod, trash, unlink, compress, extract (API 2). Paths are made relative to the account's home folder first | binary uploads (use FTP/SFTP), trash view, name search |
| Domains (`domains`) | DomainInfo domains_data; API 2 AddonDomain, SubDomain, Park; Mime add, list and delete redirects; limits from accountsummary | nameserver list |
| SSL (`ssl`) | SSL installed_hosts, start_autossl_check, delete_ssl | per-domain auto-renew switch and Always-use-HTTPS (AutoSSL renews by itself) |
| Cron (`cron`) | API 2 Cron listcron, add_line, remove_line (a switched-off job is kept as a commented line); same schedule and command rules as everywhere (`cron_rules.py`) | Run now |
| FTP (`ftp`) | Ftp list_ftp (only the customer's own `sub` logins), add_ftp, passwd, delete_ftp; each login limited to a folder in the home folder | SFTP port is not reported |
| Logs (`logs`) | Stats get_site_errors (the website's recent errors, tailed and filtered) | the access log (cPanel keeps it outside the account's folder), clearing a log |
| Backups (`backups`) | Backup list_backups, fullbackup_to_homedir (full account only) | testing a backup, restore, delete, schedules, off-server copy: use Open cPanel |

Safety rules in every tool: the domain, zone, database or user named must belong to **this account** (checked against `DomainInfo` /
`Mysql` listings first, so typing another customer's name reaches nothing); names are plain ASCII; values must come from the plan's
lists; a stale DNS line is refused rather than guessed.

## Everything is OFF until staff turn it on
Because the field names cPanel returns differ between versions and none of this has met a live server, **no cPanel customer tool is
offered by default**. Turn tools on one at a time in the environment:

```
HOSTING_CPANEL_CAPABILITIES=files,domains,dns,ssl,databases,email,php,cron,backups
```

Values not listed are not shown in the customer's menu; account actions and usage keep working as before. The gateway, permissions,
audit and error mapping apply as for any provider; an operation cPanel cannot do answers with a plain 501-style message.

## Staging checklist (do before enabling for customers)
1. Point a staging server at a test reseller token; create a test account; set `HOSTING_CPANEL_CAPABILITIES=dns` only.
2. DNS: list, add each type, edit, delete; check the zone in WHM. Confirm `dumpzone` returns records under `data.zone[0].record`.
3. Databases: create, grant, change password, delete; confirm the prefix and that privileges names are accepted.
4. Email: create, quota, password, forwarder, auto reply; confirm `list_pops_with_disk` field names (`diskused`, `diskquota`).
5. PHP: confirm `php_get_vhost_versions` and the `php_ini_*_basic_directives` answer shapes (the least certain part).
6. Fix any field-name differences in the matching `cpanel_*.py` file, add the real shape to `test_cpanel_adapter.py`, then enable.

## Verified
50 tests (`test_cpanel_adapter.py`) against a fake WHM: tools off by default and enabled one by one; the UAPI request shape; error
and odd-answer handling; every DNS type read and written with the right WHM fields, bad records refused before sending, stale lines
refused, foreign and subdomain zones refused; database names prefixed and ownership checked; passwords only in POST bodies; privilege
validation; mailbox, forwarder and auto-reply parameters; foreign mail domains refused; PHP version mapping, allowed values only;
unsupported operations clearly refused; the gateway's 501 mapping. These tests prove our side of the conversation, **not** that cPanel
answers like the fake.

## Part b (files, domains, SSL, cron, backups)
Added in the same style: a fake WHM that also answers API 2 (`cpanelresult`), further tests (`test_cpanel_adapter_b.py`) covering path
safety (nothing outside the home folder can be named), each file operation, each kind of domain, redirect address rules, certificate status
(active, ending soon, expired, none), cron rules and switched-off jobs, backup listing, API 2 failures, and a guard test that no two cPanel
mixins share a method name (one did, and silently broke PHP until a test caught it).

## Staging checklist additions
7. Files: list a folder, edit a text file, create a folder, rename, move, copy, chmod, zip and extract; confirm `Fileman::list_files` fields
   (`file`, `type`, `size`, `mtime`, `nicemode`) and the API 2 `fileop` parameter names (`op`, `sourcefiles`, `destfiles`, `metadata`).
8. Domains: add an addon, a subdomain and an alias, remove them, set and remove a redirect (`Mime` parameters are the least certain).
9. SSL: confirm `SSL::installed_hosts` returns `servername` and `certificate.not_after`; try `start_autossl_check` and `delete_ssl`.
10. Cron: confirm `Cron::listcron` returns `linekey` and `count`, and that `remove_line` takes `line`.
11. Backups: confirm `Backup::list_backups` file names look like `backup-M.D.YYYY_HH-MM-SS_user.tar.gz`.

## Still to do in H14
The real-server verification above, and a pass for the cPanel versions actually in use. Nothing in this slice has met a live server.


## Checked against the live server (9 Oct 2026, read-only)
With the tools switched on for the first real account on the live WHM (11.138), these **reads** were run and answered correctly: domains and
limits, home folder and file listing, MySQL limits and lists, mailbox limits and lists, forwarders, DNS zone, SSL status, PHP versions and
settings, cron, backup list, usage, FTP list, error log, and **Open cPanel** (WHM made a single-use link on the server's own host name with a
short expiry; the link was not followed). Two real differences were found and fixed from it: cPanel returns certificate details with flat dotted
keys (`issuer.commonName`...) and a self-signed certificate must be called that; and the account's home folder holds server-internal folders
(`.cagefs`, `.cl.selector`...) which are now hidden.

**Still not run live (they change things, so they were not tried):** creating or deleting a database, mailbox, forwarder, FTP login, domain, DNS
record, cron job or file; changing PHP, a password or a redirect; starting a backup or AutoSSL. Their request shapes follow cPanel's documentation
and are covered by the fake-WHM tests. Try each once on a throw-away account before relying on it.

**Not offered on cPanel servers** (shown as "not available for your hosting server yet" or left out of a page): IP blocking, the Security Center
(malware scan, quarantine, folder protection, SSH keys, file-integrity), usage history and live performance, webmail inside WHPanel, spam and
routing settings, automatic replies on some versions, backup testing/restore/schedules, per-domain SSL switches. Open cPanel covers the gap.


## Live write test (9 Oct 2026) - what was tried on the throw-away account and what it taught
Through the real gateway, one small thing of each kind was created, changed and removed on `testing2.com`; afterwards the account was checked
and nothing was left behind.

**Worked live:** make a folder, write, read, list, rename, copy, change permissions, zip, delete files and folders; create a database and a
database user, give and take privileges, change the user's password, delete both; create a mailbox, change its size and password, create and
delete a forwarder and an automatic reply, delete the mailbox; add, edit and delete TXT, A, CNAME and MX records; set and remove a redirect on
the main domain; change a PHP setting and put it back; create, edit, switch off and delete a cron job; create, change the password of and
delete an FTP login; read the error log; start a full backup; read usage and the server's health; Open cPanel.

**Found and fixed from it:**
- A plain **0** for subdomains, addon and parked domains means *none allowed* (the plan WordPress-Starter Startup has 0 and the server refused a
  subdomain); only the word unlimited means no limit (for disk, bandwidth and emails per hour a 0 does mean unlimited). Limits and the plan sync
  now read it that way (`plan_limit`).
- Copying a file into its own folder failed (cPanel copies into a folder): it now goes through a temporary folder.
- A timeout on a *change* was being asked again, and the second ask (for example deleting a forwarder that the first ask had already deleted)
  reported a false error. Now only plain reads are repeated; a timeout on a change says it may still have been made.
- WHM's load average function is `systemloadavg`; a service check that timed out no longer shows a service as degraded.
- Each call to this server takes 1 to 3 seconds (more at busy times), so the gateway remembers a slow provider's reads for 45 seconds (and the
  account's plan and domains too); any change forgets them. A page that took 10 to 15 seconds the first time now opens at once the second time.
- AutoSSL is not on this plan (`You do not have the feature autossl`): the message now says so in plain words.

**Not tried live:** adding a domain, subdomain or alias (this plan allows none), AutoSSL, restoring a backup, changing the account's control
panel password, and suspending and unsuspending the account (the last two were left for you to try).
