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

## Deferred
phpMyAdmin or an equivalent browser tool (needs a real provider; the screen says it will appear when offered), per-database
usage history, remote-access hosts, import/export, and the 375 / 768 px check.
