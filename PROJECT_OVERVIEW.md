# WHPanel: everything built so far

Last updated: 2026-10-17. This file is the one-stop record of the whole project: what it is, what has been built, what has only been tested, what has been proven on the real server, and what is still open. Detailed notes for each stage are in [docs/](docs/); the short plan for what comes next is [ROADMAP.md](ROADMAP.md).

**Status words used everywhere in this project**

- **Built**: the code exists.
- **Tested**: automatic tests pass (about 3,250 tests; they run on every push).
- **Proven**: it was run once on the real live server and worked.

---

## 1. What WHPanel is

The customer and staff portal of the hosting business (Web Host Era, webhostera.pk). It sells and manages hosting, domains and services, and lets customers run their websites without ever opening cPanel.

```text
Customer / staff -> WHPanel -> cPanel/WHM provider layer -> cPanel hosting servers
```

- **Stack:** Django 5.2, Django REST Framework, PostgreSQL, Redis, Celery, Docker, nginx.
- **Size:** 20 Django apps, about 95,000 lines of Python, 384 templates, about 3,250 automatic tests, 253 commits.
- **Languages in the product:** English interface. The AI Agent also answers in Urdu and Roman Urdu.

### Where it runs

| Thing | Value |
|---|---|
| Panel (live) | the panel domain on the panel server, in Docker (web, worker, beat, nginx, PostgreSQL, Redis) |
| Hosting server | one cPanel/WHM server (version 11.138, Apache, CloudLinux) holding the customers' websites |
| Test hosting account | `testing.com` on that server, used for real checks |
| Deploy | by hand: backup of the database, upload the code, rebuild the containers, collect static files, restart nginx, check the pages. Automatic deploy after green tests is built but waits for one GitHub secret (see section 11) |
| Backups | a database dump is taken before every deploy (by hand, on the same server); the weekly encrypted panel backup (`backup_panel`, Sundays 01:30, newest 3 kept) writes to the `panel_backups` volume, is read back to prove it, and is copied to S3-compatible storage once `PANEL_BACKUP_S3_*` is set (until then it exists on this server only) |
| CI | GitHub Actions on PostgreSQL 17 and Redis: runs the whole test suite on every push |
| Secrets | only on the server (`.env`) or in GitHub secrets. Provider passwords live in the database, encrypted. Nothing secret is in the repository |

---

## 2. The work, in the order it was done

### 2.1 Foundation: the hosting business engine (Phases 01 to 19)

| Phase | What was built |
|---|---|
| 01 | Foundation and architecture: project base, roles and permissions, audit log, API shape |
| 02 | Client management: clients, contacts (owner, billing, technical), staff and customer pages |
| 03 | Products, pricing and add-ons |
| 04 | Domain management: search, registration and transfer, TLD prices, registrar adapter |
| 05 | Hosting accounts and WHM provisioning |
| 06 | Cart and checkout |
| 07 | Billing and invoices: payments, quotes, tax, coupons, gateways and webhooks |
| 08 | Renewals, upgrades and proration |
| 09 | Order management and lifecycle |
| 10 | Customer service and support: tickets, departments, saved replies, attachments, knowledge base |
| 11 | Notifications and email: the catalogue of every message the system can send |
| 12 | Cancellation and service lifecycle (suspend, terminate, cancel) |
| 13 | Affiliate management and commissions |
| 14 | Reports |
| 15 | Follow-up gaps found by a documentation audit |
| 16 | Security hardening: two-factor sign-in, account lock-out, security headers |
| 17 | Reliability and failure handling: abandoned order expiry, backup before terminating, attachment retention |
| 18 to 19 | Testing and production readiness, launch checklist, scheduled hosting usage sync |

### 2.2 Design and owner tracks

- **D0 to D8** (design): navigation audit, design system, link integrity (a crawler test visits every page of every role), client and staff areas, visual system, header layout.
- **E1 to E14** (owner requests): owner tools (passwords, delete client, currencies USD/PKR/SAR, email health), cart and checkout, store categories, deliverability, editable email wording, looks (visual themes), alerts and policy pages, email design, dashboards and bulk actions, storefront integration (the WordPress plugin in `hosting-portal-connector/`).

### 2.3 Hosting control (H0 to H15)

A provider-neutral control layer between the portal and the hosting server, with a demo adapter (for tests) and a real cPanel/WHM adapter: file manager, databases, email, domains and DNS, SSL, PHP, cron, backups, security, usage and logs, staff admin access, infrastructure dashboard, and an "Open cPanel" fallback. Every action is allow-listed, permission-checked and audited.

### 2.4 The native-hosting track was dropped

The plan to build our own servers and agent was cancelled on 2026-10-15; its code was removed. It stays only in git history (`git checkout archive/native-track`).

### 2.5 The Hostinger-style experience (Phases 0 to 7 of docs/hostinger-parity-plan.md)

| Phase | Result |
|---|---|
| 0 | Prerequisites checked on the live server (PKR currency, server facts, test account) |
| 1 | New customer shell: slim icon rail, top bar, website menu with groups, breadcrumbs, phone drawer |
| 2 | Websites list and website dashboard: essentials, WordPress install with every step and a percentage, one-click WP Admin sign in, preview address through the panel's own nginx so a site opens before its domain points |
| 3 | WordPress section: overview, plugins and themes, staging, website options |
| 4 | Domains section: all domains, subdomains, parked domains, redirects, DNS zone editor; database browser and row editor |
| 5 | Advanced tools that need no special server feature: protected folders, hotlink protection, folder index, IP blocking |
| 6 | Features that depend on the server: live resource use with a 24-hour history, visitor statistics (AWStats), SSH access |
| 7 | Public site: top bar with Pricing, Products and Resources menus, pricing page |

### 2.6 The front page and the public site

New front page in the Hostinger style (hero with domain search, plans and prices, tools tabs, essentials, FAQ). Prices, plans, extensions and categories are read live from the store, so changing a price in the backend changes the page at once. Setup > Home page edits the words; Setup > Pages makes About/Terms pages; a built-in Contact page; expanded footer with social links.

### 2.7 Per-website tools (latest rounds)

Care (speed, malware scan, updates, maintenance mode, performance, quarantine), **Staging** and **Copy website** now work for every website of a plan, not only the main one. Zone management (all DNS zones of a plan in one list, filtered by name and kind). The customer Emails page opens each mailbox in webmail and explains how to sign in.

### 2.8 The AI Agent (five phases)

See section 6.

### 2.9 The help centre

110 detailed articles in 9 topics, topic and article cards, a clear article page, search, a sitemap, `robots.txt` and structured data. See section 7.

### 2.9b Extras sold on what customers already have

SSL certificates and WHOIS privacy (prices from the owner's site, yearly) are offered on the SSL page, the Domains pages, the website dashboard (when there is no SSL), the dashboard to-dos and the renewal invoice. **Buy now** makes an invoice on the spot (`apps/renewals/addon_orders.py`); once it is paid, staff get an alert, set it up and mark it done (Billing > Add-on orders) and the customer is told. A daily job (`send_expiry_notices_task`, 08:30) warns before a domain, plan or SSL certificate ends (30, 14, 7 and 1 days; 14 and 7 for SSL), once per step.

### 2.10 Access control for staff

Every permission is shown in a table per person: what the role gives, what was added, what was hidden. An administrator can add a permission for one person or hide one from them without touching the role. The person's menu shows what they can now open. (Section 8.)

---

## 3. What a customer can do today

| Area | Features |
|---|---|
| Account | Sign in with optional two-step code, profile, notifications, billing details |
| Websites | List, create WordPress (progress with steps and percentage) or custom website, add more websites to a plan, WP Admin in one click, set a new WordPress password, preview address before DNS, delete website, import website |
| Website care | Speed test and PageSpeed report, malware scan and quarantine, WordPress updates, plugins and themes manager, maintenance mode, search-engine switch, faster pages, traffic, automatic backups, **staging**, **copy website** (for each website) |
| Hosting plan tools | Files and trash, FTP, databases (browse and edit rows), PHP version, limits and extensions, cron jobs, logs, traffic, resource use, SSH, SSL, backups (create, download, restore), protected folders, IP blocking, Open cPanel fallback |
| Domains | Search, register, transfer, renew, lock and unlock, nameservers, DNS zone editor, **zone management**, subdomains, parked domains, redirects |
| Email | All mailboxes, **Open webmail**, mailbox settings (signature, automatic reply, password), forwarders, spam settings, routing |
| Billing | Invoices, online or bank payments, report a payment, renewals, upgrades |
| Orders | Cart, checkout, order status |
| Support | Tickets, help centre, AI Agent, live chat with a person, callback requests |
| Refer and earn | Personal link and commissions |

## 4. What staff can do today

Clients, users and roles (with the per-person permission table), products and pricing, servers (test connection, plan sync), domains and TLD prices, hosting accounts and admin access, orders, billing (invoices, payments, quotes, coupons, tax), renewals and cancellations, support (tickets, departments, saved replies, knowledge base with icons, summaries and featured articles), **live chats** (queue, take, reply, hand back, pass on, convert to ticket, callbacks), **AI Agent** settings, knowledge, analytics, reports, audit log, email providers and templates, brand and looks, menu control, Setup pages (Home page, Pages, Branding), a dashboard with widgets. The staff menu turns any area with more than five pages into its own menu in the header.

## 5. The public website

Front page, hosting page, pricing, product pages, domain search and prices, WHOIS lookup, help centre, contact page, owner-written pages, storefront API for an external WordPress site.

---

## 6. The AI Agent

A chat in the customer area (a pop-up from the header button on every page, and a full page).

| Phase | What it does |
|---|---|
| 1 | Chat; looks only at the customer's own services; tools for website status, SSL, disk, error log, DNS, email quota, invoices; opens tickets; switch, daily limit |
| 2 | Live chat with a person: queue, staff dashboard, take, reply, hand back, pass on, close, make a ticket; the customer's page updates by itself |
| 3 | Changes the customer approves with a button (renew SSL, force HTTPS, mailbox size, PHP limit, malware scan, backup); one use, 30 minutes |
| 4 | Sensitive changes need a **security code** emailed to the customer and entered on a separate page, never in the chat (DNS record add and delete, backup restore, mailbox delete); 5 minutes, 3 tries, lock with alerts |
| 5 | Staff online toggle and working hours (a ticket when nobody is online), analytics, thumbs on answers, callback requests, WhatsApp link, teaching the Agent from a staff answer, a test box |

**How it answers.** Two modes, chosen in Setup > AI Agent:

- **Knowledge base (default, no outside service):** matches what the customer writes against about 40 knowledge entries in English, Urdu and Roman Urdu, runs the live checks the entry asks for, asks "which domain?" when needed, and keeps unanswered questions so staff can teach it.
- **Claude (optional):** free conversation through the Anthropic API; needs `ANTHROPIC_API_KEY` in the server environment only. The tools are the same; changes are only proposed.

**How it explains.** For anything the panel lets the customer do alone, it first tells the steps in the panel; it prepares the change only when the customer asks it to or says they did not understand.

**Safety rules built in.** The panel, not the AI, decides and runs everything. A tool never receives a client number; it works only on the signed-in person's own services. Tool results are treated as data. Passwords and codes are never asked for in the chat. A switch stops all changes at once. Everything is audited.

Not possible with the panel's current provider layer, so the Agent hands these to a person: a domain transfer-out (EPP) code, and a hosting password reset (the customer sets it on the Information page).

## 7. The help centre

110 articles in 9 topics (Getting started, Websites and WordPress, Domains and DNS, Email, Hosting tools, Security and backups, Troubleshooting, Billing and orders, Account and support), about 26,800 words. Each article has a summary, steps, tips, and at least three questions and answers; articles link to each other with `[[Title]]`. Staff write in a small safe markup (headings, steps, bullets, notes, bold, code, links). Search results are not indexed. Every article carries a title, description, canonical address, Open Graph tags and JSON-LD (Article, BreadcrumbList, FAQPage). `/sitemap.xml` and `/robots.txt` are served. A reader can say whether an article helped. `python manage.py seed_help` adds the starter articles safely (never changes existing ones).

## 8. Security model

- Roles: customer, support agent, technical, manager, admin, super admin. Permissions are named `view_*` and `manage_*` per area, plus `assign_roles` and `view_audit_log`.
- Per person: extra permissions can be added, and any permission can be hidden from one person. A Super Admin cannot be restricted. Changes need `assign_roles`; only a Super Admin changes an admin.
- Customers reach only their own records (ownership checks in every module).
- Every sensitive action is written to the audit log. Provider credentials are encrypted. Uploads are validated by extension and content. Account lock-out, security headers, optional two-factor sign-in (authenticator app).
- **New device sign-in:** a correct password on a browser the panel does not know is held for a six-digit code emailed to the account (hashed, 10 minutes, 5 tries then a lock, resend every 30 seconds, 5 an hour). Only one device stays signed in: the new one becomes the device and every other session is ended (and an old one is signed out on its next click). Switch off with `SIGNIN_EMAIL_CODE=false`. The API (JWT) login is not covered.
- **Server hardening (2026-10-17):** SSH by key only (no passwords, root only with a key); host nginx accepts only Cloudflare's addresses, restores the real visitor address, rate-limits sign-in, registration, reset, codes and the domain search, and hides its version; staff must have two-factor sign-in on and are signed out after 60 idle minutes; the Django admin can be moved (`DJANGO_ADMIN_URL`); Redis can take a password; web, worker and beat run without extra Linux capabilities; CI runs pip-audit and bandit and Dependabot is on; the one-file PHP helpers expire after 15 minutes and a nightly sweep removes any left behind. The incident plan is `docs/incident-runbook.md`.
- **Payment proof:** a customer can attach a screenshot or PDF to a reported offline payment; it is stored privately, checked by type and content, and only the customer and billing staff can download it.
- The hosting layer allow-lists every operation; one-file PHP helpers are written into a website, run once with a token, and delete themselves.

---

## 9. Quality: tested versus proven

**Proven on the live server** (checked on the real WHM server with the test account): the panel deploys and runs; WordPress installation; one-click WP Admin and the preview address; database browsing; SSH, resource use and visitor statistics; the new front page and Setup editors; the help centre pages, sitemap and robots; the permission table pages load.

**Built and tested, not yet exercised on a real customer account:** Staging and Copy website for an added domain; Care for an added domain; the whole AI Agent flow (chat, how-to-first, Approve cards, security codes, live chat, callbacks); webmail buttons on the Emails page; zone management; per-person permission hiding. They pass the automatic tests against the demo hosting adapter and their pages answer on the live server. The first real use should start with something harmless, such as a malware scan or a test mailbox.

**Known limits.** The preview address is served over plain HTTP until a wildcard certificate arrangement is made. Git deployment, SSH keys and phpMyAdmin depend on server settings (section 10).

---

## 10. What only the owner can do

| Item | Why |
|---|---|
| `DEPLOY_SSH_KEY` in GitHub (Repository secrets) | Lets a push deploy by itself. Never sent through chat |
| `ANTHROPIC_API_KEY` in the server `.env` | Only if the Claude mode is wanted. The knowledge mode works without it |
| Cloudflare DNS-edit token on the server | HTTPS for preview addresses |
| WHM Feature Manager: `version_control` | Git deployment |
| WHM "SSH Access" feature | SSH keys |
| MySQL service on the server | phpMyAdmin entry |
| Meta business account | A real WhatsApp bot (a plain "continue on WhatsApp" link already works) |
| Content in Setup | Home page words, FAQ, social links, About and other pages, the Agent's knowledge entries |
| Setup > AI Agent switches | "Open a ticket when nobody is online" and working hours are off until switched on |
| Google Search Console | Submit the sitemap address |
| Old empty help categories | Close the six empty starter categories (Hosting, Domains, cPanel, DNS, WordPress, Billing) if wanted |

## 11. What is still open

1. Prove the new per-website and AI Agent flows on a real test account.
2. Sign-in and activity alerts to customers beyond the new-device email (password change, staff access to their hosting); the same device rule for the API.
3. Release hygiene: a staging copy of WHPanel, branch protection, a restore drill.
4. Customer features: SMS or WhatsApp notices, payment proof upload, public status page, customer API tokens, Urdu interface, data export and deletion.
5. Website tools not built: Git deployment, CDN, several domain pointing helpers, automatic repair of modified WordPress files.
6. A longer version of the most searched help articles once real search data exists.
7. Import real prices before going live.

---

## 12. Repository map

```text
config/              settings (base/dev/test/prod), urls, api_urls (/api/v1/), celery
apps/core/           shared: errors, crypto, navigation registry, test harness, crawler tests
apps/accounts/       users, roles, permissions (+ per-person hiding), sign-in, two-factor
apps/audit/          append-only audit log
apps/notifications/  the message catalogue, email delivery, in-app notices
apps/clients/        clients and contacts
apps/products/       catalogue, pricing, servers, public menus
apps/domains/        domains, TLD prices, registrar adapter
apps/hosting/        hosting accounts, control layer, cPanel and demo adapters, every customer tool
apps/billing/        invoices, payments, tax, coupons, gateways
apps/orders/         cart, checkout, orders, fulfilment
apps/renewals/       renewals, upgrades, proration
apps/lifecycle/      suspension, termination, cancellation
apps/affiliates/     referrals and commissions
apps/reports/        reports
apps/support/        tickets, help centre (kb.py, kb_content/), departments
apps/assistant/      the AI Agent (agent, brain, tools, actions, live chat, knowledge, analytics)
apps/branding/       brand, looks, home content, owner pages
apps/storefronts/    API for external storefronts
apps/console/        staff console: users, dashboard, setup pages
templates/ static/   pages, styles and scripts
hosting-portal-connector/   the WordPress plugin for storefronts
deploy/nginx/        proxy configuration
docs/                one note per stage, guides and the runbook
```

Useful commands: `python manage.py migrate`, `python manage.py seed_help`, `python manage.py sync_roles`, `python manage.py verify_billing`, `pytest`.
