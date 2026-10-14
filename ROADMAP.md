# WHPanel roadmap

WHPanel is the customer and staff portal for the hosting business: ordering, billing, domains, support, and day-to-day hosting management on top of the existing cPanel/WHM servers.

**Scope decision (2026-10-15):** WHPanel stays on cPanel/WHM. The earlier plan to build its own hosting servers and a server agent (the "N track"), and to migrate accounts onto them (the "M track"), was
dropped and its code removed. It is kept in git history only: `git checkout archive/native-track` (tag) shows the project as it was.

## What the product is

```text
Customer -> WHPanel -> cPanel/WHM provider layer -> cPanel servers
```

The customer never needs to open cPanel. "Open cPanel" stays as a small advanced fallback.

## Built

| Area | What exists |
|---|---|
| Business | catalogue, cart and checkout, orders, invoices and payments (manual, bank transfer, JazzCash), renewals, lifecycle, affiliates, support, notifications, reports, storefront API |
| Hosting control (H0-H15) | provider-neutral control layer, websites dashboard, file manager, databases, email, domains and DNS, SSL, PHP, cron, backups, security, usage and logs, staff admin access, infrastructure dashboard, cPanel adapter, Open cPanel fallback |
| Website workspace | Websites list, create website (WordPress or custom), website dashboard, Website care (speed test, malware scan, WordPress updates, automatic backups, maintenance mode, plugins and themes, traffic), staging, delete website, domain check with transfer guidance |
| Operations | CI on PostgreSQL, automatic deploy after green tests, deploy and failure notices through WHPanel's own email, nightly encrypted backup of the panel database |

## Next

The detailed phase plan for the Hostinger-style experience (shell, dashboard, WordPress, domains, advanced tools, public site) is in [docs/hostinger-parity-plan.md](docs/hostinger-parity-plan.md).

1. **Prove it on the live server.** Run every hosting action once against a real test account on the live WHM server and record the result (WordPress install, staging, delete, backups, SSL, DNS).
2. **Sign-in safety.** Make two-factor authentication mandatory for staff; login and activity alerts to customers (new device, password change, staff access to their hosting).
3. **Release hygiene.** A staging copy of WHPanel itself, branch protection on `main`, error tracking reviewed, a restore drill for the panel database.
4. **Customer features.** SMS/WhatsApp notices, payment proof upload with staff verification, public status page, customer API tokens, Urdu interface, data export and deletion.
5. **Website tools not built yet.** Git deployment, CDN, cache switch, several websites inside one plan, a temporary preview domain (needs a wildcard domain), automatic repair of modified WordPress files.
6. **Independence from WHMCS.** Import real prices before going live.

## Rules that stay

Nothing is deployed without the owner's approval; pushes to `main` deploy automatically when tests pass. Every report says what is built, what is tested, and what has been proven on a real server.
