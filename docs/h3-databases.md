# H3: Database Management

**Source:** next-generation roadmap, H3. **Date:** 2026-10-06. Local only.

## Rule 1 inspection
Only H0's contract existed (list, create, delete databases; list, create, delete users; set password; grant). No screen,
no provider implementation. Reused: the hosting-control gateway, the workspace menu, the File Manager's visual pieces,
the demo provider.

## What was built
- **Contract v1 additions:** `database_limits` (naming prefix, plan limits, minimum password length) and
  `list_database_grants`; the customer's privilege allowlist (`select insert update delete create alter drop index`).
  Create operations take what the customer typed (the provider adds its account prefix); everything else uses full names.
- **Demo provider** (`demo_databases.py`): a simulated service with the rules a real one has: prefixed lowercase names,
  plan limits (10 databases, 10 users), password rules (12 to 128 characters, letters and numbers, no spaces), passwords
  kept only as a hash, per-database privileges, deleting a user or database removes its grants. State is a JSON file
  beside (never inside) the website folder; `demo_hosting_data --remove` clears it. No real database server is involved.
- **Screen** (`views_databases.py`, `databases.html`, `databases.js`): counts against the plan, create a database, list with
  size and users, create/delete users (password field with show/hide and a Generate button using the browser's secure
  random), change a password (dialog), give access with "All privileges" or single boxes, current access table with
  Remove access, delete with a confirmation. Limits disable the form and say why.
- Passwords go straight to the provider; they are not stored, logged, put in a page, or written to the audit trail
  (which records who changed which database or user, and which privileges).

## Verified
16 new tests (the provider's rules; the screen end to end; errors shown not raised; limit behaviour; passwords absent from
audit and pages; a stranger gets 404; a provider without databases shows the not-available page). Hosting, client-area,
crawler, link-integrity, design and navigation suites pass. Browser check at 1440 px: no console errors.

## Update 2026-10-06: phpMyAdmin access and usage
Owner feedback: the page had no way into a database. Added:
- **Contract:** `database_admin_access` (returns `external` = a one-time address on the provider such as phpMyAdmin, or
  `embedded` = WHPanel's own browser; never a password or long-lived token), `list_tables`, `browse_table`.
- **Browse data / phpMyAdmin button** on every database row. An external manager redirects the person there only if the
  address is https (otherwise refused with a message); the demo provider answers `embedded`, which opens WHPanel's
  **read-only database browser**: tables list with row counts, a paged data grid (20 rows a page), values escaped and
  cut at 120 characters, no form or edit control anywhere on the grid. Sample WordPress-style and shop-style tables are
  generated for demo databases. There is no SQL console on purpose.
- **Usage:** total database size as a pill, and a "Storage used by your databases" card with a bar per database.
- 6 more tests (22 in total for H3): embedded browser, refusal of unknown databases/tables/bad pages, read-only grid,
  escaping, secure-address-only redirect, stranger gets 404.

## Update 2026-10-06 (second): a real database manager, not a viewer
Owner feedback: a read-only grid is not phpMyAdmin - rows could not be edited and there was no SQL. Rebuilt:
- **Demo databases are now real SQLite files** (one per database, beside the account state, outside the website folder), seeded
  with sample tables. SQLite is not MySQL (the screen says so), but every query, insert, edit and delete genuinely runs.
- **Manager screen** with three tabs: **Browse** (paged grid; Insert row, Edit and Delete per row when the table has a
  primary key; NULL shown distinctly from the text "NULL"), **Structure** (columns, types, keys, defaults) and **SQL**
  (run one statement; rows or "N rows affected"; errors shown with the typed statement kept; SELECT and COUNT shortcuts;
  the shared confirmation dialog appears for statements containing DROP, TRUNCATE, DELETE or ALTER).
- **Contract:** `describe_table`, `run_query`, `insert_row`, `update_row`, `delete_row` (and `TableData.key_columns`).
- **Safety of the SQL console:** an authorizer refuses ATTACH, DETACH, PRAGMA, virtual tables and file/extension functions, so
  SQL can never reach another database or the engine's settings; one statement per run; a 3 second time limit; a 50 MB file
  cap; results capped at 200 rows; binary values shown as a size. Row edits use validated column names and bound
  parameters, never text glued into SQL. SQL text is never written to the audit trail (only the statement kind and length;
  row edits record the table, key and column names, not values).
- 46 tests for H3 now (24 more): the engine (select/create/insert/update/drop, caps, binary, dangerous SQL refused, runaway
  query stopped, size cap, safe row editing with a quote-injection attempt, no-primary-key tables, NULL), the screens (tabs,
  running SQL, audit without statement text, insert/edit/delete from the screen, errors as messages, escaping, strangers).

## Deferred
Real phpMyAdmin on an actual server (needs the verified cPanel adapter, H14, and the secure-session work of H15; the
`external` path is built and tested), multi-statement SQL, import/export, per-database usage history, remote-access hosts, import/export, and the 375 / 768 px check.
