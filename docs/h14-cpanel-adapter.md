# H14: Complete transitional cPanel adapter - part a (DNS, databases, email, PHP)

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

Safety rules in every tool: the domain, zone, database or user named must belong to **this account** (checked against `DomainInfo` /
`Mysql` listings first, so typing another customer's name reaches nothing); names are plain ASCII; values must come from the plan's
lists; a stale DNS line is refused rather than guessed.

## Everything is OFF until staff turn it on
Because the field names cPanel returns differ between versions and none of this has met a live server, **no cPanel customer tool is
offered by default**. Turn tools on one at a time in the environment:

```
HOSTING_CPANEL_CAPABILITIES=dns,databases,email,php
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

## Still to do in H14
Files (Fileman), domains and subdomains (addon, subdomain, redirects), SSL (list, install, AutoSSL, HTTPS redirect), cron, backups
(full backup, list, restore), and the real-server verification above. The tools above also need a pass for cPanel versions in use.
