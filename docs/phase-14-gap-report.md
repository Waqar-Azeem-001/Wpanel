# Phase 14 Gap Report: Reports

**Date:** 2026-09-25
**Baseline:** Phases 01-13 complete (`6d437f7`)

## FOUND

- **Permissions** `view_reports` / `manage_reports` already existed; only Managers and above hold `view_reports` (Support Agents do not). No permission changes were needed.
- **The data was all there**, and all of it permanent records: orders, invoices, **transactions** (the source of truth for money), clients, hosting, domains, tickets, cancellations, commissions. Every earlier phase kept its figures reproducible, which is what makes reports trustworthy.
- **PDF rendering** existed for invoices and quotes (reportlab, deterministic); **no CSV or XLSX** output existed anywhere, and no XLSX library was installed.
- The only "reports" so far were per-module lists and small overview pages (support overview, email statistics, affiliate overview, the lifecycle overview). None could be exported, and none covered sales or income.
- Two roadmap debts named this phase: "Basic reports" (the MVP list) and an exports requirement (PDF, CSV, XLSX).

## PARTIAL / BROKEN

None found in earlier phases' figures; every report was cross-checked against the permanent records (tests compare the income report with the transaction ledger).

## MISSING (now implemented)

Everything on the roadmap's Phase 14 list: **Sales** (daily performance, orders, new customers), **Financial** (daily, monthly and annual income, unpaid invoices, transactions, refunds/disputes), **Services** (active hosting and domains, expiring, suspended, cancelled), **Support** (open tickets, resolved tickets, department statistics), and **exports** in PDF, CSV and XLSX.

## REUSE

| Reused | How |
|---|---|
| Transactions, invoices, orders, tickets, hosting, domains, cancellations | Read directly; no new tables, no copied numbers, **no migrations** |
| `billing.calculations.money` | Rounding |
| The lifecycle app's stage/expiry rules and `ServiceChange` | "Renewal invoiced?" on expiring services |
| reportlab (already a dependency) | The PDF export |
| Existing permissions, `audit.record`, the `run_action`/`ServiceError` conventions | Access control, export audit, error handling |
| The stats/table/filter styling | The pages (no new CSS) |

## CHANGES

- **New `apps.reports` app (no models).** `definitions` (what a report and its parameters are), `builders` (the 14 reports), `exports` (formatting and the three file types), `services` (running, permissions, audited exports, headline figures), pages and API.
- **Fourteen reports in four groups**, each a table plus headline figures, run for a date range (default the last 30 days) and, where it makes sense, grouped by day, month or year:
  - **Sales:** *Sales performance* (orders placed, their value, cancelled/failed, new customers; every day appears, quiet ones as zero); *Orders*; *New customers* (with where they came from: direct or which affiliate).
  - **Financial:** *Income* (received, refunded and net, per day / month / year, per currency); *Unpaid invoices* (as at today, with an aging breakdown: not yet due, 1-30, 31-60, 61-90, over 90 days); *Transactions* (every payment and refund, with counts of pending and failed); *Refunds and failed payments*.
  - **Services:** *Active services* (hosting, domains or both); *Expiring services* (within N days, including those already past their date, and whether a renewal has been invoiced); *Suspended services* (and whether by staff or automatically for non-payment); *Cancelled services* (carried-out cancellation requests, reasons, refunds).
  - **Support:** *Open tickets* (age, assignee, waiting for us, urgent); *Resolved tickets* (time to resolve); *Department statistics* (opened, resolved, open now, average hours to resolve).
- **Money is counted from the permanent records.** Income is the sum of *successful* payments and refunds on the day they happened; a payment a customer has reported but staff have not confirmed is not income until it is. A test proves the income report equals the transaction ledger. Amounts stay per currency and are never added across currencies; sales value is in the store currency with a note if other-currency orders exist.
- **Days are the site's days** (its time zone), not UTC days: an order at 21:30 UTC is counted on the next day in Karachi (tested). A range includes both its first and last day.
- **Headline figures** on the Reports page (income today and this month, invoices overdue, orders today, new customers today, services expiring in 30 days, open tickets), each shown only to someone who may run the report behind it.
- **Exports: CSV, XLSX and PDF** of exactly what is on the screen (the download links repeat the parameters). CSV is pure data with a byte-order mark so Excel reads accents correctly; XLSX has real numbers, dates and money formats, a bold header and the headline figures; PDF is a landscape A4 document with repeating header rows and page numbers, repeatable byte-for-byte. The page shows the first 200 rows of a long report and says so; the download has them all (up to 50,000 rows, and it says if that limit was hit).
- **Spreadsheet formula injection is closed off.** A client name, ticket subject or payment reference that starts with `=`, `+`, `-`, `@`, tab or return would run as a formula when the file is opened in Excel. CSV values get a leading apostrophe; XLSX text cells are stored strictly as text (and also get the apostrophe). Verified end to end with a customer literally named `=HYPERLINK(...)`.
- **Access.** A report needs `view_reports` *and* the permission for what it reads (financial reports need billing, service reports hosting and domains, and so on), so a report is never a way round what someone could not open in the module. Someone with reports and support access but nothing else sees only the three support reports and only the support figure. Anonymous users go to sign-in; an unknown report is 404.
- **Exports are audited** (`report.exported`: who, which report, format, parameters, row count): a file leaves the portal's control.
- **API.** `GET /api/v1/reports/` (the catalogue and each report's parameters) and `GET /api/v1/reports/<slug>/` (JSON with columns, rows, headline figures, notes; money as exact strings, never floats; add `?export=csv|xlsx|pdf` for a file). Bad dates or numbers are 400 with a message, a stranger is 403.
- **Dependency:** `openpyxl` (added to `requirements.txt`). Navigation: *Reports* for staff who hold `view_reports`.

## Defects found and fixed while building it

1. **The API returned money as floats** (`80.0` for `80.00`): DRF's JSON encoder turns a Decimal into a float. Found by a test that compares exact strings; values are now converted explicitly.
2. **Two test-data mistakes of my own, both caught by tests:** a hosting account and a domain in the fixtures shared a name, so a lookup keyed by name collided; and a "limited" reporter I had built from a support agent turned out to have every view permission already (agents can view most modules), so the limited user is now built from the two permissions alone.
3. **My own habits, again:** several placeholders and hacks (`if False`, `__import__`, a junk `__all__`, an unused helper, a garbled expression) crept into the first drafts and were removed before anything ran; the shell tool also silently drops backslash-newline sequences from scripts, which cost one failed edit (redone with the editor).

## TEST PLAN

**63 new tests: 1197 in total on PostgreSQL** (1183 on SQLite; the 14 concurrency tests skip there). All pass against a local PostgreSQL 17.5 before pushing (date grouping compiles to different SQL on each database).

- **Parameters and formatting:** default range, both ends included, every bad range and parameter refused with a message, the two-year limit for day-by-day, ignored parameters; how every kind of value reads on screen and as a file; all six formula starters neutralised; ordinary text and negative numbers untouched.
- **Every report's numbers:** dense daily/monthly/annual periods; drafts and cancelled orders; other-currency orders flagged; the site's time zone; the end date; affiliate sources; income from confirmed money only, refunds subtracted, month and year totals equal the ledger, currencies kept apart; **aging of unpaid invoices** with paid, cancelled, draft and part-paid cases; transactions and refunds/failed payments with reasons; active, expiring (lapsed first, renewal-invoiced flag, hosting *and* domain windows), suspended (cause), cancelled; open/resolved tickets and department statistics; the row cap; who may run what; the headline figures per permission.
- **Files:** CSV content, byte-order mark and neutralised formulas; XLSX read back with openpyxl (numbers are numbers, money and date formats, bold header, no formula cells); PDF is a real, repeatable, multi-page document and survives characters the built-in fonts lack; empty reports export; unknown format refused; exports audited and named by report and day.
- **Pages and API:** the index by permission, the report page with filters and download links, bad input explained rather than crashed (including on a download), 403/404/redirect rules, downloads as attachments that cannot be sniffed or cached, long reports cut on screen but complete in the file, a hostile customer name in the download; the API catalogue, JSON shape, exact money strings, errors, permissions and the three file types.
- **Mutation checks:** twelve rules were broken in turn (unconfirmed money counted, refunds ignored, exclusive end date, formula guard off, area permissions ignored, drafts counted, exports not audited, row cap ignored, unpaid list including paid invoices, refund/payment mix-up, expiry window ignored, XLSX text typing) and each was caught, except two that are not gaps: the XLSX text typing is a second layer behind the apostrophe prefix (equivalent), and the expiry-window mutation exposed a missing test, which was then added and now catches it.
- **Browser check:** headless Chromium at 1366, 768 and 375px: a support agent is refused; the manager sees the headline figures and opens all fourteen reports (each renders a table, no overflow); the unpaid-invoice aging, lapsed and suspended services with their causes, the refund and rejection reasons and the cancellation reason are all present; changing the grouping to month shrinks the income table and an inverted date range is explained; CSV, XLSX and PDF are downloaded from the page (right file types and names) and the CSV of a customer named `=HYPERLINK(...)` has it neutralised. No JavaScript errors.

## PHASE 14 EXIT CRITERIA

| Criterion | State |
|---|---|
| Existing implementation inspected, no duplicates | ✅ reads the permanent records; no copied figures |
| Sales, financial, services, support reports | ✅ 14 reports |
| Exports: PDF, CSV, XLSX | ✅ tested, formula-safe, audited |
| Authorization tested; full suite green on SQLite and real PostgreSQL | ✅ 1197 |
| Migrations (none needed, `makemigrations --check`), OpenAPI with no warnings | ✅ |
| Browser check (desktop, tablet, mobile) | ✅ |
| CI green on PostgreSQL + Redis | ✅ Run 36160185921 on `f1246ba` (first run) |

## Known limitations / deferred

- **No charts.** Tables and headline figures only; a graph would need a chart library or hand-drawn SVG (a Phase 15 dashboard candidate).
- **No scheduled or emailed reports** and no saved report views; reports run on demand.
- **No "disputes" data:** the portal has no chargeback records, so *Refunds and failed payments* is the closest, and says so.
- **Cancelled services covers cancellations requested through the portal**, not accounts terminated by hand or by the lifecycle job (those appear in the audit log).
- **Money is per currency, never converted;** there is no exchange-rate handling.
- **Reports run live against the database** with no caching or pre-aggregation; fine at this scale (rows are capped, dates are indexed) but a data-warehouse table would be the next step at high volume.
- **The PDF uses the built-in fonts** (Western European text only; other scripts print as "?"), the same limit as invoices.
- **Affiliate, support first-response-time and per-product revenue reports** are not on the roadmap list and were not added (the affiliate module has its own overview).
