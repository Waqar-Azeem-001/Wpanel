# Hostinger-style experience: phased plan

**Goal.** A customer should get the same kind of experience Hostinger's hPanel gives: a clean left-hand navigation, a Websites list grouped by plan, a dashboard per website with the
everyday tools one click away, WordPress controls, domain tools, advanced tools, and a public website with a proper menu. Everything runs on the existing cPanel/WHM servers; nothing
here needs our own server platform.

**How to read this file.** One phase at a time. Each phase says what the customer sees, what already exists, what must be built, how, what it needs from the real server, how big it is,
and when it counts as done. Status words follow the project rule: *Built* (code exists), *Tested* (automatic tests pass), *Proven* (worked on the real live server).

Size: **S** = a day or less, **M** = a few days, **L** = a week or more.

---

## Phase 0: Before anything else (prerequisites)

| Item | Why | Owner |
|---|---|---|
| Choose **PKR** as Store currency (Setup > Branding) | Prices, cart, reports and the TLD price sync all follow it; the sync refuses to run in a different currency | Owner, 1 minute |
| Fix the **DEPLOY_SSH_KEY** secret in GitHub (Repository secrets) | So a push deploys by itself instead of by hand | Owner |
| One **test hosting account** on the live WHM server that we may use freely | Every "Proven" in this plan needs a real account to try things on | Owner |
| Note the server facts: cPanel/WHM version, whether **LiteSpeed**, **CloudLinux**, **git** and **SSH** are available | Several features below only exist if the server has them; we check once and plan around it | Technical, 1 hour |

**Status (2026-10-15).**

| Item | Result |
|---|---|
| Store currency | **Done.** PKR is set on live (Setup > Branding). The TLD price sync ran: 39 extensions, prices follow webhostera.pk. |
| Deploy key | **Not working yet.** The pipeline still reports "NOT deployed" because it cannot see `DEPLOY_SSH_KEY`. The key itself works (we logged in with it). Deploys are done by hand until the secret is visible to the workflow. |
| Test account | `testing.com` (plan "WordPress-Best Value Grow") exists on the live WHM and was used for the checks below. |
| Server facts | Checked read-only on the live server, see the table below. |

**Server facts (checked on the live WHM, read-only).**

| Fact | Result | What it means for the plan |
|---|---|---|
| cPanel/WHM version | 11.138.0.15 (server.webhostera.pk) | Very recent; the APIs we use are all available |
| Web server | **Apache** (httpd). No LiteSpeed service | **No LiteSpeed cache switch** (Phase 3 drops it). Browser-cache and compression rules stay |
| CloudLinux | **Yes** (alt-php 5.2 to 8.4 installed, LVE usage keys answer) | CPU, memory, I/O and process usage are available per account (Phase 2) |
| PHP | MultiPHP: ea-php74 to 84 and alt-php | PHP version switch works as built; "PHP Info" can show it |
| Git (Version Control) | **Feature not enabled** for this plan ("You do not have the feature version_control") | Git deployment needs the owner to enable the feature in WHM > Feature Manager first (Phase 6) |
| SSH | sshd running; the account's shell is `noshell` | SSH Access is possible by granting the account a shell in WHM; needs a test (Phase 6) |
| Raw access logs | **Not reachable** through the file API (the logs folder is outside the account's home) | Real visitor analytics from logs is not possible this way; options are cPanel's own AWStats (feature exists) or a small WordPress tracking snippet (Phase 6) |
| Redirects, SSL list, error log | Answer normally | Phase 4 and the Security pages can use them |

**Done when:** a push reaches live on its own (the only open item above).

---

## Phase 1: The panel shell (hPanel layout)

**Customer sees.** A slim icon rail on the left (Home, Websites, Domains, Emails, Refer & earn, More services), a top bar (notifications, account), and inside a website a second
menu with groups: Dashboard, WordPress, Hosting Plan, Performance, Analytics, Security, Domains, Website, Files, Databases, Advanced. Breadcrumbs: Home > Websites > example.com > section.

**Exists.** The customer area has a left menu and a website workspace menu with the same tools (flat groups). Staff area has its own shell.

**Build.**
1. New customer shell layout: icon rail + top bar, collapsible, works as a drawer on a phone.
2. Website menu regrouped into the Hostinger groups above, with expandable sub-menus and the current page highlighted.
3. A website switcher at the top of the website menu (dropdown of the customer's websites), as in hPanel.
4. Breadcrumbs on every customer screen.
5. Home page: greeting, "Your business" cards (each website and domain), "To-dos" (renewals, expiring card, free domain, unfinished setup), shortcuts.

**How.** Only templates, CSS and the menu builder (`apps/core/portal.py`, `apps/core/navigation.py`). No new server calls. The existing look settings (Setup > Branding) keep driving colours.

**Needs from the server.** Nothing.

**Risks.** The staff area shares some templates; keep the new shell customer-only. Many existing tests check the old menu text and will need updating.

**Size.** L. **Done when:** every customer page renders in the new shell on desktop and phone, no page-level overflow, link crawler green, and the old menu is gone.

---

## Phase 2: Websites list and website dashboard

**Customer sees.** Websites grouped by hosting plan (plan name, expiry, "Create website" and "Import website" on the plan row). Each site row: name, **Tools** dropdown, **WP Admin**,
**Dashboard**, and a menu. The dashboard shows Essentials, Performance with desktop and mobile score, Plan resource usage, and a "transfer your domain" banner.

**Exists.** Websites list, Hosting list, dashboard with Care tiles, Tools grid, speed test and PageSpeed, domain note with transfer link.

**Build.**
1. Websites list grouped by plan; Tools dropdown (File manager, Databases, Email, Backups, PHP, Logs); "Import website" entry (explains the migration route for now).
2. Dashboard: Essentials card as in hPanel (Database, Backups, File manager, **Cache**: Clear cache and Preview without cache, Hosting plan).
3. Performance card with desktop and mobile score side by side.
4. Plan resource usage: disk, inodes, websites used / allowed, **CPU and memory (last 24 hours)** where the server reports them.

**How.** Cache clear for WordPress through the existing one-file tool (call WordPress's own cache flush); "Preview without cache" is the site address with a no-cache query. CPU, memory and
inodes come from the cPanel resource-usage API; the live server is CloudLinux, so CPU, memory, I/O and processes are available (confirmed in Phase 0).

**Needs from the server.** Nothing more: confirmed available.

**Size.** M. **Done when:** the grouped list and the dashboard match the layout above on a test account; CPU/memory shown only when the server reports them (never invented).

---

## Phase 3: WordPress section

**Customer sees.** WordPress > Overview with switches: **Force HTTPS**, **Maintenance mode**, **LiteSpeed cache**; **WordPress updates** (change); SSL status; "Edit website" (WP Admin);
database name; backup status; PHP version; **Recommended installations** (plugins and themes in one click). Sub-pages: Updates, Staging, Copy Website.

**Exists.** Maintenance mode, search-engine switch, automatic update rules, plugins/themes manager (list, switch, update), staging (create, publish, delete), backups schedule, PHP settings.

**Build.**
1. Overview page with the switches above in one place.
2. Force HTTPS switch (the SSL capability already has "always use HTTPS").
3. Plugin and theme **install** from wordpress.org by name (curated recommended list).
4. **Copy Website**: clone a site into a new folder/subdomain using the same copier staging uses.
5. A LiteSpeed switch is **not** built: the live server runs Apache (Phase 0 check).

**How.** Extend the one-file WordPress tool (`wp_tool`) with an install action using WordPress's own upgrader; reuse `wp_clone` for copy.

**Needs from the server.** Outbound access to wordpress.org from the server (already needed by the installer).

**Size.** M. **Done when:** each switch changes the real site on the test account and the page shows the real state after reload.

---

## Phase 4: Domains section

**Customer sees.** Domains > **Subdomains** (create with optional custom folder), **Parked domains**, **Redirects**, plus **DNS Zone Editor**.

**Exists.** The Domains screen (addon, subdomain, alias, redirect) and DNS records screen, as one page each.

**Build.** Split into the three Hostinger pages with their own forms and lists; put DNS Zone Editor under Domains; keep one engine underneath.

**How.** UI only; the domains capability already supports all of it.

**Needs from the server.** Nothing new.

**Size.** S to M. **Done when:** create and remove of a subdomain, an alias and a redirect work from the new pages on the test account.

---

## Phase 5: Advanced tools that need no special server feature

**Customer sees.** Under Advanced: **Password Protect Directories**, **Hotlink Protection**, **Folder Index Manager**, **IP Manager**, **PHP Info**, **Fix File Ownership**,
**Activity Log**, with Cron Jobs and PHP Configuration regrouped there.

**Exists.** Cron, PHP settings, logs, FTP; quarantine and scan in Website care; audit trail.

**Build.**
| Tool | How |
|---|---|
| Password protect a folder | Write `.htaccess` and `.htpasswd` in that folder through the files capability (password stored hashed) |
| Hotlink protection | A marked block in the site's `.htaccess` (allowed domains, file types) |
| Folder index manager | `Options -Indexes` or index-style rules, per folder |
| IP manager (block/allow) | A marked `.htaccess` deny/allow block |
| PHP Info | A one-time file that prints PHP's information to the customer, then deletes itself |
| Fix file ownership | Reset folder and file permissions to safe values (we cannot change the owner from a customer account; the page says so) |
| Activity log | The account's own audit trail (who changed what and when), no server call |

**How.** All through the existing files capability and the same "marked block" technique the performance rules already use, so every tool can be switched off again cleanly.

**Needs from the server.** Apache or LiteSpeed reading `.htaccess` (true on cPanel).

**Size.** M. **Done when:** each tool has an on and off state that survives a reload and a test on the real site proves the behaviour (for example a protected folder asks for a password).

---

## Phase 6: Features that depend on the real server

Do these only after Phase 0 has confirmed what the server offers; each one is a small spike (try it on the test account first), then a screen.

| Feature | Possible when | Notes |
|---|---|---|
| **SSH Access** (enable, port, username, key upload, change password) | WHM grants the account a shell (now `noshell`); sshd is running | Show the exact `ssh` command like hPanel; never show a password back |
| **Git deployment** | The `version_control` feature is enabled in WHM (**currently off** on the plan) | Connect repository, deploy on push, log, roll back |
| **Resource usage over time** (CPU, memory, processes, inodes) | **Available** (CloudLinux confirmed) | Needs a place to keep the history: store a reading every few minutes, chart the last 24 hours |
| **Analytics** (visitors, top pages, countries) | **Raw logs are not reachable** through the file API | Use cPanel AWStats (feature present) or a tracking snippet; decide when we get here |
| **LiteSpeed cache manager** | Not available: the server runs Apache | Only our browser-cache and compression rules |
| **phpMyAdmin / database tools** | cPanel exposes the one-time sign-in | Open from the Databases page |

**Size.** M each. **Done when:** a spike note for each says "works" or "not available on this server", and available ones have a screen.

---

## Phase 7: Public website (mega menu)

**Visitor sees.** Top bar: **Pricing**, **Products** (Hosting, Domains, Websites and apps, Ecommerce, Email and marketing) with a mega menu, **Solutions**, **Developers**, **Resources**, language, Sign in.
Home page with hero, plans, domain search, FAQs, footer with columns (Hosting, Domain, Tools, Information, Company, Support) and payment methods.

**Exists.** Public home, product category pages, domain search, knowledgebase, cart and checkout, brand and footer settings.

**Build.**
1. Mega menu fed from the product categories we actually sell (WordPress, Shared, Business, E-commerce, Reseller, VPS, Dedicated) and domain pages (search, transfer, full TLD list from the synced prices).
2. Solutions, Developers and Resources menus only for pages that really exist (knowledgebase, API docs, blog if any); no empty promises.
3. Home page sections and footer columns driven from settings so content can change without code.
4. Pricing page with the plans and the full TLD table.

**How.** Templates and the existing navigation settings; content comes from Setup.

**Needs.** The owner's content (copy, plan comparison, FAQ text).

**Size.** L. **Done when:** every menu entry opens a real page, the page works on a phone, and nothing in the menu is a dead link.

---

## Phase 8: Later or optional

| Idea | Position |
|---|---|
| AI assistant ("Agent") that answers questions and does simple actions | Possible with an AI service; needs its own plan, key, cost and safety rules. Not a copy job |
| AI Troubleshooter for WordPress | Builds on the assistant; later |
| AI Website Builder, templates marketplace | A product by itself; integrate a third-party builder instead of building |
| VPS, GPU, n8n, WooCommerce-specific hosting, email marketing, ads | Not ours to sell unless the business adds them |

---

## Suggested order

1. Phase 0, then **Phase 1** (the visible change) together with **Phase 5** (quick wins that fill the Advanced menu).
2. **Phase 2** and **Phase 3** (the dashboard and WordPress experience).
3. **Phase 4** (domain pages).
4. **Phase 6** spikes while the above is going on, since they need the server.
5. **Phase 7** when the owner's content is ready.

## Honest limits

- Everything marked Phase 1 to 5 is built from what the cPanel/WHM provider layer and the files capability already give us. It is *Built and Tested* when finished; it is *Proven* only after
  it has been tried on the live test account.
- Hostinger runs its own platform (its own caching, AI services, builder). We reproduce the **experience** on cPanel; some switches simply do not exist on a standard cPanel server
  and will not be shown rather than faked.

---

## Phase 6 results (2026-10-16, checked on the live server with the test account)

| Feature | Result | What was built |
|---|---|---|
| **Resource usage** | **Works.** `ResourceUsage::get_usages` answers with CloudLinux figures (CPU, memory, processes, entry processes, I/O, IOPS). cPanel keeps no history. | A reading every ten minutes (Celery beat), kept a week; the Usage page draws the last 24 hours and shows nothing when there are no readings. |
| **Visitor analytics** | **Works through AWStats.** Raw logs stay out of reach, but cPanel's AWStats answers for the main domain (`/awstats.pl?config=<domain>`). | "Open visitor statistics" on the Traffic page: a one-time sign-in goes straight to the report. Updated by the server once a day. |
| **SSH access** | **Works.** Port 22 is open; the account's shell can be switched with WHM `modifyacct HASSHELL` (tried on and off on the test account, restored). Key import needs the cPanel feature "ssh", which is not in the feature list, so keys show as "not manageable from here" until it is. | Advanced > SSH access: on/off, the `ssh` command, keys when the feature exists. |
| **Git deployment** | **Not available.** "You do not have the feature version_control". | Nothing built; it needs the feature enabled in WHM > Feature Manager first. |
| **LiteSpeed cache** | **Not available** (Apache). | Nothing built. |
| **phpMyAdmin** | One-time session works, but phpMyAdmin then fails inside the server (`cpses_...` MySQL user is refused). | WHPanel's own table browser instead (tables, rows, typed SQL, single-row edit) through a one-time tool. |

---

## Phase 7 results (2026-10-16)

| Item | Result |
|---|---|
| Top bar | **Pricing**, **Products** (mega menu), **Domains**, **Resources** (help centre and its categories), then Cart and Sign in. On a phone the mega menu opens inside the drawer, fully expanded. |
| Mega menu | Built from the store categories under the heading staff gave them (Hosting, Servers, Security and domains) plus a "Domains and more" column (Domain prices, Add-ons). A hidden category leaves the menu; an empty menu is not drawn. Code: `apps/products/public_menu.py`. |
| Pricing page | `/pricing/`: every plan with its lowest price by category, and the full domain price table with a filter box. Each extension leads to a search with it filled in. |
| Home | A strip of the cheapest domain extensions with their price, linking to the price table. |
| Footer | "Pricing" and "Domain prices" added. |
| **Left out on purpose** | **Solutions** (no such pages exist) and **Developers** (the API reference is staff-only by design, see `test_api_docs_are_staff_only`, so a public link would be a dead end). No language switcher: there is one language. |
| Needs the owner | Home page copy, a comparison table and FAQ text: these are written in the templates today; making them editable from Setup is a separate step once the owner supplies the content. |
