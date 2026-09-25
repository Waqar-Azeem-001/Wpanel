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
| Client management | apps.clients / accounts | Built (02) — needs D4 shell |
| Products/Services, addons | apps.products | Built (03) |
| Domain registrations, TLD pricing | apps.domains | Built (04) — real registrar adapter open |
| Servers, cPanel/WHM module | apps.hosting | Built (05) — live WHM verification open |
| Cart / order form | apps.orders | Built (06) |
| Invoices, quotes, transactions, tax, coupons | apps.billing | Built (07) |
| Renewals, upgrades, proration | apps.renewals | Built (08) |
| Orders lifecycle (Pending/Active/Fraud/Cancelled) | apps.orders lifecycle | Built (09) |
| Support tickets, departments, predefined replies, KB | apps.support | Built (10) |
| Email templates, email log, notifications | apps.notifications | Built (11) |
| Cancellation requests, suspension/termination automation | lifecycle | Built (12) |
| Affiliates | apps.affiliates | Built (13) |
| Reports | — | Phase 14 |
| Admin dashboard, setup, utilities, logs | — | Phase 15 + D4 |
| Two-factor auth, security | — | Phase 16 |
| Automation status / cron health | — | Phase 15/17 |
| Announcements, network status, downloads | — | Post-MVP (hidden) |
| Credit balance, add funds, mass pay | — | Post-MVP (hidden) |
| Local payment gateways | manual methods now | Adapter when chosen (Section 26) |

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

# PHASE 16 — SECURITY HARDENING ⬜

RBAC review, **MFA (TOTP) for staff mandatory, customers optional**, secure sessions, CSRF, rate limiting, account lockout beyond throttling, secure passwords, credential protection, server-side billing validation, webhook signature verification, API authentication, sensitive-action confirmation (confirmation modal component + re-authentication for the most sensitive actions), "Login as Client" audited with banner, security headers (CSP, HSTS, X-Frame-Options, Referrer-Policy), open-redirect protection on every `next` parameter.

**Audit:** authentication changes, security changes, user changes, product changes, price changes, invoice changes, payment verification, provisioning, suspension, termination, domain changes, admin actions.

------------------------------------------------------------------------

# PHASE 17 — RELIABILITY & FAILURE HANDLING ⬜

Scenarios: registrar unavailable, WHM unavailable, payment callback delayed, email failure, provisioning failure, duplicate webhook, network timeout, worker failure.

```text
Request → Validation → Create Job → Execute
   Success → Complete
   Failure → Retry?  Temporary → Retry | Permanent → Manual Review
```

Also: scheduled hosting usage sync, expiry of abandoned payment attempts and unpaid orders, backup/hold-before-delete for automatic termination, attachment retention, job-queue status page (linked from Utilities).

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

The first production MVP contains: customer accounts, hosting products, domain products, addons, cart, checkout, invoices, transactions, payment method(s), domain API, WHM/cPanel API, hosting services, domain management, renewals, upgrades/proration, cancellation, **WHMCS-parity customer area**, **WHMCS-parity staff area and dashboard**, support tickets, notifications/email, basic reports, RBAC, MFA for staff, audit logs, REST API foundation, **zero broken internal links (crawler green)**.

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
| 01 Foundation & Architecture | 🟢 | 70 ✅ | ✅ | — | 960bd54 | — |
| 02 Client Management | 🟢 | 98 ✅ | ✅ | — | 3d257c4 | — |
| 03 Products & Addons | 🟢 | 150 ✅ | ✅ | — | 3697eac | — |
| 04 Domain Management | 🟢 | 227 ✅ | ✅ | — | e614249 | — |
| 05 WHM Provisioning | 🟢 | 296 ✅ | ✅ | — | 9c0c517 | — |
| 06 Cart & Checkout | 🟢 | 458 ✅ | ✅ | — | 23168d7 | — |
| 07 Billing & Invoices | 🟢 | 622 ✅ | ✅ | — | ac37961 | — |
| 08 Renewals & Upgrades | 🟢 | 729 ✅ | ✅ | — | 4d00e37 | — |
| 09 Orders & Lifecycle | 🟢 | 788 ✅ | ✅ | — | 8d83271 | — |
| 10 Support | 🟢 | 885 ✅ | ✅ | — | e425a85 | — |
| 11 Notifications & Email | 🟢 | 945 ✅ | ✅ | — | 7d4026a | — |
| 12 Cancellation | 🟢 | 1046 ✅ | ✅ | — | d3f9b82 | — |
| 13 Affiliates | 🟢 | 1134 ✅ | ✅ | — | a2e8989 | — |
| 14 Reports | 🟢 | 1197 ✅ | ✅ | — | f1246ba | — |
| 15 Admin Operations | ⬜ | — | — | — | — | — |
| 16 Security | ⬜ | — | — | — | — | — |
| 17 Reliability | ⬜ | — | — | — | — | — |
| 18 Testing & Readiness | ⬜ | — | — | — | — | — |
| 19 Production Launch | ⬜ | — | — | — | — | — |

"Links" column: phases 01–14 predate the crawler; D2 back-fills it for them. D0 ran a prototype crawler (8 roles, 3,000+ pages: no 404, 500 or 403-after-link) and a hostile-query sweep, now partly in CI (`apps/core/test_link_integrity.py`).

## Design & navigation track

| Phase | Status | Tests | Browser | Links | Commit | Deploy |
|---|---|---|---|---|---|---|
| D0 UI & Navigation Audit | 🟢 | 1220 ✅ | ✅ | prototype crawler: 8 roles, 0 broken | see git log | — |
| D1 Design System & Shell | ⬜ | — | — | — | — | — |
| D2 Link Integrity Harness | ⬜ | — | — | — | — | — |
| D3 Client Area Parity | ⬜ | — | — | — | — | — |
| D4 Staff Area Parity | ⬜ | — | — | — | — | — |
| D5 Visual QA & Polish | ⬜ | — | — | — | — | — |

## Recommended order

D0 → D1 → D2 → D3 → (14 Reports: done) → D4 + 15 Admin Operations → 16 Security → 17 Reliability → D5 → 18 → 19

------------------------------------------------------------------------

# 26 — KNOWN WORK / DEFERRED ITEMS

Use this section whenever something is discovered but intentionally postponed.

**Rule:** If an item is recorded here, do not rediscover or re-plan it unless its status changes.

⚠️ **Re-triage** = the original target phase is already 🟢 but the item is still open/deferred. At the next session touching that area, confirm the real state (Rule 1) and either close it or give it a new target. Do not assume it is done.

## Open / Deferred

| Item | Status | Reason | Target |
|---|---|---|---|
| More email provider kinds (API senders), bounce handling, admin-editable templates | Deferred | SMTP only; no click tracking or bounce handling; templates are files, not editable in admin | Post-MVP |
| MFA | Deferred | Roadmap places MFA in security hardening | Phase 16 |
| Account lockout beyond rate limiting | Deferred | Auth endpoints throttled (10/min); failures audited | Phase 16 |
| Staff admin portal (non-Django-admin UI for roles/status/audit) | Deferred | Django admin used by Super Admin; API covers staff actions | D4 / Phase 15 |
| Error tracking service (e.g. Sentry) and monitoring | Deferred | Structured logging with request IDs in place | Phase 18 |
| TLS termination / certificates | Deferred | Compose Nginx serves HTTP only | Phase 19 |
| Docker Compose stack run end to end | Open | No Docker on dev machine; CI covers PostgreSQL/Redis/Celery | Phase 19 |
| Require verified email before purchase | Deferred ⚠️ Re-triage | Verification tracked; purchase gating belongs to checkout (Phase 06 is 🟢) | Re-triage → Phase 16 |
| Customer self-service sub-user invitations | Deferred | Staff manage contacts; needs customer-side permission design | Phase 15/16 |
| Closing a client suspends users/services | Deferred ⚠️ Re-triage | Service lifecycle rules belong to cancellation phase (Phase 12 is 🟢) | Re-triage |
| Server credentials / WHM adapter | Open ⚠️ Re-triage | Server model was minimal in Phase 03; Phase 05 extended it with WHM connection fields | Re-triage (likely closed by Phase 05) |
| Multi-currency product pricing | Deferred | Single store currency for MVP | Post-MVP |
| Addon-to-product-type compatibility restriction | Deferred ⚠️ Re-triage | Any addon attaches to any product for now | Re-triage |
| Staff pricing table row-actions cramped at 375px width | Deferred | Table scrolls in its own container; no page overflow | D5 / Phase 18 |
| Real registrar adapter needed before launch | Open | Manual adapter is local-only simulation, no real network calls | Before launch |
| Multi-part TLDs (.co.uk, .com.au) not supported | Deferred | Needs a public-suffix list | Post-MVP |
| Outbound transfer auth-code retrieval not implemented | Deferred | Registrar-specific; not in the roadmap's explicit op list | Real registrar adapter |
| WHM API adapter needs live verification before launch | Open | Written to WHM's public docs, never run against a real server | Before launch |
| No customer self-service for suspend/terminate/change-package | Deferred ⚠️ Re-triage | No billing/cancellation workflow to hang these off at the time (08/12 now 🟢); decision log limits self-service to view + request | Re-triage |
| No automatic multi-server capacity-based selection | Deferred | Staff pick manually when a product maps to >1 server | Post-MVP |
| Hosting usage sync is manual, not scheduled | Deferred | Needs Celery beat + a verified live WHM adapter first | Phase 17/18 |
| Guest (anonymous) carts | Deferred | Cart requires a signed-in user | Post-MVP |
| Antivirus scanning of ticket attachments | Open | Uploads are type/content checked, stored privately, only downloaded, but not scanned (e.g. ClamAV) | Before launch |
| Reply-by-email / email-to-ticket | Deferred | Customers reply in the portal; team alerts for unassigned tickets delivered in Phase 11 | Post-MVP |
| SLA timers, escalation, ticket merge, satisfaction rating | Deferred | Not in the roadmap's Phase 10 list | Post-MVP |
| Attachment retention / cleanup | Deferred | Files are kept with their ticket | Phase 16/17 |
| Add-on provisioning; add-ons and custom cycles in staff Add Order | Deferred | Add-ons are recorded against the plan, not provisioned separately | Post-MVP |
| Order suspend/terminate act on hosting only; refunds do not cancel fulfilled services | Deferred | Domains are registrations and are left alone; a refund does not undo what was fulfilled | Post-MVP |
| No real payment gateway adapter (Stripe unavailable in Pakistan) | Decided | 2026-09-25: payments are manual (COD / bank / wallet); each method = an account with instructions; staff record amount, account, transaction ID and date received. Test gateway stays dev-only (`ALLOW_TEST_PAYMENT_GATEWAY`). A real gateway = one adapter class | Until a gateway is chosen |
| Invoices/quotes visible to any contact of the client | Deferred | Restrict financial documents to owner/billing contacts | Phase 15/16 |
| Reminder schedule is fixed | Deferred | Only on/off is configurable | Phase 15 |
| Credit balances (carrying forfeited upgrade credit forward); refunds do not reverse a renewal/upgrade | Deferred | Credit beyond the new price is forfeited and shown as such; a refund never undoes a service change | Post-MVP |
| Downgrades / billing-cycle changes online; renewal of EXPIRED domains | Deferred | Only upgrades are offered online; staff handle the rest (unsuspend-on-payment closed in Phase 12) | Post-MVP |
| PDF text limited to Western European characters | Deferred | Built-in PDF fonts; other scripts print as "?" | Post-MVP |
| Abandoned online payment attempts never expire | Deferred | Stay pending until the gateway answers or the invoice is cancelled | Phase 17 |
| Coupons apply to first payment only | Deferred ⚠️ Re-triage | No per-product limits or recurring discounts yet (Phase 08 is 🟢) | Re-triage → Post-MVP |
| Tax: one rule per country, order-level, exclusive | Deferred | Tax-exempt clients done in Phase 07; no tax-inclusive pricing, state rules or per-invoice rate override | Post-MVP |
| Unpaid orders hold domain names until cancelled | Deferred | No automatic order expiry yet | Phase 17 |
| Domains are not suspended, terminated or marked expired by the lifecycle job | Deferred | The registrar owns what follows expiry; only cancellation acts on domains | Post-MVP |
| Automatic termination has no backup / hold-before-delete step | Deferred | Off by default; deletes data when on | Phase 17 |
| Lifecycle timings are global; refunds on cancellation are a staff decision | Deferred | Not per product or client; refund always a staff decision (suggested only) | Post-MVP |
| Affiliates: no multi-tier or per-product rules, no coupon-code attribution, no payout batch export | Deferred | One level; a rule applies to the whole invoice; payouts recorded by staff | Post-MVP |
| Affiliate fraud beyond hold period and rejection | Deferred | Portal keeps no address/device data by design; staff review and can reject | Phase 16 |
| Terms-of-service acceptance at checkout | Deferred | No ToS page/versioning yet | Phase 15 |
| Web cart needs exactly one client; no client chooser | Deferred | API takes client_id; staff "Add Order" exists (Phase 09) | Phase 15 / D3 |
| Announcements, Network Status, Downloads pages | Deferred (new) | Not in MVP; menu entries hidden per Rule 5.2 | Post-MVP |
| Add Funds / Mass Payment in client area | Deferred (new) | Depends on credit balances | Post-MVP |
| RTL layout (Urdu/Arabic) and translated UI | Deferred (new) | Strings translation-ready from D1; RTL styles later | Post-MVP |
| Local payment gateway adapters (e.g. JazzCash, Easypaisa, bank payment gateways) | Deferred (new) | Choose provider first; one adapter class each | When chosen |
| Phase 14 follow-ups from v2: product-sales, tax-collected, coupon-usage, churn, response-time and affiliate reports; orders-by-status view; charts; heavy reports as Celery jobs with a durable download link | Deferred | The 14 delivered reports cover the MVP list; these need a chart approach (D1 design system) and the job/status pattern | Phase 15 / D4 |
| Reports: no scheduled or emailed reports, saved views or currency conversion; no chargeback data; "cancelled services" covers portal cancellations only | Deferred | On-demand CSV/XLSX/PDF only; the portal has no dispute records | Post-MVP |
| Error pages (403/404/500) are Django defaults: unbranded, no navigation | Open (found in D0) | Rule 5.8; needs the shell | D1 / D2 |
| Ten form fields without a label (product/server status selects, invoice and quote line inputs, invoice cancel reason) | Open (found in D0) | WCAG 2.1 AA; in forms being re-skinned | D3 / D4 |
| Customers have no menu link to their cancellation requests | Open (found in D0) | Reachable only from a service page and notification links | D3 |
| htmx loaded from a CDN and effectively unused; no design tokens, no focus styles, one 172-line stylesheet; brand (logo, favicon, colour, footer) not configurable; no i18n wrapping | Open (found in D0) | v2 Sections 3 and 9 | D1 |
| Static files use default storage, not `ManifestStaticFilesStorage` | Open (found in D0) | A CSS reference to a missing file would not fail collectstatic | D2 |
| Path prefixes: customer area stays `/account/...` (v2 says `/client/...`); add vanity redirects `/login/`, `/register/`, `/store/`, `/knowledgebase/` | Decided in D0 | Links already emailed must keep working; renaming buys only cosmetics | D2 |
| Staff UI still missing for: staff users and roles, payment providers, registrar provider, email provider, audit log viewer; no staff dashboard or global search | Open (found in D0) | Django admin only today | D4 / Phase 15 |

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
| Overdue reminders / dunning | Phase 11 | Daily job: 3 days before due, then 1, 7, 14 days after (on/off in Billing settings) |
| Renewals, upgrade proration | Phase 08 | Delivered |
| Hosting terms start automatically on a paid order | Phase 09 | Fulfilment starts the term with the plan's paid value |
| Unsuspend-on-payment | Phase 12 | A paid renewal lifts a non-payment suspension |
| Staff not notified of reported offline payment | Phase 11 | Team alert to billing staff |
| Basic reports (sales, financial, services, support) with PDF/CSV/XLSX export | Phase 14 | 14 reports, audited, formula-safe exports, API |
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
| Upgrade credit = paid × days left / term days, capped at paid, none once expired; shown as discount line | Roadmap credit rules | Active |
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

------------------------------------------------------------------------

# 28 — CURRENT STARTING TASK

Phases 01–14 are 🟢. Phase D0 (UI & Navigation Audit) is done: see `docs/d0-ui-navigation-audit.md` (the gap report; Sections 10-11 are mapped there as exists / partial / missing) and `docs/route-inventory.md` (414 routes; regenerate with `python manage.py route_inventory`).

## Start: Phase D1 — Design System & Layout Shell

**Do not start coding before Rule 1 inspection of what D0 found.** Then, per the D0 report ("CHANGES → D1"):

1. Vendor Bootstrap 5 and one icon set (and htmx) into `static/`; remove the runtime CDN reference; one stylesheet entry point with our tokens (brand colour from settings).
2. Build the shells `layouts/public.html`, `client.html`, `staff.html`, `document.html`, `email.html`; keep `base.html` as a thin alias until every template is moved. **No URL name, service or API contract changes.**
3. Build the components of Section 9.4, including the single status-badge tag (replacing the 53 hand-coloured badge uses), breadcrumbs, empty states, the confirmation modal, focus styles, money/date/ID filters, i18n-wrapped strings, and fix the ten unlabelled fields.
4. Brand settings in the database (name, logo, favicon, colour, support email, footer).
5. Branded 403 / 404 / 500 templates and a maintenance page.
6. D1 ends with the existing pages rendering inside the new shell, the whole suite and `apps/core/test_link_integrity.py` green, and a browser check at 375 / 768 / 1280 px.

Then D2 (menu registry, the real crawler as a CI gate, vanity redirects, static manifest storage), D3, D4 + Phase 15, and so on per Section 25. At the end of every session follow the AI Agent Session Protocol (Section 00).

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