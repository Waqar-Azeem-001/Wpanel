# Phase 05 Gap Report: Hosting / WHM Provisioning

**Date:** 2026-09-25
**Baseline:** Phases 01-04 complete (`df33060`)

## FOUND

Phase 03 built `apps.products.models.Server` **deliberately minimal**, with an explicit note that Phase 05 must extend that same model with connection details rather than create a second one. Phases 01-04 also provide `view_hosting`/`manage_hosting` (already granted correctly), the client/contact model hosting accounts attach to, the audit log, and the request-then-complete + adapter-interface pattern Phase 04 established for domains, which this phase mirrors closely.

## PARTIAL / BROKEN

None - nothing hosting-account-specific existed yet; `Server` existed but without provisioning credentials.

## MISSING (now implemented)

Everything: the WHM adapter interface, a real WHM API adapter, `HostingAccount`, and staff/customer screens.

## REUSE

| Reused | How |
|---|---|
| `apps.products.models.Server` | Extended (not duplicated) with `kind`, `api_port`, `api_username`, encrypted `api_token`, `use_ssl`, `verify_ssl` - exactly as Phase 03 instructed |
| `accounts.view_hosting`/`manage_hosting` | Already existed, already granted correctly; no permission changes needed |
| `apps.domains`'s request-then-complete pattern, adapter interface shape, and the non-atomic-on-failure fix | Applied to hosting from the start (see Changes) rather than rediscovering the same bug |
| `apps.clients.services.contact_role` | Self-service/request authorization, same as domains |
| **New:** `apps.clients.services.single_contact_client` | Was a private, duplicated helper in `apps.domains.views`; promoted to a shared service function and both apps now call it, instead of carrying two copies |
| `apps.core.crypto` | `Server.api_token_encrypted`, same pattern as `EmailProvider`/`RegistrarProvider` |
| `notifications.EmailProvider`'s Django-admin-only precedent | Deliberately *not* repeated for `Server` - staff already had web pages for it from Phase 03 (`/staff/products/servers/`), so those were extended in place rather than adding a second, admin-only path |

## CHANGES

- **`Server` extended, not duplicated:** `kind` (Manual / WHM API) selects the adapter; `api_port`, `api_username`, `use_ssl`, `verify_ssl` plus an encrypted `api_token`. The existing staff server form/pages/API from Phase 03 were extended in place (new fields made optional with model-level fallbacks so a pre-Phase-05 minimal submission still works) rather than building a second server-management surface.
- **Adapter interface** (`apps/hosting/adapters/`): `HostingAdapter` (ABC) with exactly the roadmap's WHM Operations - `create_account`, `suspend_account`, `unsuspend_account`, `terminate_account`, `change_package`, `get_status`, `get_usage`.
  - **`ManualAdapter`**: local-only simulation, no network calls - same honest limitation as Phase 04's domain adapter, for the same reason (building and testing the full lifecycle before a real server is available).
  - **`WhmApiAdapter` - the one new thing Phase 04 couldn't do:** unlike domain registrars (many incompatible APIs), WHM is a single, stable, publicly documented protocol, so this adapter makes real HTTP calls to WHM's JSON API using API Token authentication (the current recommended method, not the root password). **It has not been exercised against a live WHM/cPanel server in this environment** (none was available) - the account-lifecycle calls use WHM API 1's standard, stable `metadata.result`/`metadata.reason` envelope; `get_usage()` (disk/bandwidth field names have varied across WHM versions) is parsed defensively and falls back to `None` rather than guessing. **Test against a real server before production use.**
- **`HostingAccount`** (new `apps.hosting` app): belongs to `Client` (matching the core business model's Service/Hosting Service concept), with a cPanel username generated from the domain (validated, deduplicated), a package-name snapshot (so a later product rename doesn't rewrite history), and status (pending, active, suspended, terminated, cancelled, failed). A partial unique constraint blocks a second live claim on the same username while allowing reuse after cancellation, mirroring `Domain`'s name constraint.
- **Services**: request-then-complete, same shape as domains, so this works without Cart/Checkout or Billing existing yet. Server assignment is automatic when a product maps to exactly one server, otherwise staff assign one explicitly before completing. **Applied the Phase 04 transaction lesson from the start:** every function that calls the adapter and must persist a failure (status/last_error/audit) before re-raising is *not* wrapped in `@transaction.atomic` - written correctly from the first draft, and still verified by tests (`test_provisioning_failure_sets_failed_and_allows_retry`, `test_suspend_failure_persists_error_and_audit`) rather than assumed correct.
- **Safe credentials:** the cPanel account password is generated per-provisioning (`secrets.token_urlsafe`), sent to the adapter, delivered to the client in a one-time welcome email, and **never written to our own database** - WHM is the system of record for it from that point on, matching the roadmap's "safe credentials" requirement literally rather than just encrypting a password we'd otherwise store.
- **API:** `/api/v1/hosting-accounts/` - list/retrieve scoped like domains (staff see all, others see their own via client contact); every mutation (`request_account`, `assign-server`, `complete`, `cancel`, `suspend`, `unsuspend`, `terminate`, `change-package`, `sync-status`, `sync-usage`) is its own action, authorized in the service layer for the same reason as domains (staff-only and shared actions coexist on one resource).
- **Web:** `/account/hosting/` (customer: list, request, read-only detail with usage) and `/staff/hosting/` (list/detail with the full action set). Customers can only view and request here - unlike domains, none of the hosting mutations (suspend, terminate, change package) are safe to expose as self-service yet, since they're either operationally sensitive or (package change) implies a billing change Phase 08 hasn't been built to handle.
- **Enum stability:** added `ENUM_NAME_OVERRIDES` for `ServerKind` and `HostingStatus`, as in every prior phase.

## TEST PLAN

69 new tests, bringing the suite to 296 (all passing on SQLite locally; PostgreSQL/Redis verified by CI as with Phases 01-04).

- **Services (27):** username generation and uniqueness; auto server-assignment for a single mapped server vs. requiring explicit assignment for multiple; permission checks (denied for a non-contact, allowed for any contact role); missing-package and unmapped-server rejections; provisioning idempotency and - specifically - the fail-then-retry path (proving the transaction fix holds); the one-time welcome email; suspend/unsuspend/terminate/change-package status rules, audit metadata, and a failure path proving `last_error` and the failure audit record survive the exception; sync status/usage; visibility scoping; search.
- **WHM adapter (18, mocked HTTP - no live server available):** authorization header and URL construction; the `metadata.result`/`reason` success/failure envelope; network and non-JSON failures both raise `HostingError`; `accountsummary` parsing for status and usage; `showbw` parsing for bandwidth; the `_parse_size_mb` helper across "150M"/"2.5G"/"500K"/"unlimited"/"0"/garbage inputs; usage falling back to all-`None` on any failure; HTTP vs HTTPS and SSL-verification toggles.
- **API (14):** authentication and visibility scoping (including the stranger-gets-404 vs Support-Agent-gets-403 distinction from Phase 04); request/complete/cancel authorization; suspend/unsuspend/terminate; change-package; server assignment; sync actions; and a test confirming the extended `Server` API accepts the new WHM fields while never echoing the token back in a response.
- **Web (10):** customer pages require login, request flow, list/detail, 404 for another client's account; staff pages require permission; Support Agent view-only; a full staff lifecycle walkthrough (provision → suspend → unsuspend → sync → change package → terminate); cancel; server assignment when a product maps to multiple servers; role-based navigation.
- **Browser check:** headless Chromium at 1366, 768 and 375 px wide, covering the customer request flow and the full staff lifecycle (provision, suspend, unsuspend, sync usage). No page-level horizontal overflow and no JS/console errors at any width - passed on the first run.

## PHASE 05 EXIT CRITERIA

| Criterion | State |
|---|---|
| Existing implementation inspected, `Server` extended not duplicated | ✅ |
| Provider-specific code confined to the adapter layer | ✅ |
| Provisioning idempotent, retries, failure states, manual retry, audit | ✅ tested explicitly, including the transaction-safety fix applied from the start |
| Safe credentials (roadmap's literal wording) | ✅ cPanel password never stored, only encrypted server API tokens are |
| New behaviour and authorization tested, full suite green | ✅ 296 passing (SQLite) |
| Migrations created, `makemigrations --check` clean, schema valid | ✅ |
| Browser check (desktop, tablet, mobile) | ✅ |
| CI green on PostgreSQL + Redis | ✅ Run 36052536493 on `9c0c517`: migrations, 296 tests, Celery/Redis round trip |
| Commit after verification | ✅ `9c0c517`, pushed to `main` |

## Known limitations / deferred

- **The WHM API adapter is unverified against a real server.** This is the single most important item in this report, same as Phase 04's registrar caveat: connect and test against a real WHM/cPanel box - particularly `get_usage()`'s field names - before relying on it in production.
- **Provisioning isn't wired to billing.** Staff complete requests manually today; once Phase 06/07/08 exist, completion should fire automatically on a paid order, exactly as the roadmap's provisioning-flow diagram describes.
- **No customer self-service for suspend/terminate/change-package.** These stay staff-only until there's a billing/cancellation workflow (Phase 08/12) to safely hang them off.
- **No automatic capacity-based server selection** across multiple servers mapped to one product - staff pick manually when a product maps to more than one.
- **Usage sync is manual** (a staff button), not scheduled; an automatic periodic sync is natural Celery-beat work for Phase 17/18 once the WHM adapter is verified live.
