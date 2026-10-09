# Hosting Management Portal

## Master Development Roadmap & Engineering Specification — v2

**Project:** Independent WHMCS-style Hosting Business Management Platform
**Primary Goal:** A production-ready platform for running a real hosting business.
**Experience Goal:** Staff and customers should feel they are using WHMCS — same workflows, same menu structure, same screen layouts — built entirely with our own code, templates and branding.
**Current Focus:** Hosting management portal (customer area + staff area + API).
**Future Client Strategy:** Web and mobile clients consume the same API/business layer.
**Control Panel Scope:** cPanel/WHM is an external provider/integration. This project does not build a server control panel.

------------------------------------------------------------------------

## What changed in v2

| Change | Where |
|---|---|
| Rule 5 (Link Integrity) and Rule 6 (WHMCS Parity) added | Section 00 |
| AI-agent session protocol added | Section 00 |
| New Design & Navigation track (D0–D5) with full WHMCS-parity screen specs | Sections 08–13 |
| Route map, menu map and link-integrity test harness specified | Sections 11–12 |
| Phases 14–19 expanded with UI and link requirements | Phase 14–19 |
| Phase Completion Checklist now includes UI, navigation and link checks | Section 24 |
| State tracker includes the design track and the recommended order | Section 25 |
| Stale deferred items (target phase already complete) flagged for re-triage | Section 26 |
| Section 28 ("Current Starting Task") updated — Phase 01 audit is long done | Section 28 |
| All tables converted to clean Markdown | Throughout |

No completed-phase decision, deferred item or architecture decision was removed or changed in meaning.

**Adopted 2026-09-25** as the single roadmap (it replaces `Hosting_Management_Portal_Master_Development_Roadmap.md`, whose content is merged here). Merged in when adopted: Phase 14 (Reports) was already complete, and Phase D0 has been run (Sections 25-28 updated).

------------------------------------------------------------------------

# 00 — READ THIS FIRST

This document is the **single development roadmap and source of truth** for the project. Every developer or AI agent must follow these rules.

## Rule 1 — Inspect Before Implementing

Before changing code:

1. Inspect the repository structure.
2. Search for the requested feature.
3. Inspect related models, services, views, URLs, templates and APIs.
4. Check existing tests.
5. Decide whether the feature is complete, partial, broken, or genuinely missing.
6. Only then decide what code needs to change.

**Never assume a feature is missing because it was requested.**

## Rule 2 — Never Repeat Completed Work

A feature that is implemented, tested, verified and documented is part of the permanent baseline. It must not be rebuilt in a later session. A later session may touch it only when:

- a new requirement explicitly changes its behaviour,
- a regression exists,
- a security issue exists,
- a provider/API dependency requires a change,
- another feature genuinely requires an integration change,
- **or the Design track (D-phases) re-skins its templates** — re-skinning changes presentation only, never business logic, URLs names or API contracts.

## Rule 3 — One Implementation Only

Never duplicate models, tables, API endpoints, services, permission systems, notification systems, billing calculations, provider clients, background-job systems, **templates, layouts, menus or UI components**. Search first; reuse.

## Rule 4 — API-First Architecture

```text
                    WEB APP
                       |
                 REST API / BFF
                       |
                       v
              BUSINESS / SERVICE LAYER
                       |
        +--------------+--------------+
        |              |              |
   PostgreSQL        Redis       External APIs
                                   |
                         +---------+---------+
                       Domain     WHM      Payment
```

The backend is **Django + Django REST Framework**. FastAPI is not needed for mobile readiness. The business/service layer never depends on the page or endpoint that called it.

```text
                 SAME API
                    |
          +---------+---------+
        Web                Mobile
     Django/HTMX     Flutter/React Native
```

## Rule 5 — Link Integrity (No Broken Links, Ever)

A broken link is a failed build, not a cosmetic issue.

1. **Every internal link is generated from a URL name** — `{% url %}` in templates, `reverse()` in Python, `reverse()`-built absolute URLs in emails and PDFs. No hand-written internal paths such as `href="/client/invoices/"`.
2. **No placeholder links.** `href="#"`, `href=""`, `href="javascript:void(0)"` and "Coming soon" pages are forbidden. A feature that does not exist yet is **hidden** from menus, not linked.
3. **Menus are data, not markup.** Every menu (customer, staff, account dropdown, sidebars, footer) is built from one menu registry (Section 12). Each entry has a URL name and a permission; the menu renders only entries the current user may open.
4. **URLs are stable.** Never rename an existing URL name. If a path must change, keep the name and add a permanent (301) redirect from the old path. Links already sent in emails and invoices must keep working forever.
5. **Every link is tested.** The link crawler (Section 12) visits every page as every role and fails CI on any internal 404, 500, or unexpected 403.
6. **Permission and link agree.** If a user can see a link, they can open it. If they cannot open it, they do not see it.
7. **Object links survive state changes.** Detail pages for cancelled, terminated, refunded or closed records still open (read-only where appropriate), because old emails and invoices point at them.
8. **Error pages are designed.** Branded 403, 404, 500 and maintenance pages exist, and each offers a working way back (dashboard, search, support).

## Rule 6 — WHMCS Parity, Own Implementation

The portal must feel like WHMCS: the same information architecture, menu names, screen layouts, list/filter/action patterns and workflows that hosting staff and customers already know.

- **Copy the experience, not the product.** Do not copy WHMCS source code, templates, CSS, images, icons or logos, and do not use the name "WHMCS" anywhere in the UI. WHMCS is licensed commercial software and a trademark. Write our own templates and styles.
- **Brand is configuration.** Company name, logo, favicon, primary colour, support email and footer text come from settings, never hard-coded.
- **Parity never overrides architecture.** If a WHMCS pattern conflicts with a rule in this document (for example client-side price calculation), this document wins.
- The parity target is described screen by screen in Sections 09–11.

## AI Agent Session Protocol

**At the start of every session:**

1. Read Sections 00, 25, 26, 27 and 28.
2. Identify the one phase/task being worked on.
3. Perform Rule 1 inspection for that task only.

**At the end of every session:**

1. Run the relevant tests plus the link crawler.
2. Update Section 25 (tracker), Section 26 (deferred items) and Section 27 (decisions) if anything changed.
3. Record exactly what was done, what was verified, and what remains.
4. Commit only after verification. Never deploy without explicit approval.

------------------------------------------------------------------------

# 01 — TECHNOLOGY STACK

## Backend

| Technology | Role |
|---|---|
| Python | Primary language |
| Django | Auth, users, permissions, ORM, transactions, business orchestration, server-rendered web app, configuration, internal admin |
| Django REST Framework | Customer API, mobile API, webhooks, integrations, async operation status, API auth and permissions |

Never build business logic inside serializers or API views.

```text
API View / Web View
   ↓
Serializer / Form (validation)
   ↓
Service Layer
   ↓
Domain / Business Logic
   ↓
Repository / ORM
   ↓
PostgreSQL
```

------------------------------------------------------------------------

# 02 — DATA & INFRASTRUCTURE

**PostgreSQL** — primary relational database. Major entities: Users, Clients, Products, Product Prices, Addons, Servers, Providers, Orders, Order Items, Services, Domains, Invoices, Invoice Items, Transactions, Quotes, Disputes, Tickets, Affiliates, Commissions, Notifications, Audit Events.

**Redis** — Celery broker, caching, short-lived state, rate limiting, job coordination. Never the permanent source of truth.

**Celery** — provisioning, domain registration/renewal, service renewal jobs, invoice reminders, expiry checks, email delivery, notifications, provider sync, report generation, retryable external API operations.

------------------------------------------------------------------------

# 03 — WEB TECHNOLOGY

| Technology | Use |
|---|---|
| Django Templates | Primary UI: customer area, staff area, support, billing, reports |
| HTMX | Filters, search, inline updates, modals, pagination, status changes, notifications |
| JavaScript | Only where client-side interaction genuinely improves UX. Never business rules |
| CSS framework | See Section 09 (design system). Assets are served from our own static files, no runtime CDN dependency |

------------------------------------------------------------------------

# 04 — SERVER / DEPLOYMENT STACK

```text
Internet → Nginx → Gunicorn → Django
                                 ├── PostgreSQL
                                 └── Redis → Celery (worker + beat) → External Providers
```

Linux, Docker, Docker Compose, Nginx, Gunicorn, PostgreSQL, Redis, Celery Worker, Celery Beat.

------------------------------------------------------------------------

# 05 — EXTERNAL INTEGRATION ARCHITECTURE

Provider-specific code lives only in the integration/adapter layer.

```text
Domain Service   → DomainProvider   → Registrar Adapter → Registrar API
Hosting Service  → HostingProvider  → WHM Adapter       → WHM / cPanel API
Payment Service  → PaymentProvider  → Gateway Adapter   → Payment Gateway
Business Event   → Notification Svc → Email Service     → Configured Email Provider
```

------------------------------------------------------------------------

# 06 — CORE BUSINESS MODEL

Keep these concepts separate. Never merge them to reduce model count.

```text
CLIENT
  ├── ORDER ──── ORDER ITEM
  ├── INVOICE ── INVOICE ITEM
  ├── TRANSACTION
  ├── SERVICE ── HOSTING SERVICE / DOMAIN SERVICE
  ├── SUPPORT TICKET
  └── AFFILIATE
```

| Concept | Meaning |
|---|---|
| Order | Commercial request/purchase |
| Invoice | Financial document |
| Transaction | Payment event |
| Service | Active purchased/provisioned service |
| Domain | Domain registration lifecycle |

------------------------------------------------------------------------

# 07 — PHASE STATUS SYSTEM

⬜ Not Started · 🟡 In Progress · 🔵 Verification · 🟢 Complete · 🔴 Blocked

A phase becomes 🟢 only after implementation, tests, browser verification, **link-crawler pass** and documentation are finished.

------------------------------------------------------------------------

# 08 — DESIGN & NAVIGATION TRACK (OVERVIEW)

Phases 01–13 built the business engine. The Design track makes it look and behave like a finished WHMCS-class product. It is a **presentation track**: it re-skins and re-organises existing templates and navigation. It does not change services, models, API contracts or URL names.

| Phase | Name | Output |
|---|---|---|
| D0 | UI & Navigation Audit | Gap report: templates, routes, menus, dead links, inconsistent components |
| D1 | Design System & Layout Shell | Tokens, components, base layouts for public, customer and staff areas |
| D2 | Link Integrity Harness | Menu registry, link crawler, template lint, CI gate |
| D3 | Client Area Parity | Every customer screen in Section 10 on the new shell |
| D4 | Staff Area Parity | Every staff screen in Section 11 on the new shell (replaces Django admin for daily operations) |
| D5 | Visual QA & Polish | Responsive, accessibility, empty/error states, print/PDF, screenshots |

D0 → D1 → D2 must be done before D3/D4, so every re-skinned screen is born inside the link-tested shell.

------------------------------------------------------------------------

# 09 — DESIGN SYSTEM (PHASE D1)

## 9.1 Framework

- Inspect first: if the project already uses a CSS framework, keep it.
- If none exists, use **Bootstrap 5** (WHMCS's own themes are Bootstrap-based, so layout conventions match naturally) plus **one** icon set (Bootstrap Icons or Font Awesome Free), both vendored into static files.
- One stylesheet entry point with our tokens on top. No per-page ad-hoc CSS.

## 9.2 Design Tokens (CSS custom properties)

| Token group | Content |
|---|---|
| Brand | `--brand-primary`, `--brand-primary-dark`, `--brand-accent` — from settings |
| Neutrals | page background, panel background, borders, muted text, body text |
| Status | success, warning, danger, info, neutral (used only through the status badge component) |
| Typography | one sans-serif family for UI, one monospace for IDs/code/IPs; fixed scale (12/14/16/20/24/30) |
| Spacing | 4-px base scale |
| Radius / shadow | panel, button, input, modal |
| Layout | content max width, sidebar width, navbar height |

## 9.3 Status Badge Map (one component, used everywhere)

| Colour | Statuses |
|---|---|
| Green (success) | Active, Paid, Resolved, Approved, Completed, Registered |
| Amber (warning) | Pending, Pending Payment, Processing, Provisioning, Partially Paid, Customer-Reply, Renewal Due, Grace |
| Red (danger) | Unpaid, Overdue, Fraud, Failed, Suspended, Rejected, Expired |
| Blue (info) | Open, Answered/Agent Reply, In Progress, Pending Transfer, Draft |
| Grey (neutral) | Cancelled, Terminated, Closed, Refunded, Collections |

Text label is always shown; colour is never the only signal. Badges are rendered by one template tag (e.g. `{% status_badge obj %}`), never hand-coloured.

## 9.4 Component Library

Build once, reuse everywhere:

- **Navbar** (public, customer, staff variants) with account dropdown and notification bell.
- **Sidebar panels** — WHMCS-style boxed panels with title bar and link list (Section 10).
- **Stat tiles** — icon, number, label; whole tile is a link to the filtered list.
- **Data table** — search box, filter bar (HTMX), sortable columns, status badges, row actions, bulk-action bar, pagination, "showing X–Y of Z", horizontal scroll container on small screens.
- **Detail page header** — title, status badge, key facts row, primary action buttons.
- **Tabs** — used for client profile and service/domain details; each tab is its own URL (deep-linkable, crawler-testable).
- **Forms** — label, help text, inline errors, required markers, disabled-state rules; one form-rendering include.
- **Modals** — HTMX-loaded; confirmation modal for sensitive actions (required by Phase 16).
- **Alerts/toasts** — Django messages rendered in one place.
- **Empty states** — message plus one working action (e.g. "No services yet — Order New Services").
- **Breadcrumbs** — on every page below top level.
- **Money, date and ID formatters** — template filters; currency and date format from settings.
- **Pagination**, **loading indicator**, **timeline/activity feed**, **invoice/quote document layout**.

## 9.5 Layout Shells

| Shell | Used for | Structure |
|---|---|---|
| `layouts/public.html` | Store, domain search, knowledgebase, login/register | Top navbar, content, footer |
| `layouts/client.html` | Customer area | Top navbar, breadcrumb, left sidebar panels + main content (sidebar collapses under content on mobile) |
| `layouts/staff.html` | Staff area | Dark top navbar with WHMCS-style menus, global search, right-side quick links, main content, optional right context panel |
| `layouts/document.html` | Invoice, quote, printable views | Print-optimised, no navigation |
| `layouts/email.html` | HTML emails | Table-based, inline CSS, brand header, footer links |

## 9.6 Standards

- Responsive at 375, 768, 1024, 1280, 1440 px. No page-level horizontal overflow.
- WCAG 2.1 AA contrast, visible focus, keyboard-reachable menus and modals, labelled form fields.
- No JS errors in console on any page.
- i18n-ready: all UI strings wrapped for translation; layout does not break with longer strings. RTL is deferred (Section 26).

------------------------------------------------------------------------

# 10 — CLIENT AREA PARITY SPEC (PHASE D3)

Menu names follow WHMCS so customers feel at home. Entries whose feature does not exist yet are **hidden** (Rule 5.2) — shown here in *italics* with their status.

## 10.1 Top Navigation — Logged Out

Home · Store · Knowledgebase · Affiliates · Contact Us · Login · Register · Cart
*Announcements, Network Status — not built (Post-MVP), hidden*

## 10.2 Top Navigation — Logged In

| Menu | Items |
|---|---|
| Home | Client area dashboard |
| Services | My Services · Order New Services · View Available Addons |
| Domains | My Domains · Renew Domains · Register a New Domain · Transfer Domains to Us · Domain Search |
| Billing | My Invoices · My Quotes · Payment Methods · *Add Funds / Mass Payment (credit balances deferred — hidden)* |
| Support | Tickets · Knowledgebase |
| Open Ticket | New ticket |
| Affiliates | Affiliate dashboard (or sign-up page if not an affiliate) |
| Account dropdown ("Hello, Name") | Your Profile · Contacts · Change Password · Security Settings · Email History · Notification Preferences · Logout |
| Right side | Cart (item count) · Notification bell |

## 10.3 Client Area Dashboard

```text
+------------------------------------------------------------------------+
| Welcome back, Name                                   [breadcrumb]      |
+------------+-----------------------------------------------------------+
| YOUR INFO  |  [ Services 4 ] [ Quotes 1 ] [ Tickets 2 ] [ Invoices 1 ] |
| name       |                                                           |
| company    |  ! You have 1 overdue invoice (Rs X). [Pay Now]           |
| address    |                                                           |
| [Update]   |  YOUR ACTIVE PRODUCTS/SERVICES          [View All]        |
+------------+  product · domain · status · next due · [Manage]          |
| CONTACTS   |                                                           |
| list       |  RECENT SUPPORT TICKETS                  [Open New]       |
| [New]      |  #id subject · status · last reply                        |
+------------+                                                           |
| SHORTCUTS  |  REGISTER A NEW DOMAIN  [ domain search box ] [Search]    |
| Order      |                                                           |
| Domain     |  AFFILIATE PROGRAM  balance · referrals · [View]          |
| Logout     |                                                           |
+------------+-----------------------------------------------------------+
```

Every tile, panel title and "View All" is a link to the matching filtered list.

## 10.4 Customer Screens

| Screen | Key content | Built in |
|---|---|---|
| My Services (list) | product, pricing, next due, status; filter by status | 05/09 |
| Service details | tabs: Overview, Information (server, IP, nameservers, username), Addons, Upgrade/Downgrade, Request Cancellation; cPanel login link where supported | 05/08/12 |
| My Domains (list) | domain, registration date, next due, auto-renew, status; bulk renew | 04 |
| Domain details | tabs: Overview, Auto Renew, Nameservers, DNS, Registrar Lock, Renew | 04 |
| Domain search / register / transfer | search result list with price per TLD, "Add to Cart" | 04/06 |
| Store / product group | product cards with cycle selector and price from server | 03/06 |
| Configure product | cycle, domain choice, addons, running summary (server-calculated) | 06 |
| Cart / Checkout | lines, coupon, tax, total, payment method, terms | 06 |
| My Invoices | number, date, due, total, status; filter Unpaid/Paid/Overdue | 07 |
| Invoice view | printable document, payment instructions per method, "report payment", PDF download | 07 |
| My Quotes | list, view, accept, download PDF | 07 |
| Tickets | list with department, subject, status, last updated | 10 |
| Ticket view | conversation thread, attachments, reply box, related service/domain, close ticket | 10 |
| Open Ticket | choose department → form | 10 |
| Knowledgebase | categories, article list, search, article view | 10 |
| Affiliates | referral link, clicks/sign-ups, commissions by status, payout history | 13 |
| Profile / Contacts / Password / Security | edit forms; contacts with roles | 01/02 |
| Email History | sent emails list and view (secret-bearing emails excluded) | 11 |

## 10.5 Customer Area Sidebars (context-aware)

| Page | Sidebar panels |
|---|---|
| Dashboard | Your Info, Contacts, Shortcuts |
| Services list | View (filter by status with counts), Actions (Order New, View Addons) |
| Service details | Overview (product, status), Actions (Upgrade, Request Cancellation, Open Ticket) |
| Domains list | View (by status), Actions (Renew, Register, Transfer) |
| Domain details | Manage (Overview, Auto Renew, Nameservers, DNS, Lock) |
| Invoices | View (All, Paid, Unpaid, Overdue with counts), Billing (Quotes, Payment Methods) |
| Tickets | View (by status with counts), Support (Open Ticket, Knowledgebase) |

------------------------------------------------------------------------

# 11 — STAFF AREA PARITY SPEC (PHASE D4)

The staff area lives at its own prefix (not `/admin/`, which stays Django admin for Super Admin emergency use only). Daily operations must never require Django admin.

## 11.1 Top Navigation

| Menu | Items | Built in |
|---|---|---|
| Clients | View/Search Clients · Add New Client · Products/Services · Domain Registrations · Cancellation Requests · Manage Affiliates | 02/04/05/12/13 |
| Orders | List All Orders · Pending Orders · Active Orders · Fraud Orders · Cancelled Orders · Add New Order | 09 |
| Billing | Transactions List · Invoices · Billable Items · Quotes · Offline Payments (pending verification) | 07 |
| Support | Support Overview · Support Tickets · Open New Ticket · Predefined Replies · Knowledgebase | 10 |
| Reports | Reports index (Phase 14) | 14 |
| Utilities | Activity Log · Audit Log · Email Message Log · Provisioning/Module Log · Job Queue status · WHOIS lookup | 11/15/17 |
| Setup (gear icon) | General Settings · Staff & Roles · Products/Services · Product Addons · Domain Pricing · Servers · Payment Methods · Tax Rules · Coupons · Email Provider · Email Templates (view) · Notifications · Lifecycle Timings · Affiliate Settings · Support Departments | 01–15 |
| Right side | Global search (clients, invoices, domains, tickets, orders) · Notifications · Staff account menu |

Menu entries appear only if the staff member's `PortalAccess` allows them (Rule 5.6).

## 11.2 Staff Dashboard (widgets, each linking to its list)

Billing (today / this month / this year income, unpaid invoice total) · Orders (pending count, recent orders) · Support (open / awaiting reply / unassigned tickets) · Automation (last run of each Celery beat job, failures) · Provisioning Failures · Expiring Domains (30 days) · Services Due for Renewal · System Health (DB, Redis, Celery workers, email provider, registrar, WHM) · Recent Activity · Staff Online.

Widgets load via HTMX so one slow widget never blocks the page.

## 11.3 Client Profile (the most important staff screen)

Header: client name, company, status badge, client ID, "Login as Client" (audited, Phase 16 decision), quick actions.

Tabs — each tab is its own URL:

| Tab | Content |
|---|---|
| Summary | Client info, contacts, invoice totals (paid/unpaid/overdue), products & services counts by status, recent emails, notes, quick actions (Add Order, Create Invoice, Open Ticket, Send Email) |
| Profile | Edit client details, status, tax-exempt |
| Contacts | Users/contacts with roles (owner/billing/technical) |
| Products/Services | List + service detail with WHM actions (Create, Suspend, Unsuspend, Terminate, Change Package), lifecycle stage, term dates |
| Domains | List + domain detail with registrar actions |
| Billable Items | Items waiting to be invoiced |
| Invoices | List, create, view |
| Quotes | List, create, view |
| Transactions | Payments and refunds |
| Tickets | Client's tickets |
| Emails | Email log for this client |
| Cancellations | Requests and decisions |
| Affiliate | Referral relationship and commissions |
| Notes | Internal staff notes |
| Log | Audit/activity events for this client |

## 11.4 Standard Staff List Screen

```text
[Title]                                              [+ Add New] [Export]
[ Search ______ ] [Status v] [Date range] [More filters]   (HTMX, URL keeps filters)
+---+--------+------------+---------+---------+----------+-----------+
| □ | ID     | Client     | Item    | Amount  | Status   | Actions   |
+---+--------+------------+---------+---------+----------+-----------+
With selected: [Accept] [Cancel] [Delete*]        Showing 1–25 of 312  « 1 2 3 »
```

Filters are stored in the query string so a filtered list is a shareable, bookmarkable, crawler-testable link.

## 11.5 Staff Workflow Parity

| Workflow | Behaviour |
|---|---|
| Accept Order | From Pending Orders: checkboxes for run provisioning, register domain, send welcome email; result shown per line |
| Offline payment | Staff records amount, account, transaction ID, date received (decision 2026-09-25) |
| Add New Order | Choose client (with search), products, cycle, domain, promo; price from engine |
| Support reply | Reply, internal note, predefined reply inserter, change department/priority/status/assignee in one screen |
| Service actions | Buttons with confirmation modal, result toast, entry in client Log |

------------------------------------------------------------------------

# 12 — NAVIGATION, ROUTES & LINK INTEGRITY (PHASE D2)

## 12.1 URL Rules

- Existing URL **names** are permanent. Inspect all `urls.py` in D0 and produce the route inventory before changing any path.
- Target prefixes (apply only via redirects if existing paths differ):

| Area | Prefix |
|---|---|
| Public site / store | `/`, `/store/`, `/domains/`, `/knowledgebase/`, `/cart/`, `/checkout/` |
| Auth | `/login/`, `/register/`, `/password/...`, `/verify-email/...` |
| Customer area | `/client/...` |
| Staff area | `/staff/...` |
| API | `/api/v1/...` |
| Webhooks | `/api/v1/webhooks/<provider>/...` |
| Django admin | `/admin/` (Super Admin only) |

- Detail URLs use stable identifiers (invoice number, ticket ID, domain ID), never positions or session state.
- Trailing slash policy consistent (`APPEND_SLASH=True`).

## 12.2 Menu Registry

One module (e.g. `apps/core/navigation.py`) defines every menu:

```python
MenuItem(
    key="billing.invoices",
    label=_("My Invoices"),
    url_name="client:invoice_list",
    permission="view_billing",      # PortalAccess area
    area="client",                  # public | client | staff
    parent="billing",
    feature_flag=None,              # hidden when the feature is off
    badge=unpaid_invoice_count,     # optional callable
)
```

- Templates render menus from the registry; no menu is hand-written in HTML.
- On startup/test, every `url_name` in the registry is `reverse()`-checked. An unresolvable entry fails the test suite.
- Active-state highlighting and breadcrumbs derive from the same registry.

## 12.3 Link Integrity Tests (all run in CI)

| Test | What it proves |
|---|---|
| Registry test | Every menu `url_name` reverses; every parent exists |
| URL reverse test | Every named URL without required args reverses; every URL with args reverses with fixture objects |
| **Link crawler** | Logs in as each role (anonymous, customer owner, billing contact, technical contact, support agent, manager, admin, super admin) with a fixture dataset containing at least one object in every status; starts from the role's home page; follows every internal `href`, `action`, `hx-get`, `hx-post` target (GET only for navigation), `src` and `<link href>`; asserts no 404 and no 500 anywhere, and no 403 on a link the page itself displayed |
| Template lint | Fails on `href="#"`, empty `href`, `javascript:` links, and hard-coded internal paths in templates (allow-list for external URLs) |
| Email link test | Renders every email template with fixtures; every link resolves and uses the configured site domain over HTTPS |
| PDF link test | Links printed in invoice/quote PDFs resolve |
| Static asset test | `collectstatic` with `ManifestStaticFilesStorage` succeeds (it fails on missing files referenced from CSS); no 404 on static in the crawler |
| Redirect test | Every retired path returns 301 to its current URL |
| Auth redirect test | Protected pages redirect to login with a safe `next` (only same-host, validated) and return after login |
| External links (non-blocking) | Weekly job checks links to knowledgebase external resources and reports, does not fail CI |

The crawler is the single most important UI test. A phase cannot be 🟢 if it fails.

## 12.4 Error Pages

403 (with "request access" or "back to dashboard"), 404 (with search box and dashboard link), 500 (static, no DB dependency), maintenance page (served by Nginx). All use the brand shell and all links on them are crawler-tested.

------------------------------------------------------------------------

# 13 — WHMCS PARITY MAP

| WHMCS area | Our module | Status |
|---|---|---|
| Client management | apps.clients / accounts | Built (02, D4 shell) |
| Products/Services, addons | apps.products | Built (03) |
| Domain registrations, TLD pricing | apps.domains | Built (04) — real registrar adapter open |
| Servers, cPanel/WHM module | apps.hosting | Built (05) — live WHM verification open |
| Cart / order form | apps.orders | Built (06, E2/E3 guest cart and redesign) |
| Invoices, quotes, transactions, tax, coupons | apps.billing | Built (07) |
| Renewals, upgrades, proration | apps.renewals | Built (08, E14 upgrade-proration revision) |
| Orders lifecycle (Pending/Active/Fraud/Cancelled) | apps.orders lifecycle | Built (09) |
| Support tickets, departments, predefined replies, KB | apps.support | Built (10) |
| Email templates, email log, notifications | apps.notifications | Built (11, E4/E10 editable/designed emails) |
| Cancellation requests, suspension/termination automation | lifecycle | Built (12, Phase 17 backup-before-terminate) |
| Affiliates | apps.affiliates | Built (13) |
| Reports | apps.reports | Built (14) — charts/scheduled reports Post-MVP (Section 26) |
| Admin dashboard, setup, utilities, logs | apps.console + Setup | Built (D4, Phase 15, D6-D8) |
| Two-factor auth, security | apps.accounts.mfa + Phase 16 hardening | Built (16, optional for everyone) |
| Automation status / cron health | apps.core (`TaskRun`, `console:automation`) | Built (17) |
| Announcements, network status, downloads | — | Post-MVP (hidden) |
| Credit balance, add funds, mass pay | — | Post-MVP (hidden) |
| Local payment gateways | manual methods now | Adapter when chosen (Section 26) |
| External/WordPress storefront integration | apps.storefronts | Built (E13), no WordPress plugin itself (`docs/storefront-connection-guide.md`) |

------------------------------------------------------------------------

# PHASES 01–13 — COMPLETED BASELINE (DO NOT REBUILD)

These specifications are kept as reference. They are complete (Section 25). Only Rule 2 exceptions allow changes.

## PHASE 01 — FOUNDATION & ARCHITECTURE 🟢

- **Accounts:** registration, login, logout, password reset, email verification, profile, account status.
- **Roles:** Customer, Support Agent, Manager, Admin, Super Admin.
- **Permissions:** clients, products, orders, billing, domains, hosting, support, reports, providers, system settings.
- **API foundation:** versioning, authentication, permission classes, consistent error format, validation, pagination, filtering, rate limiting, documentation strategy.
- **Infrastructure:** PostgreSQL, Redis, Celery, email, notifications, audit logging, error logging, environment configuration.

## PHASE 02 — CLIENT MANAGEMENT 🟢

View, search, filter, add, edit clients; manage users; status; profile with Users, Orders, Services, Domains, Invoices, Transactions, Tickets, Quotes, Cancellations, Affiliate, Activity. Customer- and admin-facing endpoints share one service layer.

## PHASE 03 — PRODUCTS, PRICING & ADDONS 🟢

- **Products:** Shared, WordPress, Reseller, VPS, Dedicated, Domain products (domains priced separately via TLD pricing).
- **Fields:** name, description, status, pricing, setup fee, billing cycles, resource limits, server mapping, WHM package mapping, provisioning rules, auto-renewal.
- **Cycles:** Monthly, Quarterly, Semi-Annual, Annual, Biennial, Custom.
- **Addons:** SSL, Dedicated IP, Backup, Malware protection, Migration, Extra storage, Extra email, Premium support.
- Prices are calculated server-side.

## PHASE 04 — DOMAIN MANAGEMENT 🟢

Availability search, registration, renewal, transfer, details, nameservers, DNS, auto-renew, expiry. `DomainProvider` operations: `check_availability`, `register_domain`, `renew_domain`, `transfer_domain`, `get_domain`, `update_nameservers`, `get_dns`, `update_dns`, `lock_domain`, `unlock_domain`. Registration/renewal are idempotent.

## PHASE 05 — HOSTING / WHM PROVISIONING 🟢

Server records, status, credentials, package mapping, provisioning settings. WHM operations: create, suspend, unsuspend, terminate, change package, status, usage.

```text
PAID ORDER → Provisioning Job → HostingProvider → WHM API → Success: Active | Failure: Retry/Review
```

Idempotency, retries, failure states, manual retry, audit, safe credentials, timeout handling.

## PHASE 06 — CART & CHECKOUT 🟢

```text
Product → Domain → Addons → Duration → Cart → Checkout → Payment → Invoice → Order
```

Never trust browser values for price, total, duration, discount, credit, tax, expiry or provisioning state.

## PHASE 07 — BILLING & INVOICES 🟢

Transactions, invoices, invoice items, billable items, quotes, discounts, tax, due dates, payment status, PDF invoices. States: Draft, Unpaid, Paid, Partially Paid, Overdue (derived), Cancelled, Refunded. Figures reproducible from permanent records.

## PHASE 08 — RENEWALS, UPGRADES & PRORATION 🟢

```text
Renewal: Service → Renewal Price → Invoice → Payment → Expiry Extended
Upgrade: Plan → Days Remaining → Unused Value → New Price → Credit → Net Payable → Invoice → Payment → New Plan + Expiry
```

Credit server-side, capped at paid value, none once expired; tampered credit/expiry ignored; invoice shows the calculation.

## PHASE 09 — ORDER MANAGEMENT & LIFECYCLE 🟢

Screens: All, Pending, Active, Fraud, Cancelled, Add Order.
`Draft → Pending Payment → Paid → Processing → Provisioning → Active`; exceptional: Fraud, Failed, Cancelled, Suspended, Terminated. Every transition audited.

## PHASE 10 — CUSTOMER SERVICE & SUPPORT 🟢

Overview, tickets, new ticket, departments, assignment, priority, status, internal notes, predefined replies, attachments, related service/domain. States: Open → Agent Reply → Customer Reply → Pending → Resolved → Closed. Knowledgebase: Hosting, Domains, cPanel, DNS, Email, WordPress, Billing, Getting Started.

## PHASE 11 — NOTIFICATIONS & EMAIL 🟢

Events: registration, verification, order, payment, invoice, overdue, hosting activation, domain registration, renewal, suspension, cancellation, ticket update, quote update, dispute/payment update. Tracking: sent, failed, opened, open rate (a signal, not proof).

## PHASE 12 — CANCELLATION & SERVICE LIFECYCLE 🟢

Request, reason, immediate/end-of-term, admin review, approval, suspension, termination, domain handling, refund/final billing.
`Active → Renewal Due → Overdue → Grace → Suspended → Terminated` with configurable timings.

## PHASE 13 — AFFILIATE MANAGEMENT 🟢

Accounts, referral links, attribution, commission rules (percentage/fixed), records (pending/approved/paid/rejected), payout history, reports.

------------------------------------------------------------------------

# PHASE 14 — REPORTS 🟢

> **Delivered before v2 was adopted** (commit `f1246ba`, `docs/phase-14-gap-report.md`): 14 reports (sales, financial, services, support) with CSV / XLSX / PDF export (audited, formula-safe), a REST API and headline figures. The v2 items not yet built are recorded in Section 26 ("Phase 14 follow-ups"). The specification below is kept as the reference for those follow-ups.

## Objective
Business visibility, WHMCS-style reports index.

## Reports

| Group | Reports |
|---|---|
| Sales | Daily performance, orders by status, new customers, product sales |
| Financial | Daily / monthly / annual income, unpaid invoices, transactions, refunds/disputes, tax collected, coupon usage |
| Services | Active hosting, active domains, expiring services (30/60/90 days), suspended, cancelled, churn |
| Support | Open tickets, resolved tickets, department statistics, response times |
| Affiliates | Commissions by status, top affiliates |

## Requirements

- Every figure comes from the existing billing/service records through the existing calculation modules (no second billing arithmetic, Rule 3).
- Reports index page with one card per report; each report has date range filter, table, optional chart, and export (PDF, CSV, XLSX).
- Filters in the query string (bookmarkable, crawler-testable).
- Heavy reports run as Celery jobs with a status page and a download link that stays valid.
- Report access controlled by `view_reports`.
- Staff dashboard widgets (Phase 15) reuse these report queries.

------------------------------------------------------------------------

# PHASE 15 — ADMIN OPERATIONS ⬜

## Objective
All daily operations from the staff area. Django admin no longer needed for routine work. Works together with D4 (D4 = layout/navigation of existing modules; Phase 15 = new operational features below).

## Features

- Staff dashboard widgets (Section 11.2).
- Global staff search.
- Staff & role management UI (replaces the Django-admin-only deferred item).
- Setup screens for providers, servers, products, domain pricing, payment methods, tax, coupons, email, notifications, lifecycle timings, affiliates, departments.
- Audit log and activity log viewers with filters.
- System health page: DB, Redis, Celery workers, beat last-run per job, email provider, registrar, WHM.
- Maintenance mode toggle.
- Configurable reminder schedule (deferred item).
- Terms-of-service page with versioning and acceptance at checkout (deferred item).
- Client chooser for web cart / staff ordering on behalf (deferred item).
- Customer self-service sub-user invitations (deferred item, with Phase 16 permission design).
- Restrict invoices/quotes to owner/billing contacts (deferred item, with Phase 16).

------------------------------------------------------------------------

# PHASE 16 — SECURITY HARDENING 🟢

> **Slice 1 delivered** (`docs/phase-16-gap-report.md`): two-factor sign-in (TOTP), optional for everyone, with
> recovery codes and a Super Admin reset; account lockout beyond the API rate limit; a Content Security Policy and
> Permissions Policy. Everything else FOUND already in place (CSRF, secure sessions, rate limiting, webhook
> signatures, API auth, open-redirect protection, audit coverage) is listed in that report, along with what is
> deferred to a later slice (RBAC re-audit, "Login as Client").
>
> **Slice 2 delivered** (same report, "Slice 2" section): an RBAC review - fixed the route inventory's own
> under-reporting of a few genuinely-guarded `/staff/...` routes as "public" (a documentation-accuracy bug, not a
> real gap), and flagged one vestigial permission (`manage_reports`, granted but checked by no view; reports are
> read-only) for cleanup whenever `roles.py` is next touched. Deferred: "Login as Client" (no such feature exists to
> gate).
>
> **Also in slice 2, tried and reverted the same day**: two-factor sign-in was briefly made mandatory for staff and a
> step-up "confirm it's you" re-authentication was built for the four most sensitive staff actions. Both were tried
> and found to interrupt routine staff work rather than read as a safeguard for a small, trusted team, so both were
> removed - two-factor sign-in is optional for staff exactly as it is for a customer, and there is no re-confirmation
> step. See the gap report's "Revised after this slice" note for the full reasoning.

RBAC review, **MFA (TOTP), optional for staff and customers alike**, secure sessions, CSRF, rate limiting, account lockout beyond throttling, secure passwords, credential protection, server-side billing validation, webhook signature verification, API authentication, sensitive-action confirmation (confirmation modal component), "Login as Client" audited with banner, security headers (CSP, HSTS, X-Frame-Options, Referrer-Policy), open-redirect protection on every `next` parameter.

**Audit:** authentication changes, security changes, user changes, product changes, price changes, invoice changes, payment verification, provisioning, suspension, termination, domain changes, admin actions.

------------------------------------------------------------------------

# PHASE 17 — RELIABILITY & FAILURE HANDLING 🟢

> **Slice 1 delivered** (`docs/phase-17-gap-report.md`): Rule 1 inspection found most of this phase's own scenario
> list already built in earlier phases to a high standard - idempotent/resumable order fulfilment with a stuck-order
> sweep (Phase 09), duplicate-webhook and delayed-callback handling by recording a gateway's event id before applying
> it (Phase 09), and retrying email delivery with backoff (Phase 11). This slice closed the two concrete gaps that
> inspection found: automation status (`console:automation`, a "Background jobs" check on the System Health widget,
> and `TaskRun` recording every Celery task's last run from one place via Celery's own signals) and a WHOIS lookup
> tool (`apps.domains.whois`, verified against the real public WHOIS system, not mocked). Deferred to a later slice:
> scheduled hosting usage sync (blocked on the WHM adapter's own unrelated pre-existing "needs live verification"
> item), expiry of abandoned payments/unpaid orders, backup/hold-before-delete for automatic termination, and
> attachment retention.
>
> **Slice 2 delivered** (same report, "Slice 2" section): closed three of the four items slice 1 deferred - expiry of
> abandoned payment attempts and unpaid orders (`sweep_abandoned_orders`, daily, gateway-backed payment methods
> only; a manual method never auto-expires, by design), backup/hold-before-delete before automatic termination
> (`HostingAdapter.backup_account`, a `backup_before_terminate` setting on by default, terminating waits for a later
> day's run once a backup has been requested), and ticket attachment retention/cleanup (`purge_stale_attachments`,
> daily, wipes a closed ticket's old attachment files but keeps the row, mirroring how `purge_sensitive` already
> treats old email bodies). Still deferred: scheduled hosting usage sync, on the same pre-existing WHM-verification
> blocker as slice 1 - not a new gap. Live-tested backup request against the owner's own real, already-configured
> WHM server; it answered with a genuine `403 Forbidden`, handled cleanly with no crash - a real data point for the
> existing "WHM API adapter needs live verification before launch" item, not a bug in this slice.

Scenarios: registrar unavailable, WHM unavailable, payment callback delayed, email failure, provisioning failure, duplicate webhook, network timeout, worker failure.

```text
Request → Validation → Create Job → Execute
   Success → Complete
   Failure → Retry?  Temporary → Retry | Permanent → Manual Review
```

Also: scheduled hosting usage sync (still deferred, slice 2), expiry of abandoned payment attempts and unpaid orders (closed, slice 2), backup/hold-before-delete for automatic termination (closed, slice 2), attachment retention (closed, slice 2), job-queue status page (linked from Utilities, closed, slice 1).

------------------------------------------------------------------------

# PHASE 18 — TESTING & PRODUCTION READINESS ⬜

**Automated:** unit, integration, authorization, API, billing, domain, provisioning, renewal, upgrade, webhook, notification, **link crawler, template lint, email/PDF link tests**.

**Browser:** desktop, tablet, mobile across customer area, staff area, checkout, billing, support. Verify no overflow, no broken navigation, no JS errors, forms work, permissions work, every menu item reachable for its role.

**Infrastructure:** migrations, HTTPS, DNS, email, payment method(s), registrar, WHM, Redis, Celery, scheduled jobs, monitoring, error tracking (e.g. Sentry).

------------------------------------------------------------------------

# PHASE 19 — PRODUCTION LAUNCH ⬜

```text
Development → Implementation → Tests → Link Crawler → Review → Commit → Push
→ CI → Staging → Smoke Test (+ crawler on staging) → Production Deploy → Production Smoke Test
```

**Launch checklist:** domain, HTTPS/TLS, production database, email, payment methods, registrar API, WHM API, workers, scheduled jobs, monitoring, error tracking, customer signup, test order, invoice, payment, provisioning, renewal, **every email link opens the correct page on the production domain**, **error pages served**.

------------------------------------------------------------------------

# 20 — MVP BOUNDARY

The first production MVP contains: customer accounts, hosting products, domain products, addons, cart, checkout, invoices, transactions, payment method(s), domain API, WHM/cPanel API, hosting services, domain management, renewals, upgrades/proration, cancellation, **WHMCS-parity customer area**, **WHMCS-parity staff area and dashboard**, support tickets, notifications/email, basic reports, RBAC, MFA (optional, for everyone - Phase 16 slice 2 reverted a mandatory-for-staff attempt on owner feedback), audit logs, REST API foundation, **zero broken internal links (crawler green)**.

Advanced features are added after the MVP is stable.

------------------------------------------------------------------------

# 21 — API DESIGN STANDARD

Base: `/api/v1/`. No unversioned public APIs.

Resources (determined from actual implementation, not created up front): `auth`, `clients`, `products`, `orders`, `services`, `domains`, `invoices`, `transactions`, `tickets`, `notifications`.

Rules: authenticated by default, explicit public endpoints only, object-level permissions, pagination, filtering, consistent errors, stable response contracts, server-side validation, audit sensitive operations. **Re-skinning the web UI never changes an API contract.**

------------------------------------------------------------------------

# 22 — MOBILE READINESS

Not required for MVP; the backend must not block it. API supports authentication, profile, products, cart, checkout, invoices, payments, services, domains, tickets, notifications. Mobile holds presentation logic only; business rules stay in Django services. Flutter or React Native chosen when mobile work begins. The design tokens in Section 9.2 are the source for the mobile theme.

------------------------------------------------------------------------

# 23 — ENGINEERING QUALITY RULES

- **Server-side truth:** never trust client prices, totals, discounts, credits, expiry, permissions, service status.
- **Idempotency:** payment webhooks, provisioning, domain registration, renewal, termination, invoice payment, notifications.
- **Provider isolation:** no provider API calls in templates; avoid them in views.
- **Transactions:** atomic financial and state-changing operations.
- **Observability:** status, timestamps, error info, retry count, correlation ID for background operations.
- **Never log:** passwords, API keys, payment secrets, tokens, unnecessary customer data.
- **Presentation rules:** templates contain no business logic and no queries beyond what the view supplies; money, dates and statuses always rendered through the shared filters/components; every link through `{% url %}` (Rule 5).

------------------------------------------------------------------------

# 24 — PHASE COMPLETION CHECKLIST

Before marking any phase 🟢:

### Code
- [ ] Existing implementation inspected
- [ ] No duplicate implementation (including templates/components)
- [ ] Architecture rules followed
- [ ] API/business logic separated
- [ ] Permissions implemented
- [ ] Error handling implemented

### Tests
- [ ] New behaviour tested
- [ ] Authorization tested
- [ ] Regression tests passed
- [ ] Relevant full suite passed

### UI (if UI changed)
- [ ] Uses the layout shells and shared components (Section 9)
- [ ] Menu entries added through the registry, with permission
- [ ] Breadcrumbs, empty states and status badges present
- [ ] Checked at 375 / 768 / 1280 px, no horizontal overflow
- [ ] No JS console errors
- [ ] Keyboard and contrast checks pass

### Links
- [ ] Link crawler green for every role
- [ ] Template lint green (no `#`, empty or hard-coded internal links)
- [ ] Email/PDF links resolve
- [ ] Any changed path has a 301 redirect; no URL name renamed

### Database
- [ ] Migrations created if required
- [ ] Migration check clean
- [ ] No unnecessary schema changes

### Documentation
- [ ] Roadmap updated (Sections 25–27)
- [ ] Known limitations recorded
- [ ] Architecture decision recorded if applicable

### Git
- [ ] Changes reviewed
- [ ] Commit created only after verification

### Deployment
- [ ] Push only when approved
- [ ] CI checked
- [ ] Deploy checked
- [ ] Production smoke test performed

------------------------------------------------------------------------

# 25 — PROJECT STATE TRACKER

## Business phases

| Phase | Status | Tests | Browser | Links | Commit | Deploy |
|---|---|---|---|---|---|---|
| 01 Foundation & Architecture | 🟢 | 70 ✅ | ✅ | D2 crawl: 0 broken | 960bd54 | — |
| 02 Client Management | 🟢 | 98 ✅ | ✅ | D2 crawl: 0 broken | 3d257c4 | — |
| 03 Products & Addons | 🟢 | 150 ✅ | ✅ | D2 crawl: 0 broken | 3697eac | — |
| 04 Domain Management | 🟢 | 227 ✅ | ✅ | D2 crawl: 0 broken | e614249 | — |
| 05 WHM Provisioning | 🟢 | 296 ✅ | ✅ | D2 crawl: 0 broken | 9c0c517 | — |
| 06 Cart & Checkout | 🟢 | 458 ✅ | ✅ | D2 crawl: 0 broken | 23168d7 | — |
| 07 Billing & Invoices | 🟢 | 622 ✅ | ✅ | D2 crawl: 0 broken | ac37961 | — |
| 08 Renewals & Upgrades | 🟢 | 729 ✅ | ✅ | D2 crawl: 0 broken | 4d00e37 | — |
| 09 Orders & Lifecycle | 🟢 | 788 ✅ | ✅ | D2 crawl: 0 broken | 8d83271 | — |
| 10 Support | 🟢 | 885 ✅ | ✅ | D2 crawl: 0 broken | e425a85 | — |
| 11 Notifications & Email | 🟢 | 945 ✅ | ✅ | D2 crawl: 0 broken | 7d4026a | — |
| 12 Cancellation | 🟢 | 1046 ✅ | ✅ | D2 crawl: 0 broken | d3f9b82 | — |
| 13 Affiliates | 🟢 | 1134 ✅ | ✅ | D2 crawl: 0 broken | a2e8989 | — |
| 14 Reports | 🟢 | 1197 ✅ | ✅ | D2 crawl: 0 broken | f1246ba | — |
| 15 Admin Operations | 🟢 (delivered with D4d: staff and roles, email provider, registrar, audit log viewer; see D4. Follow-up 2026-10-03: maintenance mode (redesigned the same day), configurable reminder schedule, ToS acceptance at checkout, cart client chooser - found by a pre-Phase-18 documentation audit) | 2201 ✅ | ✅ | crawler green | 7e5f35b | — |
| 16 Security | 🟢 (slice 1: MFA/lockout/headers; slice 2: RBAC review, mandatory-MFA and step-up re-auth tried then reverted per owner feedback) | 2046 ✅ | ✅ | crawler green | 3737541, e05ae9d | — |
| 17 Reliability | 🟢 (slice 1: found most of the phase's scenarios already built in earlier phases; added automation status and WHOIS lookup. slice 2: abandoned payment/order expiry, backup-before-terminate, attachment retention; usage sync still deferred on WHM verification) | 2167 ✅ | ✅ | crawler green; real WHM 403 handled cleanly | — | — |
| 18 Testing & Readiness | 🟢 local (2026-10-04: suite green, optional Sentry error tracking added, `docs/operations-runbook.md` for backup/restore/monitoring; `docs/phase-18-gap-report.md`. Open launch blockers carried to 19: WHM live verification, scheduled usage sync, staging checks of HTTPS/DNS/email/registrar) | green, 0 failures | — | — | — | — |
| 19 Production Launch | 🟡 started 2026-10-04: `docs/phase-19-launch-checklist.md` (owner inputs needed: domain, server, TLS, WHM access, email, registrar, backup destination) | — | — | — | — | — |

"Links" column: phases 01–14 predate the crawler; D2 back-filled it for them: the crawl covers every page of every phase for eight roles on data with one object in every status. D0 ran a prototype crawler (8 roles, 3,000+ pages: no 404, 500 or 403-after-link) and a hostile-query sweep, now partly in CI (`apps/core/test_link_integrity.py`).

## Design & navigation track

| Phase | Status | Tests | Browser | Links | Commit | Deploy |
|---|---|---|---|---|---|---|
| D0 UI & Navigation Audit | 🟢 | 1220 ✅ | ✅ | prototype crawler: 8 roles, 0 broken (kept as a CI test in D2) | dd1c88a | — |
| D1 Design System & Shell | 🟢 | 1293 ✅ | ✅ | nav links: 6 visitor kinds, 0 broken | ee8a609 | — |
| D2 Link Integrity Harness | 🟢 | 1399 ✅ | ✅ | CI crawler: 8 roles x every status, 0 broken; emails, PDFs, redirects, `next` | da099e8 | — |
| D3 Client Area Parity | 🟢 | 1480 ✅ | ✅ | CI crawler: 8 roles, new pages included, 0 broken; tabs and sidebar counts tested | 03ba01b | — |
| D3b Modern look & Web Host Era brand (owner request) | 🟢 | 1517 ✅ | ✅ | crawler green; 35 customer pages x 3 widths 0 findings | ef7a6ce | — |
| D4 Staff Area Parity + Phase 15 screens (D4a dashboard, search, audit log; D4b client profile tabs; D4c lists and bulk actions; D4d staff, email provider, registrar; D4e re-skin, legacy.css deleted) | 🟢 | 1668 ✅ | ✅ | crawler green (dashboard widgets followed via hx-get) | aab5715, 11a26c6, 4ea073a, 7e5f35b, 83cb778 (D4a-e) | — |
| D6 Sign-in, application shell and Users area (owner request 2026-09-26: split-screen sign-in and sign-up, left rail + top bar + phone drawer/bottom bar generated from the registry, Users screens for every role, Technical Staff role) | 🟢 local (not deployed) | 1714 ✅ (PostgreSQL 17) | ✅ 609 pages x 6 roles x 3 widths, 0 findings | crawler green with a ninth role (technical staff) | 19c45ca |
| D7 Visual system, navigation and dashboards, second pass (owner request 2026-09-26: rebuilt tokens for both themes, one rail with folding sections and a light/dark/auto switch, KPI strip + needs-attention + varied panels, customer overview recomposed, states, page headers) | 🟢 local (not deployed) | 1748 ✅ (PostgreSQL 17) | ✅ 1218 page loads: 6 roles x 4 widths (1440, 1024, 820, 390) x light + dark subsets; 30 pages contrast, names, focus | crawler green | 8b188ca |
| D8 Layout change (owner request 2026-09-26: left rail replaced by one navy header with section menus and the area's pages, hero band on both dashboards, all inner pages restyled through shared CSS) | 🟢 local (not deployed) | 1754 ✅ (PostgreSQL 17) | ✅ 4 sweeps x 212 pages x 6 roles (1440, 820, 390 light; 390 dark), 17 menu and phone interaction checks, 30 pages contrast, names, focus | crawler green | — |
| E1 Owner tools (owner request 2026-09-26: Super Admin sets/resets passwords and deletes clients; USD/PKR/SAR on clients and draft invoices/quotes; email diagnosis, automatic retry, delivery timeline, click tracking, email log management; bulk delete in emails, notifications, drafts, clients; audit-log export and Super Admin purge) | 🟢 local (not deployed) | 1792 ✅ (PostgreSQL 17) | ✅ 38 browser checks at 1440 and 390 (bulk delete, retry, password set + sign-in, client delete, currency label, purge limits), no JS errors | crawler green | — |
| E2 Cart and checkout for visitors (owner request 2026-09-26: guest cart, sign-in that keeps it, checkout that creates the account, upsell and cross-sell chosen by staff) | 🟢 local (not deployed) | 1817 ✅ (PostgreSQL 17) | ✅ 22 browser checks at 1440 and 390 (plan → cart → upgrade → domain → checkout → order), no JS errors | crawler green | — |
| E3 Store categories and redesigned cart and checkout (owner request 2026-09-26: cards with one order summary, period menu, add-on switches, WHOIS privacy on domains, categories on the front page, bank transfer) | 🟢 local (not deployed) | 1831 ✅ (PostgreSQL 17) | ✅ 24 browser checks at 1440 and 390 (home, store, cart, checkout, order), no JS errors | crawler green | — |
| E4 Mail deliverability, client sign-in, editable email wording, store categories, sectioned forms, invoice lines, reports and dashboard numbers (owner requests 2026-09-26) | 🟢 local (not deployed) | 1869 ✅ (PostgreSQL 17) | ✅ 41 browser checks at 1440 and 390, no JS errors | crawler green | — |
| E13 External / WordPress storefront integration (owner spec `WP_Requirement.md`; slice 1 2026-10-02, slice 2 2026-10-03): `ExternalStorefront`/`StorefrontProduct`, versioned read-only `/api/v1/storefront/...` API with its own credentialed auth, per-product global + per-storefront publishing with a per-storefront override screen, Setup → External storefronts staff UI, signed SSRF-safe webhook cache invalidation with retry | 🟢 local (not deployed) | 2137 ✅ | ✅ staff UI + live HTTP round-trip against a running scratch server + webhook UI, no JS errors | crawler green (new fixture storefront+product seeded) | — |
| E14 Nine owner requests 2026-10-03 (quote PDF footer/highlight, client alternate email, self-service contact invites, per-staff extra permissions, optional invoice email at Add Order + manual "Email invoice", domain registration manual/auto switch, Fraud status gates client delete, Abuse support department, upgrade proration reversed to preserve the renewal date) | 🟢 local (not deployed) | 2157 ✅ | ✅ 5 staff/customer screens + a real rendered quote PDF, no JS errors | crawler green | — |
| D5 Visual QA & Polish (Rule 1 found most already built in D7/D8/D1/E14; closed 4 remaining gaps: pricing table @375px, a 400-page test, three empty-state rows, print stylesheet for invoice/quote actions) | 🟢 local (not deployed) | 2169 ✅ | ✅ 375px screenshot + print-media screenshot, no JS errors | crawler green | — |

## Next-generation track (`WHPANEL_NEXT_GENERATION_ROADMAP_FOR_CLAUDE.md`; local only, started 2026-10-05)

| Slice | Status | Notes |
|---|---|---|
| H0 Hosting control architecture and provider abstraction | 🟢 local | `docs/h0-hosting-control-architecture.md`: capability contracts, one gateway (`apps/hosting/control.py`), `GET /api/v1/hosting-accounts/{id}/capabilities/`; 14 new tests |
| H1 Websites and hosting dashboard | 🟡 local (browser pass at 375/768/1280 pending) | `docs/h1-websites-dashboard.md`: card grid with plan, status, storage and bandwidth meters, SSL/PHP when a provider reports them |
| H2 File manager | 🟡 local | `docs/h2-file-manager.md`: sandboxed browse, upload, edit, rename, copy, delete, download, search on the demo provider; permissions, archive/extract, bulk actions deferred |
| H3 Databases | 🟡 local | `docs/h3-databases.md`: databases, users, passwords, privileges, plan limits on the demo provider; phpMyAdmin deferred |
| H4 Email | 🟡 local | `docs/h4-email.md`: mailboxes, quotas, passwords, forwarders, automatic replies, routing, spam settings, simple webmail on the demo provider; real mail delivery deferred |
| H5 Domains and DNS | 🟡 local | `docs/h5-domains-dns.md`: extra domains, subdomains, aliases, redirects, DNS zones with per-type validation and mail record helpers on the demo provider |
| H6 SSL and H7 PHP | 🟡 local | `docs/h6-h7-ssl-php.md`: certificate status, issuer, expiry, auto-renew, always-use-HTTPS, install/renew/remove; PHP version, memory, upload, run time, error display and extensions on the demo provider |
| H8 Cron, logs, FTP, IP blocking | 🟡 local | `docs/h8-cron.md`: scheduled PHP scripts (never a free shell), error/access log viewer, FTP accounts limited to a folder, IP blocking with self-block protection; redirects are in H5 |
| H9 Backup and restore | 🟡 local | `docs/h9-backups.md`: real archives for files, databases, email and full account, integrity check, restore with safety copy, retention and schedule |
| H10 Security Center | 🟡 local | `docs/h10-security.md`: malware scan with quarantine, folder protection, SSH keys, file-integrity monitoring, security events, SSL and IP summary |
| H11 Usage, logs and performance | 🟡 local | `docs/h11-usage.md`: allowance and resource counts, warnings, 7/30/90-day history, live CPU/memory/processes/disk activity, disk limit enforced by the provider |
| H12 Admin Access | 🟡 local | `docs/h12-admin-access.md`: staff manage a customer's hosting through the same engine with a reason, banner, one-hour grant, role-based actions and full audit |
| H13 Infrastructure dashboard | 🟡 local | `docs/h13-infrastructure.md`: a card per node with health, region, provider, account count, CPU/memory/disk/load/network and web/database/mail/DNS/agent/backup services; cPanel nodes report only what WHM can |
| H14 cPanel adapter (files, domains, DNS, SSL, databases, email, PHP, cron, backups) | 🟡 local, unverified on a real server | `docs/h14-cpanel-adapter.md`: UAPI and API 2 mappings behind a per-tool switch `HOSTING_CPANEL_CAPABILITIES` (off by default), tested with a fake WHM; real-server verification still to do |
| H15 Open cPanel (transitional) | 🟡 local, unverified on a real server | `docs/h15-cpanel-escape-hatch.md`: one-time cPanel sign-in via WHM, authorised and audited, no token or password shown, off by default; remove with cPanel |
| Provider-neutral hosting API | 🟡 local | `docs/hosting-api.md`: `/api/v1/hosting/services/{id}/files, databases, emails, domains, dns, ssl, php, cron, backups, usage` through the same gateway |
| N0 Native architecture and security design | 🟡 proposed, awaiting decisions | `docs/adr/`: 20 architecture decision records with options, scores and a recommendation each; none is Accepted yet |
| N1-N10, M0-M4 | ⬜ | Per the next-generation roadmap; H14 needs the WHM adapter verified on a real server |

## Recommended order

D0 ✅ → D1 ✅ → D2 ✅ → D3 ✅ → D3b ✅ → D4 + 15 Admin Operations ✅ → D6 Sign-in, shell and Users ✅ (local) → D7 Visual system and dashboards ✅ (local) → D8 Layout change ✅ (local) → 16 Security ✅ (local) → 17 Reliability ✅ (local) → D5 Visual QA ✅ (local) → 18 ✅ (local) → **19 (next)**

------------------------------------------------------------------------

# 26 — KNOWN WORK / DEFERRED ITEMS

Use this section whenever something is discovered but intentionally postponed.

**Rule:** If an item is recorded here, do not rediscover or re-plan it unless its status changes.

⚠️ **Re-triage** = the original target phase is already 🟢 but the item is still open/deferred. At the next session touching that area, confirm the real state (Rule 1) and either close it or give it a new target. Do not assume it is done.

## Open / Deferred

| Item | Status | Reason | Target |
|---|---|---|---|
| More email provider kinds (API senders), bounce handling, admin-editable templates | Deferred | SMTP only; no click tracking or bounce handling; templates are files, not editable in admin | Post-MVP |
| MFA | Closed (Phase 16 slice 1) | TOTP, optional for everyone (staff and customers alike, after slice 2 reverted mandatory-for-staff on owner feedback) - `apps/accounts/mfa.py` | — |
| Account lockout beyond rate limiting | Closed (Phase 16 slice 1) | Per-account lockout after 5 wrong passwords/codes, on top of the existing per-IP throttle | — |
| Staff admin portal (non-Django-admin UI for roles/status/audit) | Done (D4d, D6) | Users, roles, status, audit log, providers and setup screens are app screens; Django admin is a superuser-only technical door ("Database admin") | — |
| Error tracking service (e.g. Sentry) and monitoring | Deferred | Structured logging with request IDs in place | Phase 18 |
| TLS termination / certificates | Deferred | Compose Nginx serves HTTP only | Phase 19 |
| Docker Compose stack run end to end | Open | No Docker on dev machine; CI covers PostgreSQL/Redis/Celery | Phase 19 |
| Require verified email before purchase | Deferred ⚠️ Re-triage | Verification tracked; purchase gating belongs to checkout (Phase 06 is 🟢); not part of Phase 16's actual scope, which is now 🟢 too | Post-MVP |
| Customer self-service sub-user invitations | Partly closed (E14) | An owner contact can invite someone else onto the account (`docs/e14-nine-owner-requests.md`); role changes and removal are still staff-only | Post-MVP |
| Closing a client suspends users/services | Deferred ⚠️ Re-triage | Service lifecycle rules belong to cancellation phase (Phase 12 is 🟢) | Re-triage |
| Server credentials / WHM adapter | Closed (Phase 05) | Stale - `Server` was extended with `kind`/`api_port`/`api_username`/encrypted `api_token` exactly as planned; this row was never updated when Phase 05 did it | — |
| Multi-currency product pricing | Deferred | Single store currency for MVP | Post-MVP |
| Addon-to-product-type compatibility restriction | Deferred ⚠️ Re-triage | Any addon attaches to any product for now | Re-triage |
| Staff pricing table row-actions cramped at 375px width | Closed (D5) | Reused the existing `.table-stack` component (console/users.html); the table is a column of labelled rows below 575.98px | — |
| Real registrar adapter needed before launch | Open | Manual adapter is local-only simulation, no real network calls | Before launch |
| Multi-part TLDs (.co.uk, .com.au) not supported | Deferred | Needs a public-suffix list | Post-MVP |
| Outbound transfer auth-code retrieval not implemented | Deferred | Registrar-specific; not in the roadmap's explicit op list | Real registrar adapter |
| WHM API adapter needs live verification before launch | Open | Written to WHM's public docs, never run against a real server | Before launch |
| No customer self-service for suspend/terminate/change-package | Deferred ⚠️ Re-triage | No billing/cancellation workflow to hang these off at the time (08/12 now 🟢); decision log limits self-service to view + request | Re-triage |
| No automatic multi-server capacity-based selection | Deferred | Staff pick manually when a product maps to >1 server | Post-MVP |
| Hosting usage sync is manual, not scheduled | Built 2026-10-04 but OFF by default (`HOSTING_USAGE_SYNC_ENABLED`); turn on after WHM live verification ⚠️ | Needs a verified live WHM adapter first (Phase 17 slice 2 live-tested the new backup call against the owner's real WHM server and got a 403 - that item is still open) | Before launch |
| Guest (anonymous) carts | Closed (E2) | Stale - a visitor can shop without an account (`Cart.guest_token`/`is_guest`, session-kept, merged into their own cart on sign-in); this row was never updated when E2 built it | — |
| Antivirus scanning of ticket attachments | Open | Uploads are type/content checked, stored privately, only downloaded, but not scanned (e.g. ClamAV) | Before launch |
| Reply-by-email / email-to-ticket | Deferred | Customers reply in the portal; team alerts for unassigned tickets delivered in Phase 11 | Post-MVP |
| SLA timers, escalation, ticket merge, satisfaction rating | Deferred | Not in the roadmap's Phase 10 list | Post-MVP |
| Attachment retention / cleanup | Closed (Phase 17 slice 2) | `purge_stale_attachments`, daily: wipes a closed ticket's old attachment file, keeps the row | — |
| Add-on provisioning; add-ons and custom cycles in staff Add Order | Deferred | Add-ons are recorded against the plan, not provisioned separately | Post-MVP |
| Order suspend/terminate act on hosting only; refunds do not cancel fulfilled services | Deferred | Domains are registrations and are left alone; a refund does not undo what was fulfilled | Post-MVP |
| No real payment gateway adapter (Stripe unavailable in Pakistan) | Decided | 2026-09-25: payments are manual (COD / bank / wallet); each method = an account with instructions; staff record amount, account, transaction ID and date received. Test gateway stays dev-only (`ALLOW_TEST_PAYMENT_GATEWAY`). A real gateway = one adapter class | Until a gateway is chosen |
| Invoices/quotes visible to any contact of the client | Deferred | Restrict financial documents to owner/billing contacts; a permissions-design decision, not part of Phase 16's actual scope | Post-MVP |
| Reminder schedule is fixed | Closed (Phase 15 follow-up) | `BillingSettings.reminder_schedule_days` (comma-separated days from the due date, validated); default unchanged (-3,1,7,14) | — |
| Maintenance mode toggle (Phase 15's own feature list named it; never built, never tracked here - found by the pre-Phase-18 documentation audit) | Closed (Phase 15 follow-up) | `BrandSettings.maintenance_mode` + `MaintenanceModeMiddleware` (staff/sign-in/API exempt); a flag-file layer for Nginx (`deploy/nginx/whpanel.conf`), config-only, same limitation as the Compose stack below | — |
| Credit balances (carrying forfeited upgrade credit forward); refunds do not reverse a renewal/upgrade | Deferred | Credit beyond the new price is forfeited and shown as such; a refund never undoes a service change | Post-MVP |
| Downgrades / billing-cycle changes online; renewal of EXPIRED domains | Deferred | Only upgrades are offered online; staff handle the rest (unsuspend-on-payment closed in Phase 12) | Post-MVP |
| PDF text limited to Western European characters | Deferred | Built-in PDF fonts; other scripts print as "?" | Post-MVP |
| Abandoned online payment attempts never expire | Closed (Phase 17 slice 2) | `sweep_abandoned_orders`, daily: cancels a gateway-backed Pending Payment order after 3 days, which cancels its invoice and fails the stale transaction; a manual payment method never auto-expires, by design | — |
| Coupons apply to first payment only | Deferred ⚠️ Re-triage | No per-product limits or recurring discounts yet (Phase 08 is 🟢) | Re-triage → Post-MVP |
| Tax: one rule per country, order-level, exclusive | Deferred | Tax-exempt clients done in Phase 07; no tax-inclusive pricing, state rules or per-invoice rate override | Post-MVP |
| Unpaid orders hold domain names until cancelled | Closed (Phase 17 slice 2) | A gateway-backed order's domain-name hold now releases automatically when `sweep_abandoned_orders` cancels it; a manual-payment order still holds (by design, no timeout on manual payments) | — |
| Domains are not suspended, terminated or marked expired by the lifecycle job | Deferred | The registrar owns what follows expiry; only cancellation acts on domains | Post-MVP |
| Automatic termination has no backup / hold-before-delete step | Closed (Phase 17 slice 2) | `backup_before_terminate` (on by default): a backup is requested first, termination itself waits for a later day's run; no backup storage/restore of its own, only the adapter's own reference | — |
| Lifecycle timings are global; refunds on cancellation are a staff decision | Deferred | Not per product or client; refund always a staff decision (suggested only) | Post-MVP |
| Affiliates: no multi-tier or per-product rules, no coupon-code attribution, no payout batch export | Deferred | One level; a rule applies to the whole invoice; payouts recorded by staff | Post-MVP |
| Affiliate fraud beyond hold period and rejection | Deferred | Portal keeps no address/device data by design; staff review and can reject; not part of Phase 16's actual scope | Post-MVP |
| Terms-of-service acceptance at checkout | Closed (Phase 15 follow-up) | A required checkbox at checkout when policy pages are configured (E8); `Order.policies_agreed_at`/`policies_snapshot` record what was agreed to, snapshotted like the billing details | — |
| Web cart needs exactly one client; no client chooser | Closed (Phase 15 follow-up) | `/cart/choose-client/`: a contact of several accounts picks one for the session; the API's existing `client_id` and staff "Add Order" (Phase 09) are unchanged | — |
| Announcements, Network Status, Downloads pages | Deferred (new) | Not in MVP; menu entries hidden per Rule 5.2 | Post-MVP |
| Add Funds / Mass Payment in client area | Deferred (new) | Depends on credit balances | Post-MVP |
| RTL layout (Urdu/Arabic) and translated UI | Deferred (new) | Strings translation-ready from D1; RTL styles later | Post-MVP |
| Local payment gateway adapters (e.g. JazzCash, Easypaisa, bank payment gateways) | Deferred (new) | Choose provider first; one adapter class each | When chosen |
| Phase 14 follow-ups from v2: product-sales, tax-collected, coupon-usage, churn, response-time and affiliate reports; orders-by-status view; charts; heavy reports as Celery jobs with a durable download link | Deferred ⚠️ Re-triage | The 14 delivered reports cover the MVP list; D4/Phase 15 are both now 🟢 and neither touched this (they were staff-area/admin-ops UI parity, not new report types or a charting library decision) - confirmed still genuinely unbuilt by the pre-Phase-18 audit | Post-MVP |
| Reports: no scheduled or emailed reports, saved views or currency conversion; no chargeback data; "cancelled services" covers portal cancellations only | Deferred | On-demand CSV/XLSX/PDF only; the portal has no dispute records | Post-MVP |
| Error pages: branded 403/404/400, standalone 500 and maintenance page exist (D1); each has its own direct test since the crawler cannot organically reach an error handler (`apps/core/test_ui.py`, all four pages including the 400 case added at D5) | Closed (D1, test gap closed D5) | Rule 5.8 | — |
| Customers have no menu link to their cancellation requests | Closed (D1/D2) | Stale - `apps/core/navigation.py`'s `account.cancellations` entry has linked to `lifecycle_customer:list` from the account menu since D1/D2; this row was never updated when it was fixed | — |
| Page templates are not yet wrapped for translation (shells and components are); `legacy.css` is deleted (D4e) | Open | Wrap strings as pages are touched (confirmed still the right policy at D5: 52% of templates already wrapped, customer-facing areas essentially done, staff-admin screens lag) | Post-MVP |
| Vendored Bootstrap 5.3.3, Bootstrap Icons 1.11.3 and htmx 2.0.4 are updated by hand | Deferred | No package manager for front-end assets; versions recorded here | Post-MVP |
| Dark mode follows the system setting only (no manual toggle); HTML emails are the text email in a branded frame | Deferred | Not required by v2 | Post-MVP |
| Staff UI still missing for: payment providers (payments are manual; no gateway), webhook events, carts, coupon redemptions (Django admin only) | Open | Staff users, registrar, email provider, audit log, dashboard and global search were delivered in D4 and D6; Phase 17 (now 🟢, both slices) was reliability/background-job work and never addressed this | Post-MVP / gateway decision |
| External storefront webhook trigger coverage | Deferred (new, E13 slice 2) | Pings fire on publish/unpublish and on an already-published plan's own name/description/status/price changing; a category rename or other indirect change does not yet trigger one (`docs/e13-storefront-integration.md`) | Next storefront slice |
| External storefront webhook delivery: no manual retry/resend in the staff UI | Deferred (new, E13 slice 2) | Automatic retry (Celery, up to 5 attempts with backoff) and a recent-deliveries table exist; no way to force a retry sooner or resend a specific past delivery | Next storefront slice |
| Fraud status has no workflow beyond gating client delete | Deferred (new, E14) | A label plus a safety gate (only a Fraud-status client can be deleted); no auto-suspend or review queue (`docs/e14-nine-owner-requests.md`) | Post-MVP |
| Domain auto-register is one global switch, not per-TLD/per-product | Deferred (new, E14) | `auto_renew` (the renewal half) is already per-domain; registration's on/off is global only | Post-MVP |

## Closed

| Item | Closed in | Resolution |
|---|---|---|
| Email open/failure tracking | Phase 11 | Sent, failed, opened, open rate, resend, alerts |
| Client profile record sections | Phase 13 | Orders, invoices, payments, quotes, domains, hosting, tickets, cancellations and affiliate referral shown |
| Domain registration/transfer wired to billing | Phase 09 | Paid order registers/transfers its domains automatically |
| Automated renewal invoicing (expiry job) | Phase 08 | Nightly job creates renewal invoices; reminders in Phase 11 |
| Hosting provisioning/lifecycle wired to billing | Phase 09 | Paid order provisions hosting and starts its term; suspend/terminate cascade |
| Payment, invoices, transactions for orders | Phase 07 | Checkout issues the invoice; paying it marks the order paid |
| Order fulfilment from a paid order | Phase 09 | SYSTEM actor runs ordinary services; idempotent, resumable, retryable |
| Customer "request without paying" flows | Phase 09 | Retired; creation is staff/system only in the service layer |
| Staff not alerted when fulfilment fails | Phase 11 | Team alert on a failed order |
| Overdue reminders / dunning | Phase 11 | Daily job, default 3 days before due then 1, 7, 14 days after; on/off and the day schedule itself both configurable in Billing settings (schedule made configurable in the Phase 15 follow-up) |
| Renewals, upgrade proration | Phase 08 | Delivered |
| Hosting terms start automatically on a paid order | Phase 09 | Fulfilment starts the term with the plan's paid value |
| Unsuspend-on-payment | Phase 12 | A paid renewal lifts a non-payment suspension |
| Staff not notified of reported offline payment | Phase 11 | Team alert to billing staff |
| Basic reports (sales, financial, services, support) with PDF/CSV/XLSX export | Phase 14 | 14 reports, audited, formula-safe exports, API |
| Runtime CDN dependency; unbranded errors; hand-coloured status badges; unlabelled form fields; no focus styles; brand not configurable | D1 | Assets vendored; branded error pages; one status-badge tag; labels added and tested; focus rings; brand settings in the database (colours checked for contrast) |
| Static files on default storage; vanity paths; hand-written navbars; no link crawler in CI; emailed links and PDFs untested; a Support Agent shown a link to Products | D2 | `ManifestStaticFilesStorage` in production (a missing file fails the build); `/login/`, `/register/`, `/store/`, `/knowledgebase/` answer 301 via one `RETIRED` map; all menus from `apps/core/navigation.py` with a startup check; crawler for 8 roles on a world with one object in every status as a CI gate; email, PDF, redirect and safe-`next` tests; deploy check that `SITE_URL` is https |
| Customer dashboard, sidebars with counts, service and domain tabs, addons/payment methods/email history/contacts pages, re-skin of every customer screen | D3 | Section 10 delivered; `legacy.css` rules only the customer area used deleted; see `docs/d3-client-area.md` |
| Basic-looking design; no storefront front page; brand not the owner's | D3b | Modern theme (Plus Jakarta Sans vendored, light logo bar, pricing cards, hero); storefront front page; `seed_webhostera` loads the Web Host Era brand, 8 plans and 35 domain prices; see `docs/d3b-modern-look.md` |
| Server errors from junk or NUL characters in the address or forms; two "create" pages that 404 without `?client=`; hand-built internal URLs | D0 | `query_id` helper, `StripNullBytesMiddleware`, redirects to the client chooser, `reverse()` everywhere; regression tests |

------------------------------------------------------------------------

# 27 — ARCHITECTURE DECISION LOG

| Decision | Reason | Status |
|---|---|---|
| Django is primary backend | Strong relational/business application framework | Active |
| Django REST Framework is API layer | Mobile/API readiness without a second backend framework | Active |
| PostgreSQL is primary database | Transactional billing/service data | Active |
| Redis + Celery for async work | Provisioning, notifications, scheduled operations | Active |
| Provider adapters | Avoid provider lock-in | Active |
| Order/Invoice/Transaction/Service remain separate | Different business lifecycles | Active |
| API-first business architecture | Web and future mobile clients share business logic | Active |
| cPanel/WHM is external integration | Portal manages business; WHM manages server hosting accounts | Active |
| Single role per user; roles are Django Groups synced from `apps.accounts.roles` | One permission system; easy to audit | Active |
| Portal permissions live on `accounts.PortalAccess` (`view_`/`manage_<area>`) | Permission checks do not depend on future module models | Active |
| JWT (simplejwt) for API/mobile, sessions for web; JWT revoked on password change/suspension | Mobile readiness; immediate revocation | Active |
| Email provider is a DB record with encrypted credentials | No provider credentials in ENV | Active |
| Provider credentials encrypted with Fernet (`apps.core.crypto`), key from `CREDENTIALS_ENCRYPTION_KEY` | Reusable for WHM, registrar, payment | Active |
| Emails recorded first, delivered by Celery after commit | Retry-safe, auditable delivery | Active |
| Product/Addon share one CatalogStatus and one PriceEntry shape (ProductPrice/AddonPrice) | Avoids two visibility vocabularies and two pricing calculators | Active |
| Public catalog API/pages are one view with staff-vs-public shape | Same visibility rule (staff see all, others see active) as Phase 02 | Active |
| Server model created minimal in Phase 03; domain-as-product excluded from Product | Phase 05 extends the same model; domains get TLD-keyed pricing | Active |
| Business records belong to Client; users reach clients through ClientContact (owner/billing/technical) | Agencies have several users; one user can manage several accounts | Active |
| Staff accounts cannot be client contacts | Keeps staff and customer access apart | Active |
| Self-registration creates a Client owned by the new user | Every customer can order immediately | Active |
| Domain registration/transfer are request-then-complete (contact requests, staff completes) | Worked before Cart/Checkout/Billing existed (superseded for customers by Phase 09: purchase only via checkout) | Active |
| Registrar adapter interface with a Manual (local-only) adapter shipped | Registrar code confined to one layer; real registrar swaps in without touching services | Active |
| Self-service domain actions open to any client contact | None are financial; technical contacts exist for this | Active |
| Domain-specific TLD pricing model (TldPricing), separate from Product | Register/renew/transfer pricing by TLD does not fit the billing-cycle shape | Active |
| Server model extended (not duplicated) with WHM connection fields | One place for server records | Active |
| Real WHM API adapter written against public docs; registrars Manual-only | WHM is one stable documented protocol, unlike registrars | Active |
| cPanel passwords generated per provisioning, emailed once, never stored | Nothing to leak from our own DB | Active |
| Hosting self-service limited to view + request | Other actions are operationally sensitive or imply billing change | Active |
| A cart stores selections, never prices; one pricing engine (`orders.pricing`); orders snapshot it | Never trust browser prices; one place for billing calculations | Active |
| Phase 06 ends at an Order awaiting payment | Follows roadmap phase definitions | Active |
| `apps.billing` holds configuration (payment methods, tax, coupons); `apps.orders` holds carts and orders | Phase 07 extends it, no second billing app | Active |
| Payment methods (customer-facing) separate from payment gateways | Gateways are live-configured providers | Active |
| Order created once with full lifecycle vocabulary | Later phases add transitions, never a second Order | Active |
| Checkout recomputes under row locks, atomic and double-submit safe; unpaid orders reserve domain names | Correct money, no double coupon redemption, no name sold twice | Active |
| One billing arithmetic (`billing.calculations`) for cart, orders, invoices, quotes; discount and tax allocated to lines in cents | No duplicate calculations; documents sum exactly | Active |
| Invoice paid/refunded amounts and status derived from succeeded transactions under row lock; "overdue" derived | Figures reproducible from permanent records (`verify_billing`) | Active |
| Issued invoices immutable (pay, refund, cancel only); gap-free numbers taken at issue | Legal/financial integrity | Active |
| Payments succeed only via a signed gateway webhook (idempotent by event id, amount/currency checked); lock order invoice → tx → order | Never trust browser redirect; retries safe | Active |
| PaymentProvider is DB-configured and admin-only; test gateway behind `ALLOW_TEST_PAYMENT_GATEWAY` | No credentials in ENV; no-money gateway never in production | Active |
| Every order status change goes through `lifecycle.transition` | Every transition auditable; no status written by hand | Active |
| Fulfilment runs as SYSTEM actor through ordinary services, after payment commits, idempotent per line, claimed under order row lock | No unchecked path into provisioning; a failure never undoes a payment | Active |
| Customers cannot create hosting or domains outside checkout | One way to buy: cart → invoice → payment | Active |
| Ticket attachments validated, stored outside web-served paths, served only as authenticated downloads | Uploads are untrusted input | Active |
| Internal notes excluded from every customer path by one function; customers never see staff email addresses | Notes never reach customers | Active |
| Renewals/upgrades are invoices (`apps.renewals`); figures frozen on a ServiceChange; applied once when paid | One financial record; payment never lost to a service failure | Active |
| Upgrade credit = paid × days left / term days, capped at paid, none once expired; shown as discount line | Roadmap credit rules (superseded by E14 below for what is charged and whether a fresh term starts; the credit formula itself is unchanged) | Active |
| Upgrade keeps the existing term (no fresh term, no moved renewal date) while time remains; only the new plan's own cost prorated for the remaining days is charged, less the same unused-value credit; an already-expired account still gets a fresh term, since none remains to keep | Owner request (E14, 2026-10-03), confirmed after being told it reverses the original Phase 08 design | Active |
| Commission earned when invoice paid in full, ex-tax after discount, frozen; refunds lower unpaid, flag paid; attribution decided at sign-up | Frozen figures; refunds follow money | Active |
| Service lifecycle stage derived from status and paid-through date; daily job uses LifecycleSettings | Reproducible; timings change without migration | Active |
| Ending a service goes through existing hosting services, claimed with a lock-free update; automatic termination off by default | One audited path; never ended twice; failure retryable | Active |
| Approving a cancellation does final billing at once; refunds only for immediate ends via `payments.refund_payment` | Nothing billed for an ending service | Active |
| All business messaging via `notifications.dispatch` and the event registry; essential events ignore preferences | One place decides who is told and how | Active |
| Secret-bearing emails stored encrypted until delivered, then wiped (24 h if undelivered); never open-tracked | Secrets never sit in a log | Active |
| Open tracking is a signal, not proof | Image blocking and scanners distort it | Active |
| Manual payment methods until a Pakistan-available gateway is chosen (2026-09-25) | Stripe unavailable; one adapter class per future gateway | Active |
| **WHMCS-parity experience, own implementation** (Rule 6) | Familiar workflows without copying licensed code/assets or trademark | Active (confirmed in D0) |
| **All menus from one registry; all internal links via URL names; link crawler is a CI gate** (Rule 5) | Zero broken links by construction and by test | Active (confirmed in D0) |
| **Staff area on its own prefix; Django admin reserved for Super Admin emergency use** | Daily work never depends on Django admin | Active (confirmed in D0) |
| **Design track is presentation-only; never changes URL names, services or API contracts** | Re-skin without regressions | Active (confirmed in D0) |
| Reports read the permanent records (money from successful transactions, never stored totals); a report needs `view_reports` and its area's permission; every export is audited and neutralises spreadsheet formulas | A report can never disagree with the ledger; exports leave the portal's control | Active |
| The customer area stays under `/account/`; v2's `/client/` prefix is not adopted; short vanity redirects are added instead | Verification/reset/invoice links already sent must keep working | Active (D0 decision; revisit in D2 if the owner prefers `/client/`) |
| A query-string id is parsed by `core.web.query_id` (junk = not given); NUL characters are stripped from queries and forms by middleware | PostgreSQL rejects NUL and a junk id must never be a server error; SQLite hid both | Active |
| Bootstrap 5.3.3 + Bootstrap Icons + htmx are vendored into `static/vendor/`; our tokens (`theme.css`) sit on top and the brand arrives through `/brand.css` | No framework existed; no runtime CDN; brand colours without inline style | Active |
| One layout shell per audience (public, client, staff), chosen per request by a context processor; `base.html` is an alias | Every page moves into the new shell without being edited; new pages extend a shell directly | Active |
| Status colour is decided in one template tag (`{% status_badge %}`) from the roadmap map; a text label is always shown | One vocabulary; unknown statuses are neutral, never uncoloured | Active |
| Brand (name, logo, favicon, colours, footer, formats) is a database record; primary colour must give 4.5:1 contrast with white; images are validated by content and never SVG | Rule 6; readable buttons; an uploaded file must never run as script | Active |
| `static/css/legacy.css` is a bridge for the old class names and receives no new rules | Retired screen by screen in D3/D4 (customer screens done in D3; the rest goes with D4) | Active |
| The Web Host Era brand, plans and prices are loaded by `manage.py seed_webhostera` into the database (editable in Setup); the accent colour must carry white or dark text; the front page is the storefront for visitors | Brand is configuration (Rule 6); the owner's site is the source of the catalogue | Active |
| The staff console (`apps/console`) only reads: dashboard widgets are loaded separately, shown only to staff who may open what they link to, and every figure equals the list it links to; global search covers only areas the person may open | Rule 5.6; a number never disagrees with its list; search cannot reveal what a menu hides | Active |
| A view that supplies `sidebar` panels (built in `apps/core/portal.py` from URL names) gets a two-column page; every count on a link equals the rows it opens | One sidebar mechanism; a number never disagrees with its list | Active |
| The signed-in areas use one application shell (`layouts/app.html`): a left rail (sections and groups, collapsible, drawer on phones) and a top bar, both generated from the menu registry; customers also get a bottom bar; sign-in uses its own split-screen layout; the store keeps its top bar | A distinct product identity instead of a copy of a billing-panel layout; one source for every menu | Active (D6) |
| **Superseded by D8:** the left rail is gone; navigation is one navy header with section menus (areas and their pages inside), the area's pages under it, and "jump to" for everything | The owner disliked the sidebar layout twice | Active (D8) |
| The rail is one flat list (no expanders); the pages of an area are a strip under the top bar generated from the registry (`groupnav`), and "jump to" lists every page the person may open | The first D7 rail with expanders and nested lists felt awkward to the owner; one way to reach a page | Active (D7) |
| Colour and type live in semantic tokens (`--bg --surface --elevated --text --muted --border --primary --success --warning --danger --info` and tinted `-bg` variants, `--accent-ink` for brand-coloured text) defined for light and dark; components use tokens only, contrast is a unit test and is measured again in a real browser; the theme is Light, Dark or Auto, remembered per browser | Both themes are designed, not derived; readable status colours; no literal colours in components | Active (D7) |
| The staff overview is a KPI strip, a needs-attention list and varied panels, each figure equal to the list it links to and shown only to people who may open that list; a panel that cannot load shows an error with a retry | The dashboard answers what needs attention now; no invented figures; no blank areas | Active (D7) |
| **Technical Staff** is a fifth staff role (view clients, orders, hosting, domains, support; manage hosting, domains, support; nothing else) | Technical people run services and tickets without seeing money, reports, users or settings; only additive RBAC change of D6 | Active (D6) |
| "Users" replaces "Staff & Roles": one screen for customers and every staff role; changes still go through the audited services (never yourself, admin accounts only by a Super Admin, roles need `assign_roles`); Django admin is not in the rail | Rule: no duplicate permission system; Django admin is not the product | Active (D6) |
| Service and domain pages are tab bars (each tab its own URL); the tab for an action is shown only when the person can use it (`portal.can_cancel`) | Rule 5.6: a visible link always opens something usable | Active |
| Customer contacts are read-only pages; staff manage contacts | Self-service contact management stays deferred | Active |
| Every menu, breadcrumb and active state is drawn from the menu registry (`apps/core/navigation.py`); `manage.py check` fails on a bad entry (`core.E001`); a group is shown only when a child is | A menu entry cannot name a missing page, and is shown exactly to people who may open it | Active |
| The link crawler (`apps/core/harness.py`, `test_crawler.py`) is a CI gate: 8 roles, a world with one object in every status, no 404/500/403/dead link, and every GET page reached by some role unless allowlisted with a reason | Rule 5: a broken link is a failed build | Active |
| Old paths live in one `RETIRED` map (`apps/core/redirects.py`), 301 with the query string, named `retired_*`; a moved page keeps its URL name | Rule 5.7: emailed links work forever | Active |
| Production uses `ManifestStaticFilesStorage`; `SITE_URL` must be https (deploy check `core.E002`); no address is typed by hand in a template or code | Fingerprinted assets; emailed links open for customers | Active |
| Dropdown toggles are buttons, never `href="#..."`; the crawler reports empty, `#`, `javascript:` and missing-anchor links | Rule 5: no dead links | Active |
| Abandoned-order expiry only ever cancels a gateway-backed Pending Payment order (`payment_method.provider` set); a manual payment method (bank transfer, JazzCash...) has no timeout | Payments are manual by design (2026-09-25 decision above); staff may legitimately take days to record one, and an automated chase/cancel would undermine that | Active (Phase 17 slice 2) |
| Automatic termination requests a backup first (`backup_before_terminate`, on by default) and waits for a later day's run before actually terminating; the backup is only the adapter's own reference, not a restore flow this project owns | A procedural safety net and audit trail, not a full backup/restore system - disproportionate to build before the WHM adapter itself is live-verified | Active (Phase 17 slice 2) |

------------------------------------------------------------------------

# 28 — CURRENT STARTING TASK

Phases 01–17 and D0–D8 (including D3b) are 🟢, all local (not deployed). See `docs/d0-ui-navigation-audit.md` (the audit and the Section 10/11 mapping), `docs/d1-design-system.md`, `docs/d2-link-integrity.md`, `docs/d3-client-area.md` (dashboard, sidebars, tabs, new customer pages), `docs/d3b-modern-look.md`, `docs/d4-staff-area.md`, `docs/d6-shell-and-users.md`, `docs/d7-visual-system.md`, `docs/d8-header-layout.md`, `docs/d5-visual-qa.md` (Rule 1 found most of this phase already built in D7/D8/D1/E14; closed the pricing-table/375px, 400-page test, empty-state and print-stylesheet gaps), `docs/phase-16-gap-report.md` (both slices, including what was tried and reverted), `docs/phase-17-gap-report.md` (slice 1: automation status, WHOIS lookup; slice 2: abandoned payment/order expiry, backup-before-terminate, attachment retention), `docs/e13-storefront-integration.md` (external/WordPress storefront API, slice 1), `docs/e14-nine-owner-requests.md`
(quote PDF, client/contact, staff permissions, orders, domains, Fraud gate, upgrade proration reversed),
`docs/storefront-connection-guide.md` (screenshots: connecting a storefront, where the credential comes from, what
calls the API today and what a future WordPress plugin would still need to do), `docs/phase-15-follow-up-gaps.md`
(pre-Phase-18 audit: maintenance mode, configurable reminder schedule, ToS acceptance at checkout, cart client
chooser - plus three stale Known-Work rows corrected), and `docs/route-inventory.md` (regenerate with `python manage.py route_inventory`). The E1–E12 owner-request track ran alongside the phases (`docs/e*.md`). The project stays **local** until the owner asks to deploy.

## Start: Phase 18 — Testing & Production Readiness

**Do not start coding before Rule 1 inspection.** Scope is the Phase 18 section above: a pass across the automated suite (unit, integration, authorization, API, billing, domain, provisioning, renewal, upgrade, webhook, notification, link crawler, template lint, email/PDF link tests), a browser pass (desktop/tablet/mobile, customer/staff/checkout/billing/support, overflow/navigation/JS-errors/forms/permissions/every-menu-item), and infrastructure readiness (migrations, HTTPS, DNS, email, payment method(s), registrar, WHM, Redis, Celery, scheduled jobs, monitoring, error tracking). Phase 17's one remaining deferred item (scheduled hosting usage sync) and the pre-existing "WHM API adapter needs live verification" item are both real launch blockers this phase should surface, not silently work around.

Payments stay manual (no gateway). Everything stays local until the owner approves a deployment.

Then 19 (Production Launch) per Section 25. At the end of every session follow the AI Agent Session Protocol (Section 00).

------------------------------------------------------------------------

# 29 — FINAL PROJECT PRINCIPLE

> **Build once. Verify once. Document once. Reuse thereafter.**
>
> **Providers are live configurable connections. Do not hardcode provider credentials or require provider-specific ENV variables.**
>
> **Every link is generated, permission-checked and tested. A broken link is a failed build.**
>
> **It should feel like WHMCS; it must be entirely our own.**
>
> The project must continuously move forward from its current state. No future session should restart a completed phase, recreate an existing feature, or repeat an already-verified implementation without a documented reason.