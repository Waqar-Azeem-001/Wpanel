# Phase 02 Gap Report: Client Management

**Date:** 2026-09-24
**Baseline:** Phase 01 complete (`960bd54`)

## FOUND

Phase 01 provides users, roles, the `view_clients` and `manage_clients` permissions, the audit log, the email service, the API foundation and the account pages.

## PARTIAL

- **Customer registration** created a user but no business account. It now also creates a client with the new user as owner. This integration change is allowed by Rule 2 because Phase 02 requires it.

## BROKEN

None.

## MISSING (now implemented)

Everything client-specific was missing: a client model, the link between users and clients, staff client screens, the client API and the customer account page.

## REUSE

| Reused | How |
|---|---|
| `accounts.view_clients` / `manage_clients` | Staff API (`HasPortalPermission`) and staff pages (the new `portal_permission_required` decorator) |
| `audit.services.record` | Every client change is audited with `client_id`; the audit log drives the Activity timeline |
| `notifications.services.send_email` | The "set your password" welcome email for contacts added by staff |
| The Phase 01 password-reset token and page | The welcome link uses them; no second password-setting flow |
| `ServiceError`, error format, pagination, filters | All client endpoints |

## CHANGES

- **Models:** `Client` holds company, contact, address, country and tax ID, currency, status (active, inactive, closed) and internal notes, with a reference like `C000123`. `ClientContact` links a user to a client with the role owner, billing or technical, and each user can appear only once per client.
- **Services** (`apps/clients/services.py`):
  - create a client, linking an existing customer or inviting a new one with a set-password email;
  - create a client at registration;
  - update, with staff and customer seeing different sets of fields;
  - set status;
  - add, re-role and remove contacts, with a guard that keeps at least one owner;
  - search by name, company, email, phone, reference or contact email;
  - an activity query.
- **Rules:**
  - Staff accounts cannot be client contacts.
  - Customers see only clients they belong to.
  - Only owner and billing contacts can edit their client, and not once it is closed.
  - Customers cannot change currency, status or notes, and never see notes.
- **Staff API:** `/api/v1/clients/` supports list (search, status, country, currency and date filters, ordering), create, retrieve and PATCH. Actions are `status/`, `contacts/` (POST), `contacts/{id}/` (PATCH role, DELETE) and `activity/`. Clients cannot be deleted.
- **Customer API:** `/api/v1/me/clients/` supports list, retrieve and PATCH, and includes `my_role`.
- **Staff web:** `/staff/clients/` has a list with HTMX live search and a status filter, add, edit, and a client profile page with details, status, contacts, an account records placeholder and the activity timeline.
- **Customer web:** `/account/client/` shows the user's account details, editable only by owner and billing contacts.
- **Other:** Django admin shows clients read-only, so every change goes through the service layer. The navigation links depend on role. The API schema now has stable names for its choice fields, for generated mobile clients.

## TEST PLAN

There are 28 new tests, bringing the suite to 98.

- **Registration:** it creates a client with the new user as owner.
- **Staff API:** creating a client with a new or existing owner, staff blocked as contacts (rolls back), country validation, audited updates, status changes, search and filters, contact add, re-role and remove, last-owner guard, other clients' contacts unreachable, and the activity timeline.
- **Authorization:** customers are denied the staff API, support agents can view but not modify, and deletion returns 405.
- **Customer side:**
  - users see only their own clients, and notes are hidden;
  - the owner's edits ignore restricted fields;
  - technical contacts are denied edits;
  - other clients return 404;
  - a closed client cannot be edited;
  - the welcome link sets a password.
- **Web:** login and permission gates, support agent read-only, HTMX partial, create, edit and status, validation errors, contacts, customer page editable vs read-only, 404 on other clients, and role-based navigation.
- **Browser check:** headless Chromium at 1366, 768 and 375 px wide covering staff list, create, profile, add contact, status change, HTMX search and the customer page. No horizontal page overflow and no JS errors.

## PHASE 02 EXIT CRITERIA

| Criterion | State |
|---|---|
| Existing implementation inspected, no duplicates | ✅ |
| Customer and staff endpoints share one service layer | ✅ |
| New behaviour and authorization tested, full suite green | ✅ 98 passing (SQLite) |
| Migrations created, `makemigrations --check` clean, schema valid with 0 warnings | ✅ |
| Browser check (desktop, tablet, mobile) | ✅ |
| CI green on PostgreSQL + Redis | ✅ `.github/workflows/ci.yml` on `3d257c4` (badge confirmed) |
| Commit after verification | ✅ `3d257c4`, pushed to `main` |

## Known limitations / deferred

- The account records section (orders, services, domains, invoices, transactions, tickets, quotes, cancellations, affiliate) is a placeholder until phases 04–13 add those models.
- Customers cannot invite their own sub-users yet; staff manage contacts. Deferred to Phase 15/16, with a decision needed on customer-side permissions.
- A staff member cannot also be a client contact. Revisit if staff need personal customer accounts.
- Closing a client does not suspend its users' logins or services. Service suspension belongs to Phase 12.
