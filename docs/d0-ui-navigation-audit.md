# Phase D0 Gap Report: UI & Navigation Audit

**Date:** 2026-09-25
**Baseline:** Phases 01-14 complete (`211a3bb`); roadmap v2 (`new_phase.md`) adopted
**Method:** inspection only (Rule 1), using four tools built for the audit: a route inventory read from Django's URL resolver (now `manage.py route_inventory`), a first link crawler (8 roles, 3,000+ pages), a permission matrix (68 pages x 5 roles), a hostile-query sweep (3,588 requests) and a browser pass (one page per route, 3 roles, 375 and 1280 px, headless Chromium).

Full route table: [route-inventory.md](route-inventory.md) (414 routes: 157 staff, 54 customer, 18 public, 184 API).

## FOUND (exists and works)

- **Link health is already good.** The first crawler followed every internal link as anonymous, customer owner, billing contact, technical contact, support agent, manager, admin and super admin: **no 404, no 500 and no 403 on any link a page displayed.** The menu already agrees with permissions (Rule 5.6): support agents get 403 on 20 staff pages, and none of them is linked to them.
- **Rule 5.1 is nearly met.** Across 118 templates there is exactly one hand-written internal link (the brand link), no `href="#"`, no empty `href`, no `javascript:` link and no hand-written form action. Emails build every link as `{{ site_url }}{{ link }}` with `link` coming from `reverse()`; PDFs contain no links.
- **Authorization holds on direct URLs.** Requesting every argument-free page as five roles gave **no 500 anywhere**; anonymous users are redirected to sign-in on every protected page, customers get 403 on all 44 staff pages.
- **The browser pass was almost clean:** no horizontal overflow, no JavaScript errors, no failed requests, and exactly one `<h1>` on every page checked (69 pages x 2 sizes x 3 roles).
- **Brand and trademark:** the word "WHMCS" appears nowhere in templates, styles or code (Rule 6).
- **Reusable UI pieces are already shared:** one form include used by 40 templates, one pager used by 19, one client picker used by 3 (all staff "create on behalf" flows), one page-header pattern on 40 pages, one status-badge style, `messages` rendered once in the base template.
- **Staff screens exist for nearly all daily work** (products, addons, servers, domains, TLD pricing, hosting, orders, the whole billing area including payment methods, tax rules, coupons and settings, support, affiliates, lifecycle, reports, email log); see the mapping tables below.

## PARTIAL (exists but incomplete or inconsistent)

1. **One layout for everything.** All 100 page templates extend a single `base.html` (public, customer and staff alike). v2 wants four shells (public, client, staff, document) plus an email layout.
2. **Menus are hand-written HTML** in `base.html`: 27 hard-coded entries and 19 permission or ownership tests written out by hand (11 `perms.accounts` checks, 8 `client_contacts.exists` checks), in one flat bar of up to 17 links (a manager sees Plans, Domains, Help, Products, Clients, Domain Admin, Hosting Admin, Reports, Affiliates, Lifecycle, Orders, Billing, Support, Notifications, name, Admin, Sign out). No registry, no grouping, no active-state, no badges except the notification count. (Rule 5.3)
3. **Styling is one 172-line stylesheet** written from scratch: no framework, no design tokens (colours repeated as literals), 37 hand-written `status-*` rules, 4 media queries and **no `:focus` styles at all**. 53 templates colour badges by hand-typing `class="badge status-<value>"`; a status with no rule silently renders uncoloured. (v2: one badge component; WCAG focus.)
4. **htmx is loaded from a CDN but effectively unused** (two mentions in the whole site: the CSRF header on `<body>` and the script tag). v2 forbids a runtime CDN dependency and wants HTMX for filters, inline updates and widgets.
5. **Error pages are Django's bare defaults** (see BROKEN): no template, no brand, no navigation.
6. **Emails have no shared HTML layout:** the HTML part is generated from the plain text with inline styles (Phase 11). It works; it is not a branded `layouts/email.html`.
7. **Brand is configuration only in part:** `SITE_NAME` and `SITE_URL` come from environment variables; company name/address/footer live in Billing settings; there is no logo, favicon, primary colour or footer text setting, and the stylesheet hard-codes the accent colour. (Rule 6: brand is configuration.)
8. **Path prefixes differ from v2 Section 12.1** (customer area `/account/` not `/client/`; login and register under `/account/`; store at `/products/`, knowledgebase at `/help/`).
9. **Money, dates and IDs are formatted in views and model properties** (strings like `"USD 100.00"`), not by shared template filters; date and currency formats are not settings-driven.
10. **No breadcrumbs anywhere;** back-links are hand-written "&larr; ..." paragraphs (41 of them). No sidebar panels; filters are plain GET forms (which is exactly what makes them bookmarkable).
11. **Accessibility:** ten form fields have no label (status selects on product and server edit pages, the three quantity/price/description fields x 3 rows on the invoice and quote forms, the cancel-reason field on the invoice page); the `:focus` gap above; no i18n (no UI string is wrapped for translation).
12. **Static files:** default storage, not `ManifestStaticFilesStorage`, so a CSS reference to a missing file would not fail `collectstatic`.
13. **`/staff/billing/invoices/create/` and `/quotes/create/` needed `?client=`** and returned 404 when typed or bookmarked without it (fixed, below).

## BROKEN (dead links, 404/500, 403-after-link, JS errors, overflow)

Found and **fixed in D0 with regression tests** (Section 28: "fix immediately"):

| # | Defect | Fix |
|---|---|---|
| 1 | **Four server errors from a non-numeric `client` in the address:** `/staff/billing/invoices/create/`, `/staff/billing/quotes/create/`, `/staff/orders/new/`, `/staff/support/tickets/new/` returned **500** for `?client=abc` (and `-1`, or a 30-digit number), found by the hostile-query sweep | One shared helper (`core.web.query_id`): a query id is a sane positive integer or "not given". The two create pages now send you to the client chooser; the chooser pages show the chooser |
| 2 | Bare `/staff/billing/invoices/create/` and `/quotes/create/` gave a plain **404** | Redirect to the chooser (`invoice_new` / `quote_new`) |
| 3 | The brand link was a hand-typed `href="/"` and the home route had **no URL name** (`/` was the only unnamed route) | Route named `home`; the brand link uses `{% url 'home' %}` |
| 4 | Three internal URLs hand-built in Python: the email-failure alert link (`/staff/notifications/emails/?status=failed`), the email open-tracking image (`/e/o/<token>.gif`) and the payment-webhook address shown in Django admin | All three now come from `reverse()` |

Still broken, **scheduled** (not fixable as a one-line bug):

- **Unbranded error pages.** 404 is "Not Found / The requested resource was not found on this server." (179 bytes, no links); 403 is "403 Forbidden" with an empty paragraph (135 bytes). No navigation, no way back (Rule 5.8). There is no 500 template either. -> D1 (shell) then D2 (crawler-tested).
- **No customer navigation link to *Cancellation requests*** (the list is reachable only from the cancel card on a service page and from the notification links). -> D3.
- **Ten unlabelled inputs** (PARTIAL 11). -> D3/D4 (they are in forms being re-skinned).

Not found: no dead internal link, no 403-after-link, no JavaScript error, no overflow.

## MISSING (from Sections 10 and 11 of roadmap v2)

**Customer area (Section 10)**

| Item | State |
|---|---|
| Logged-out menu: Home, Store, Knowledgebase, Affiliates, Contact Us, Login, Register, Cart | Store (`Plans`), Knowledgebase (`Help`), Login, Register **exist**; **Home** (a public landing page; `/` redirects to sign-in), public **Affiliates** sign-up page and **Contact Us** are **missing**; Cart requires sign-in (guest carts deferred) |
| Logged-in menu structure (Home, Services, Domains, Billing, Support, Open Ticket, Affiliates, account dropdown, Cart, bell) | Every destination exists except those listed below, but as one flat bar, not these menus |
| **Client-area dashboard** (welcome, stat tiles, overdue banner, active services, tickets, domain search, affiliate panel) | **Missing.** `/` redirects to the profile page; the nearest thing is the client record page |
| **Sidebar panels** (Your Info, Contacts, Shortcuts, View-by-status with counts, Actions) | **Missing** |
| Service details with tabs (Overview, Information, Addons, Upgrade, Cancellation) | **Partial:** one page with cards (term/billing, lifecycle/cancel); upgrade is a separate page; no tabs |
| Domain details with tabs (Overview, Auto Renew, Nameservers, DNS, Lock, Renew) | **Partial:** one long page, no tabs |
| View Available Addons | **Missing** as a page (addons appear only inside checkout) |
| Renew Domains (bulk) / Transfer Domains to Us (menu entry) | **Partial:** renew per domain; transfer added from the domain search page |
| My Quotes | **Exists** |
| Payment Methods (customer page) | **Missing** (instructions show on the invoice and checkout) |
| Contacts (customer manages) | **Partial:** shown on the client page; staff manage contacts; customer self-service is a deferred item |
| Security Settings (2FA) | **Missing** (Phase 16) |
| Email History (customer) | **Missing** (staff have the email log) |
| Notification Preferences | **Exists** (linked from the inbox, not from an account menu) |
| Invoices/Quotes/Tickets/Domains/Services list filters by status with counts | **Partial:** filters exist as tabs/selects, without counts |
| Announcements, Network Status, Downloads, Add Funds | Deferred by v2 (hidden) |

**Staff area (Section 11)**

| Item | State |
|---|---|
| Grouped top navigation (Clients, Orders, Billing, Support, Reports, Utilities, Setup gear, global search, staff menu) | **Missing** as a structure. A flat bar today; **Utilities** (Activity/Audit Log, Job Queue, WHOIS) and **Setup** (settings gathered in one gear menu) do not exist as menus |
| **Staff dashboard** (income, orders, support, automation, failures, expiring, renewals, health, activity) | **Missing.** The nearest thing is the *Reports* page headline figures (Phase 14) |
| **Global staff search** | **Missing** (each list has its own search box) |
| Client profile with 15 tabs | **Partial:** one long page with the same sections (summary, contacts, records incl. cancellations and affiliate, notes...), no separate tab URLs; no "Login as Client" (Phase 16 decision) |
| Staff & role management UI | **Missing** (Django admin only) |
| Payment providers (gateways), registrar provider, email provider (SMTP) setup | **Missing** (Django admin only) |
| Audit / activity log viewer | **Missing** (Django admin only) |
| System health, job queue, maintenance mode | **Missing** (Phase 15/17) |
| Standard list pattern (search + filters + bulk actions + "showing X-Y of Z") | **Partial:** search and filters everywhere, pagination with "Page x of y"; no bulk-action bar, no "showing X-Y of Z" |
| Pending / Active / Fraud / Cancelled order lists | **Exists** (filter tabs on the orders list) |
| Accept Order with per-line checkboxes | **Partial:** fulfilment is automatic on payment, with retry per line; no accept-with-options screen |
| Offline payment recording (amount, account, transaction ID, date) | **Exists** (Phase 14 follow-up) |
| Support reply / note / predefined reply / re-assign in one screen | **Exists** |
| Service actions with confirmation modal and log entry | **Partial:** buttons and flash messages, audited; no confirmation modal (Phase 16) |

**Staff screens that still require Django admin for daily work:** staff users and roles, payment providers, registrar provider, email provider, audit log. (Everything else in the setup list has a staff screen.)

## REUSE (keep and promote to shared components)

`partials/form.html` (the form renderer), `billing/_pager.html` (pagination; move to `partials/`), `billing/staff/client_picker.html`, the `page-head` header pattern, the `.stats`/`.stat` tiles (become the stat-tile component), `.table-wrap` + `.table` (data table), `.tabs` (filter tabs) and `.subnav` (section navigation used by five staff areas), `.badge status-*` (becomes the single status-badge tag), the `messages` block, the lifecycle `{% lifecycle_card %}` inclusion tag as the model for tab/sidebar inclusion tags. Emails: `notifications.dispatch` and the event registry stay; only the HTML shell changes.

## ROUTES

Full inventory: [route-inventory.md](route-inventory.md) (regenerate any time with `python manage.py route_inventory`). Summary: 414 routes, **all now named**; 115 are POST-only actions; 184 API (`/api/v1/`); customer area `/account/...`; staff `/staff/...`; public `/`, `/products/`, `/domains/`, `/help/`, `/cart/`, `/checkout/`, `/r/<code>/`, `/e/o/<token>.gif`.

**Proposed handling of v2 Section 12.1 (no URL name is renamed; deviations are decisions for D1-D3):**

| v2 target | Today | Proposal |
|---|---|---|
| `/login/`, `/register/`, `/password/...` | `/account/login/`, `/account/register/`, `/account/password-reset/...` | Keep `/account/...` canonical (verification and reset links already sent point at it and must keep working); add short redirects `/login/`, `/register/` |
| `/client/...` | `/account/...` | Keep `/account/...` as the customer-area prefix (renaming ~54 routes and every link already sent buys nothing but cosmetics); record the deviation in Section 27 |
| `/store/` | `/products/` | Keep; add `/store/` redirect |
| `/knowledgebase/` | `/help/` | Keep; add `/knowledgebase/` redirect |
| `/staff/...` | `/staff/...` | Matches. Add a staff home (`/staff/`, the dashboard, currently unrouted) |
| Django admin `/admin/` | `/admin/` | Matches; reserved for Super Admin once the staff UI covers the five admin-only areas |

Unlinked pages: `/e/o/<token>.gif` (tracking pixel, by design). Pages that no role could click through to **with the audit dataset** were all data-dependent (no quotes, knowledgebase articles, addons or affiliate commissions in the dataset), not orphans; the D2 fixture must include one object in every status so the crawler proves them.

## CHANGES (exact D1 and D2 work)

**D1 - Design system and layout shell**

1. Adopt **Bootstrap 5 + one icon set, vendored** into `static/` (there is no framework to keep), with our tokens on top (brand colour from settings), one stylesheet entry point; remove the runtime CDN reference (htmx vendored too).
2. Build the shells `layouts/public.html`, `client.html`, `staff.html`, `document.html`, `email.html`; make `base.html` a thin alias until every template is moved; **no URL name, service or API contract changes**.
3. Components: navbar (3 variants), sidebar panel, stat tile, data table with filter bar, detail header, tabs (each tab its own URL), form include (labels, help, errors, **fixing the ten unlabelled fields**), confirmation modal, messages, empty state, breadcrumbs, money/date/ID filters (format from settings), pager, status badge **tag** replacing the 53 hand-coloured badge uses, focus styles, i18n-wrapped strings.
4. Brand settings (name, logo, favicon, primary colour, support email, footer text) in the database, edited in Setup.
5. Branded 403/404/500 templates (500 static, no database) and a maintenance page.

**D2 - Link-integrity harness (the crawler is the CI gate)**

1. `apps/core/navigation.py` menu registry (key, label, url name, permission, area, parent, feature flag, badge) generating every menu; registry test reverses every entry.
2. Promote the tools built for D0 into tests: the **link crawler** (roles: anonymous, customer owner, billing contact, technical contact, support agent, manager, admin, super admin; fixture with one object per status; follows `href`, GET `action`, `hx-get`, `src`, `<link>`; fails on 404, 500 or a 403 the page displayed), the **template lint** (its first version already runs: `test_templates_have_no_placeholder_or_hand_written_internal_links`), the hostile-query sweep (already runs), the email-link test, the PDF-link test, the static-asset test (`ManifestStaticFilesStorage`), the redirect test and the safe-`next` test.
3. Add the vanity redirects (`/login/`, `/register/`, `/store/`, `/knowledgebase/`) and the redirect test.

**D3 / D4** then re-skin and re-organise using the mapping tables above (MISSING lists are the build lists).

## TEST PLAN

- **Roles:** anonymous, customer owner, billing contact, technical contact, support agent, manager, admin, super admin (all eight already exercised by the D0 crawler).
- **Fixture dataset:** at least one object in every status for orders, invoices, quotes, transactions, tickets, hosting, domains, cancellation requests, commissions and payouts, plus knowledgebase articles, an addon, a coupon and an email log with a failed message. (D0's dataset lacked quotes, articles, addons, customer-visible cancellations and commissions.)
- **Lint rules:** no `href="#"`, empty `href`, `javascript:`, or hand-written internal path (`href`, `action`, `src`, `hx-*`); every `{% url %}` name exists; no menu written outside the registry.
- **Standing sweeps:** hostile query strings on every page (already in CI), permission matrix per role, browser pass at 375 / 768 / 1280 px for overflow and console errors.

## EXIT (what must be true before D0 -> D1 begins)

| Criterion | State |
|---|---|
| Route inventory produced; every route has a name | ✅ 414 routes |
| Menus, hard-coded links, error pages, admin-only work, layouts and CSS inventoried | ✅ |
| Every screen mapped to Sections 10 and 11 (exists / partial / missing) | ✅ |
| BROKEN items either fixed with regression tests or scheduled with an owner phase | ✅ (4 fixed; 3 scheduled) |
| The four "Proposed" decisions in Section 27 confirmed or adjusted | ✅ confirmed as Active, with the path-prefix adjustment above |
| Full suite green on SQLite and PostgreSQL; CI green | see below |

## Fixed in this phase (regression tests)

`apps/core/test_link_integrity.py` (21 tests): the query-id helper; the four pages against junk and bare `client=` values; a sweep of **every argument-free page with 14 hostile query strings** (700+ requests, no 500 allowed); the template lint; the named home link; the three formerly hand-built URLs; every route has a name; the inventory command. Mutation check: with the query-id guard removed 11 of these tests fail.
