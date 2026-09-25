# Phase D3 Report: Client Area Parity

**Date:** 2026-09-26
**Baseline:** Phase D2 complete (`ce43113`)
**Scope rule (v2 Section 08):** presentation and navigation only. **No URL name was renamed, no service, model or API contract changed.** New pages are additive; `home` keeps its name.

## What was built (Section 10)

| Spec item | Result |
|---|---|
| **Dashboard** (10.3) | `/account/` (name `dashboard`; `home` sends customers there, everyone else where they went before). Welcome, five tiles that are links (Services, Domains, Quotes, Tickets, Invoices), an overdue banner with the amount per currency and a **Pay now** button (straight to the invoice if there is one, else the overdue list), active services with next-due date and *Manage*, recent tickets, a domain search box, the affiliate panel (balance, application state or an invitation). Sidebar: **Your info, Contacts, Shortcuts** (sign-out is a POST) |
| **Sidebars** (10.5) | Any view that supplies `sidebar` panels gets a two-column page (the sidebar sits *under* the page on a phone). **View** panels with counts on services, domains, invoices, quotes and tickets; Actions panels; Billing and Support panels. Built in `apps/core/portal.py` from URL names |
| **Status filters with counts** | `?status=` on hosting, domains, invoices (Unpaid, Overdue, Paid, Cancelled, Refunded), quotes and tickets. A count on a link is the number of rows that link opens (tested for every link on every list). Unknown values are ignored, never an error. The older `?view=open|closed` on tickets still works |
| **Service details with tabs** | Overview · Information · Addons · Upgrade/Downgrade · Request cancellation. Each tab is its own URL; the last two are the pages that already existed, now inside the same tab bar. The Upgrade tab shows only for an active service and the Cancel tab only to someone who can actually request cancellation (Rule 5.6) |
| **Domain details with tabs** | Overview · Auto renew · Nameservers · DNS · Registrar lock · Renew · Cancel. The old POST-only addresses now show their tab on GET and act on POST, then return **to the same tab**. The auto-renew switch is a real form with a Save button (it was inline JavaScript) |
| **New pages** | *View Available Addons* (`/products/addons/`), *Payment Methods* (`/account/billing/payment-methods/`), *Email History* (list and message; never a message that carries a private link), *Contacts* (read-only, all my accounts) |
| **Menus** | Services gained *View Available Addons*; Billing gained *Payment Methods*; the account menu gained *Contacts* and *Email history*; *Home* points at the dashboard. All through the registry |
| **Re-skin** | Every customer screen (35 templates: account and sign-in, store, domain search, help centre, cart, checkout, orders, invoices and quotes, tickets, cancellations, affiliates, notifications) is on Bootstrap components, the status-badge tag and the brand money/date formats; strings wrapped for translation; the browser's `confirm()` boxes replaced by the confirmation modal. The shared document and card partials (`billing/_items`, `_totals`, `renewals/_term_card`, `lifecycle/_service_card`, `support/_conversation`) were converted too, so staff pages that include them look consistent |
| **`legacy.css`** | Deleted every rule only the customer area used (plans grid, method options, conversation, summary lines, totals, offers, event list, notification cards, copy field...); old bare `.card` padding now applies only to the old markup, so a Bootstrap card is not padded twice. What remains is what the staff area (D4) still uses |

## Decisions

- **Tabs for navigation, sidebar for context.** Section 10.5 lists the domain tabs again as a sidebar "Manage" panel; showing both would be the same links twice. Detail pages have a tab bar (Section 10.4) and a sidebar with an Overview and the actions that make sense (Renew, Upgrade, Cancel, Open a ticket).
- **The DNS tab is the existing `dns_add` route** (GET shows the tab, POST adds a record) so no URL name changes.
- **Contacts are read-only.** Self-service contact management stays a deferred item; the page says to contact support.
- **Not built, hidden (Rule 5.2):** Renew Domains (bulk), a separate *Transfer Domains to Us* page (transfer lives on the domain search page and the cart), Security Settings (Phase 16), Announcements, Network Status, Downloads, Add Funds.
- **Logged-out menu unchanged** (Plans, Domains, Help, Sign in, Create account): a public Home page, public Affiliates and Contact Us are not built.

## Defects found while doing it

1. **A hidden bug in my first tab design:** showing "Request cancellation" as a tab for everyone would have linked non-owners to a page they cannot use; an existing test ("the service pages offer cancellation to the owner only") caught it, and the tabs, sidebar and card now share one rule (`portal.can_cancel`).
2. **The invoice tile and its filter disagreed** (the tile counted part-paid invoices, the link listed only unpaid ones). Found while writing the "every count equals its rows" test; "Unpaid" now means everything still owing.
3. **The notification count was invisible** on the brand-coloured bar (accent on accent), visible only in the browser review; it is now a white pill.
4. **DNS form fields too narrow** ("3600" clipped to "360"), found by looking at the screenshot.
5. **A mangled edit** (a newline expanded inside a string by my own edit script) produced a syntax error; caught at once by `manage.py check`.
6. **A latent flaky test found by the PostgreSQL run:** the staff orders search test asserted that the order's reference text was absent from a no-match page, but the search box's placeholder is an example reference (`O000123`), so it failed whenever an order happened to get id 123 (PostgreSQL sequences do not reset between tests; SQLite ids do). Reproduced by advancing the sequence, then fixed to look for the row's link instead.
7. Test infrastructure: the shared world fixture lives in `apps/core/conftest.py`; the harness world now includes an active addon and a failed email.

## Tests

**+81 tests (`apps/core/test_client_area.py`): 1480 (1466 on SQLite + 14 skipped concurrency tests; PostgreSQL run recorded in the tracker).** All 1399 earlier tests pass unchanged apart from two that asserted moved wording (the domain renew form now lives on its tab; the menu lists gained entries).

- **Home and dashboard:** where each kind of person lands; every contact role sees the dashboard; each tile equals the rows its link opens; the overdue banner (one, several, none) and its totals; live services and latest tickets; the three sidebar panels; affiliate states; **another customer's data never appears**; one `<h1>`, no dead links.
- **Sidebars:** every count in every View panel of the five lists equals the rows behind that link; exactly one link is current; junk `status` values (including `<script>`, NUL and 300 characters) never error; older ticket addresses work; drafts never listed; sidebar order on phone vs desktop; pages without a sidebar stay single column.
- **Tabs:** every tab of a service and of a domain opens; each marks itself current; Cancel/Upgrade tabs only where usable, per role; a service or domain of someone else is 404 on every tab; domain actions return to their tab and really change the domain; a GET never changes anything; finished domains are read-only.
- **New pages:** addons (hidden ones absent, no sign-in needed); payment methods (inactive absent); email history (own only, secrets never); contacts (own accounts only); the registry offers them to customers only.
- **Re-skin guards:** no customer template uses an old class name, inline `style`, or `onsubmit="return confirm"`; every customer template is translation-ready; the retired rules are gone from `legacy.css`.
- **Crawler (D2)** still green for all eight roles, including the new pages (the coverage floor found every new page linked).
- **Mutation checks:** twelve faults introduced in turn and each caught (counts ignoring the filter, cancel tab for everyone, the dashboard reading every customer's services, home ignoring whether you have an account, secret emails listed, an action returning to the wrong tab, a tile and its link disagreeing, a junk status crashing, sidebar order, an inline style returning, hidden addons shown, other people's contacts shown).
- **Browser pass** (headless Chromium, 35 customer pages at 375 / 768 / 1280 px plus dark mode on four, tabs, sidebar filter, keyboard, the Billing menu): **zero findings** (no overflow, JavaScript errors, failed requests, unlabelled fields, dead links or external assets; sidebar under the page on a phone and beside it on a desktop). Screenshots reviewed by eye.

## D3 exit criteria

| Criterion | State |
|---|---|
| Every customer screen in Section 10 on the new shell, none left on the bridge stylesheet | ✅ (staff screens remain for D4) |
| Dashboard, sidebars with counts, service and domain tabs | ✅ |
| Every new page in the menu registry, crawler and coverage floor green | ✅ |
| No URL name renamed; no service or API change | ✅ |
| Browser check at 375 / 768 / 1280 px, keyboard, dark mode | ✅ |
| CI green | see tracker |

## Known limitations (all scheduled)

- **Staff pages still use `legacy.css`** (D4). Two shared partials were converted early, so staff invoice and order pages already use Bootstrap tables.
- **Ticket detail and invoice detail have no sidebar** (they are wide documents); lists do.
- **The public "Home" landing page, Contact Us and a public Affiliates page** are not built.
- **Bulk renewal, customer-managed contacts, Security Settings, Announcements, Network Status, Downloads and Add Funds** are not built (deferred or later phases) and are not shown.
- **Page strings are wrapped for translation, but no translation file exists yet** (English only).
