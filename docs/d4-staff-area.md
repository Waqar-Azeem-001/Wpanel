# Phase D4 Report: Staff Area Parity and Admin Operations (Phase 15 screens included)

D4 was large, so it was built and verified in stages, each committed with green CI. **Stage status (all done):**

| Stage | Content | State |
|---|---|---|
| **D4a** | Staff dashboard with widgets, global search, audit log viewer, staff front door | ✅ this report |
| **D4b** | Client profile as tabs (each its own URL) | ✅ report below |
| **D4c** | Standard list pattern ("Showing X–Y of Z", bulk-action bar), confirmation modal on destructive actions | ✅ report below |
| **D4d** | Phase 15 screens: staff users and roles, email provider, registrar | ✅ report below |
| **D4e** | Re-skin the remaining staff templates and delete `static/css/legacy.css` | ✅ report below |

## D4a: what was built

New app `apps/console` (the staff console). It only **reads** existing records through their own services and models; nothing in it writes (tested).

| Item | Result |
|---|---|
| **Staff front door** | `/` sends staff to the dashboard `/staff/` (`console:dashboard`; `home` keeps its name). Customers still go to theirs, visitors see the storefront |
| **Dashboard (Section 11.2)** | Eight widgets, each loaded on its own with HTMX (a slow widget never blocks the page; a `<noscript>` link opens each one without JavaScript): **Billing** (income today / month / year from succeeded payments less refunds; unpaid total; overdue; payments waiting to be confirmed), **Orders** (pending, active, fraud, cancelled, latest five), **Support** (open, customer replied, unassigned, urgent, who is waiting), **Provisioning failures**, **Domains expiring in 30 days**, **Services and renewals** (lifecycle stages, cancellations waiting), **System health** (database, cache, email provider, registrar, servers; a broken dependency shows as a problem, never as an error page or a leaked message), **Recent activity** |
| **Permissions (Rule 5.6)** | A widget is shown only to staff who may open what it links to, and its address answers 403 to anyone else. Support agent: 6 widgets; manager: 7 (adds activity); admin and super admin: all 8. Shortcut buttons (New client / invoice / order / ticket) likewise |
| **Every figure equals the list it links to** | Tested: order counts equal the order lists, the support tiles equal the ticket lists they open (a first version disagreed: "unassigned" counted open tickets but linked to all tickets; the links now carry `status=active`), the income figures equal a sum over the ledger |
| **Global search (Section 11.1)** | One box in the staff bar (`/staff/search/`): clients (name, email, reference), invoices (number), orders, domains, hosting accounts, tickets. Each kind uses that area's own search and appears only if the person may open that area, so search never reveals what a menu would hide. At most six per kind with the true total and a **See all** link to the filtered list. Terms under two characters are refused politely; hostile terms (`%`, `_`, backslash, NUL, SQL, HTML, 400 characters) are safe |
| **Audit log viewer (Utilities)** | `/staff/utilities/audit/` needs `view_audit_log` (manager, admin, super admin): search by action, person, record or request id; filter by area; 50 per page; secrets shown as `[redacted]`. Django admin is no longer needed to read the log |
| **Menus** | The staff menu now starts with **Dashboard**; **Utilities** gains **Audit Log** (so managers now see Utilities too). The staff bar's breakpoint moved to 1400 px: at 1200-1366 px the full bar (menus, search, Setup, bell, account) overflowed the screen, found by measuring in the browser; below 1400 px it collapses to the menu button |

## D4b: the client profile as tabs (Section 11.3)

The one long profile page is now a **header** (name, reference, status, quick actions: New invoice, New quote, New order, Open a ticket, each by permission) plus **tabs, each its own URL**:

| Tab | Address | Shown to |
|---|---|---|
| Summary | `/staff/clients/<id>/` (unchanged) | anyone who may open the profile: details, status form, counts, five recent events |
| Profile | `.../edit/` (unchanged) | `manage_clients` (the edit form now sits inside the tabs) |
| Contacts | `.../contacts/` (GET shows the tab; POST adds; role change and removal return **to this tab**) | profile viewers; changes only for `manage_clients` |
| Products/Services, Domains, Billable Items, Invoices, Quotes, Transactions, Tickets, Emails, Cancellations, Affiliate, Log | `.../tab/<name>/` | `view_hosting`, `view_domains`, `view_billing` (x4), `view_support`, `view_settings`, hosting or domains, `view_affiliates`, `view_audit_log` |
| Notes | `.../tab/notes/` | profile viewers (edit link for `manage_clients`) |

- A tab appears exactly when the person may open it; asking for one directly is refused (302/403) otherwise (tested per role: agent sees 11 tabs, manager 14, admin 15).
- Each list tab shows this client's records only, 25 to a page with "Showing X-Y of Z"; a count next to the tab name equals the rows the tab opens (tested); every link in every cell opens; dates and money use the brand formats.
- Nothing moved that a URL depended on: `detail`, `edit`, `contact_add`, `contact_role`, `contact_remove`, `status` keep their names and paths; `tab` is new.
- Wide tab bars scroll sideways on a phone and wrap on a desktop.
- Tests: **+30** (`apps/core/test_client_profile.py`); three older tests that looked for records on the profile page now look at the right tab; **eight mutation checks** (permission ignored, invoices not scoped to the client, counts not scoped, any staff adding contacts, contact changes returning to the wrong page, the summary treated as a tab, notes edit link for everyone, paging removed) all caught. Browser: profile, invoices, contacts, log, notes and edit at 1440 and 375 px, no errors or overflow.

## D4c: the standard list (Section 11.4)

- **"Showing X-Y of Z"** appears under **every paged list** (staff and customer) from the shared pager, and follows the filters and the page; previous/next appear only when there is more than one page and keep the filters.
- **Bulk actions** (`apps/core/bulk.py`): a "With selected" bar with a select-all box, on **Tickets** (assign to me, mark resolved, close, set priority; `manage_support`) and **Invoices** (issue drafts, cancel unpaid ones with one reason; `manage_billing`). A bulk action is only a loop over the ordinary service call, so it has the same permission checks, rules and audit trail as doing it one by one; **one record failing never stops the others** and the outcome says how many were done and why the rest were not; ids that match nothing are ignored; at most 200 ticked rows; junk ids are dropped; the return address is the filtered list but never another site. The bar is shown only to people who may use it, and the form asks first in the modal.
- **Confirmation modal everywhere:** the last nine browser `confirm()` boxes (terminate hosting, cancel/suspend/fraud/terminate an order, cancel/issue an invoice or quote, remove a price, delete a KB article, delete a billable item, renew a domain) now use the accessible modal; a test forbids the old form.
- Priority badges use the status colours (urgent red, high amber).
- Tests: **+21** (`apps/core/test_lists.py`); **eight mutation checks** (one failure stopping the rest, an open redirect, no id cap, missing permissions on either bulk view, the summary line removed, the browser confirm back, the bar shown to readers) all caught. Browser: select-all ticks every row, the modal appears, confirming applies the change and shows "6 ticket(s) re-prioritised".
- Not done in D4c (recorded): bulk actions on orders, domains, hosting, commissions and failed emails (each needs its own careful per-record rule; the mechanism is ready); saved filters; column chooser; CSV export from lists (reports already export).

## D4d: the screens that used to need the Django admin (Phase 15)

New services (rules and audit in one place, like every other module) and three Setup screens. Django admin is no longer needed for staff accounts, the email provider, the registrar or the audit log.

| Screen | What it does | Who |
|---|---|---|
| **Setup > Staff & Roles** (`/staff/setup/staff/`) | List staff with role, status, last sign-in; **add** a member of staff (an email with a one-time link to set their own password: nobody ever sees a password); change a role; suspend and reactivate with an audited reason | look: `view_users` (managers too); add / role: `manage_users` + `assign_roles` (admin, super admin); suspend: `manage_users`. Only a Super Admin can create or change an admin; nobody can change their own role or status |
| **Setup > Email Provider** (`/staff/setup/email/`) | Add, edit and delete the outgoing mail server; **only one is in use** (switching on one switches the others off in the same transaction); **Send a test email** through any provider and see the real result | `view_providers` / `manage_providers` (admin, super admin) |
| **Setup > Domain Registrar** (`/staff/setup/registrar/`) | The same for the registrar; credentials are a JSON object in the adapter's own shape; the manual registrar is the only kind today | same |

- **Secrets:** the SMTP password and registrar credentials are **encrypted**, are never shown again (edit forms leave them blank; blank keeps the stored value) and never reach the audit log, the pages or a test-email failure message (tested: a failure that mentions the password is reduced to the error's class name).
- **Validation before writing:** both providers are validated (STARTTLS and SSL cannot both be on, JSON must be an object, ports in range) and nothing is saved on a refusal; the active provider cannot be deleted; a registrar that has registered domains cannot be deleted.
- New event `account.staff_welcome` (essential, sensitive: stored encrypted until delivered, then wiped) with its email template.
- **Payment providers (gateways) are deliberately not built**: payments are manual until the owner chooses a gateway (decision recorded 2026-09-25); payment *methods* already have their screen.
- **Utilities > Job queue and WHOIS** stay hidden: there is no job-run history yet (Phase 17) and no WHOIS integration.
- Tests: **+36** (`apps/core/test_setup.py`; 1661 on PostgreSQL), **ten mutation checks** (password stored in clear, a blank password wiping the stored one, two active providers, the test email echoing the password, an active provider deletable, an admin creating admins, create without the permission, customers editable as staff, registrar credentials in clear, a used registrar deletable) all caught. Browser: the three screens at 1440 px, no errors or overflow.

## D4e: every screen on the components; the bridge stylesheet deleted

- **61 templates** re-skinned by a checked script plus hand fixes: every button is a Bootstrap button (primary, outline, small, danger), muted text, right-aligned numbers, responsive tables, alert boxes, stat tiles, timelines, badges; the five section menus and three period selectors are **Bootstrap tabs with the current one marked**; every card has a `card-body` (Bootstrap pads the body, not the card); ticket priority uses the status badge.
- **`static/css/legacy.css` is deleted** (and no template loads it). What the staff screens still need is now named, documented components in `theme.css` ("Page width, form layout and small layout components": `.field`, `.filters`, `.row-form`, `.grid-form`, `.inline-form`, `.split`, `.meta`, and a few table helpers), written once on the design tokens.
- Guards (`apps/core/test_reskin.py`, 8 tests, 7 mutation checks all caught): no template uses a class name from the old sheet, every `btn` is a Bootstrap button, **no bare card**, every component still used is defined, the deleted sheet stays deleted, the section menus are tabs with a current item, priority is a status badge, no fixed-width inline styles.
- Browser sweep: **192 staff pages** (every page reachable from the staff menus plus detail pages) at 375, 768, 1280 and 1440 px: no JavaScript errors, no failed requests; one 8 px overflow on the hosting page at 375 px (an action row that did not wrap) was found and fixed. Screenshots of invoice, order, hosting, clients, brand and setup pages reviewed by eye.

## Deliberately not built (Rule 5.2: hidden until real)

- **Automation widget (last run of each scheduled job)** and **Staff online**: there is no job-run history or presence tracking yet (Phase 17 and 16). The scheduled jobs exist; their history does not.
- **System health for Redis, Celery workers and WHM reachability**: needs the reliability work in Phase 17; the widget shows what can be checked now.

## Tests (D4a)

**+57 (`apps/core/test_console.py`): 1574 on PostgreSQL**. Front door per role; dashboard for staff only; exactly the right widgets per role (page and endpoint); no-script fallback; shortcuts by permission; billing figures against the ledger, the income period, refunds; order and support tiles against their lists; failures with working links; domains soon-to-expire (including already-expired); health with a missing provider and with a broken dependency; activity; **a read writes nothing**; search by every kind, every hit opens for the searcher, only permitted areas, short/odd/hostile terms, limit versus total, see-all link; audit log permission, filters, paging, redaction, odd terms; menus per role. **Nine mutation checks** (a widget ignoring its permission, search ignoring a permission, income forgetting refunds or the period, the audit log without its permission, health leaking an exception, staff sent to the profile, no minimum search length, the domain widget dropping overdue ones) all caught. Four older tests that encoded the old front door and the old menu were updated. Browser: dashboard at 1280 and 375 px, all eight widgets load, no JavaScript or network errors, no horizontal overflow at 1200 / 1280 / 1366 / 1440 px for agent, manager and admin.
