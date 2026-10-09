# The provider-neutral hosting API

`/api/v1/hosting/services/{id}/<resource>/` (roadmap section 03). No provider is ever named; every call goes through the same
hosting-control gateway as the screens, so permissions, the live-account check, plan limits, error mapping and the audit trail are the
same. Sign in as for the rest of the API (JWT or session). A 404 means the service is not yours; adapter refusals are 400 with a plain
message. Responses never contain passwords, keys or tokens.

| Resource | GET | POST | PATCH / PUT | DELETE |
|---|---|---|---|---|
| `files/?path=` | list a folder | `{path}` make a folder | | `?path=` move to trash |
| `databases/` | limits, databases, users | `{name}` | | `databases/{name}/` |
| `emails/` | limits, mailboxes, forwarders | `{address, password, quota_mb}` | `emails/{address}/` `{password}` or `{quota_mb}` | `emails/{address}/` |
| `domains/` | limits, domains | `{name, kind, document_root}` | `domains/{name}/` `{redirect_to, permanent}` | `domains/{name}/` |
| `dns/?domain=` | records | `{domain, type, name, value, ttl, priority}` | `dns/{record_id}/` (PUT) | `dns/{record_id}/?domain=` |
| `ssl/` | every domain's certificate | `{domain}` install or renew | `ssl/{domain}/` `{auto_renew, https_redirect}` | `ssl/{domain}/` |
| `php/?domain=` | options and settings | | PATCH `{domain, version, memory_limit, ...}` | |
| `cron/` | options, jobs | `{schedule, command}` | `cron/{id}/` | `cron/{id}/`; `POST cron/{id}/run/` |
| `backups/` | options, backups | `{kind}` | | `backups/{id}/`; `POST backups/{id}/verify/`, `.../restore/` |
| `usage/?days=7` | usage, optional history | | | |

**Staff** (not contacts of the client) must send `X-Admin-Access-Reason: <ticket or note, 5 to 200 characters>` on every call; it is
recorded with each change, with `admin_access`, the client and the service. A role that may only look cannot change anything or read
mail and database rows (403), exactly as in the screens. A provider that does not offer an operation answers 501.
