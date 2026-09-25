# Phase D4 Report: Staff Area Parity and Admin Operations (in progress)

D4 is large, so it is built and verified in stages, each committed with green CI. **Stage status:**

| Stage | Content | State |
|---|---|---|
| **D4a** | Staff dashboard with widgets, global search, audit log viewer, staff front door | ✅ this report |
| **D4b** | Client profile as tabs (each its own URL) | ✅ report below |
| D4c | Standard list pattern (search, filters, bulk-action bar, "Showing X–Y of Z"), confirmation modal on destructive actions | planned |
| D4d | Phase 15 screens: staff users and roles, payment/registrar/email providers, Utilities menu (job queue, WHOIS) | planned |
| D4e | Re-skin the remaining staff templates and delete `static/css/legacy.css` | planned |

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

## Deliberately not built (Rule 5.2: hidden until real)

- **Automation widget (last run of each scheduled job)** and **Staff online**: there is no job-run history or presence tracking yet (Phase 17 and 16). The scheduled jobs exist; their history does not.
- **System health for Redis, Celery workers and WHM reachability**: needs the reliability work in Phase 17; the widget shows what can be checked now.

## Tests

**+57 (`apps/core/test_console.py`): 1574 on PostgreSQL**. Front door per role; dashboard for staff only; exactly the right widgets per role (page and endpoint); no-script fallback; shortcuts by permission; billing figures against the ledger, the income period, refunds; order and support tiles against their lists; failures with working links; domains soon-to-expire (including already-expired); health with a missing provider and with a broken dependency; activity; **a read writes nothing**; search by every kind, every hit opens for the searcher, only permitted areas, short/odd/hostile terms, limit versus total, see-all link; audit log permission, filters, paging, redaction, odd terms; menus per role. **Nine mutation checks** (a widget ignoring its permission, search ignoring a permission, income forgetting refunds or the period, the audit log without its permission, health leaking an exception, staff sent to the profile, no minimum search length, the domain widget dropping overdue ones) all caught. Four older tests that encoded the old front door and the old menu were updated. Browser: dashboard at 1280 and 375 px, all eight widgets load, no JavaScript or network errors, no horizontal overflow at 1200 / 1280 / 1366 / 1440 px for agent, manager and admin.
