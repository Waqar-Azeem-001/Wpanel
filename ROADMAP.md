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

The full record, with what is tested and what is proven, is in [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md). In short:

| Area | What exists |
|---|---|
| Business | catalogue, cart and checkout, orders, invoices and payments (manual, bank transfer, JazzCash), renewals, lifecycle, affiliates, support, notifications, reports, storefront API and WordPress plugin |
| Hosting control (H0-H15) | provider-neutral control layer, websites dashboard, file manager, databases (browse and edit), email with webmail, domains, DNS and zone management, SSL, PHP, cron, backups, security, usage, logs, SSH, staff admin access, infrastructure dashboard, cPanel adapter, Open cPanel fallback |
| Website workspace | Websites list, create website (WordPress or custom), several websites per plan, website dashboard, preview address, one-click WP Admin, Website care, staging and copy website for every website, delete website, domain check with transfer guidance |
| Customer shell and public site | icon rail and top bar, website menu, public top bar with Pricing, Products and Resources menus, new front page, editable home words and own pages, expanded footer |
| AI Agent | chat on every customer page (knowledge base mode, Claude optional), how-to first, changes with Approve, security code for sensitive changes, live chat with a person, callbacks, analytics, staff knowledge editor |
| Help centre | 110 articles in 9 topics, search, cards, readable article page, structured data, sitemap, robots |
| Access control | roles, plus per-person extra permissions and hidden permissions, with the person's menu shown |
| Operations | CI on PostgreSQL, deploy after green tests (waits for the GitHub secret), deploy and failure notices through WHPanel's own email, a database dump before every deploy (taken by hand, kept on the same server). The nightly encrypted panel backup is built but does NOT work yet: pg_dump is missing in the image, the folder is not a volume and nothing is copied off the server |

## Next

The detailed plan for the Hostinger-style experience is in [docs/hostinger-parity-plan.md](docs/hostinger-parity-plan.md); its phases 0 to 7 are done.

1. **Prove it on the live server.** Run the newest flows once against the real test account and record the result: staging and copy website of an added domain, Care of an added domain, the AI Agent (a how-to, an Approve card, a security-code change, a live chat, a callback), the Emails page buttons, zone management.
2. **Owner items** (see section 10 of the overview): the deploy key secret, preview HTTPS, Git deployment and SSH keys in WHM, phpMyAdmin, the AI Agent switches and working hours, Setup content, the sitemap in Google Search Console.
3. **Sign-in safety.** Make two-factor authentication mandatory for staff; login and activity alerts to customers (new device, password change, staff access to their hosting).
4. **Release hygiene.** A staging copy of WHPanel itself, branch protection on `main`, error tracking reviewed, a restore drill for the panel database.
5. **Customer features.** SMS/WhatsApp notices (and a real WhatsApp bot), payment proof upload with staff verification, public status page, customer API tokens, Urdu interface, data export and deletion.
6. **Website tools not built yet.** Git deployment, CDN, automatic repair of modified WordPress files, hosting password reset and the domain transfer-out code by the Agent (the provider layer has no operation for them yet).
7. **Help centre depth.** Lengthen the most searched articles once real search data exists; keep the Agent's knowledge entries growing from the questions it could not answer.
8. **Independence from WHMCS.** Import real prices before going live.

## Rules that stay

Nothing is deployed without the owner's approval; pushes to `main` deploy automatically when tests pass. Every report says what is built, what is tested, and what has been proven on a real server.
