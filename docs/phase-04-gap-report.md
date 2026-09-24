# Phase 04 Gap Report: Domain Management

**Date:** 2026-09-25
**Baseline:** Phases 01-03 complete (`85ba963`)

## FOUND

Phases 01-03 provide users, roles (`view_domains`/`manage_domains` already existed and were already granted correctly - Support Agent view-only, Manager view+manage), the client/contact model domains attach to, the audit log, the API foundation, and the `PublicReadPermission`/staff-vs-public visibility pattern from Phase 03 this phase reuses directly.

## PARTIAL / BROKEN

None - nothing domain-related existed yet.

## MISSING (now implemented)

Everything: the registrar abstraction, domain and DNS models, TLD pricing, and public/customer/staff screens.

## REUSE

| Reused | How |
|---|---|
| `accounts.view_domains`/`manage_domains` | Already existed, already granted correctly; no permission changes needed |
| `apps.clients.services.contact_role` | Self-service authorization (`can_self_service`) - any contact of the domain's client, exactly the ownership check Phase 02 established |
| `apps.core.permissions.PublicReadPermission` | TLD pricing endpoint (public read, `manage_domains` write) - same pattern as Phase 03's product/addon catalog |
| `notifications.EmailProvider` pattern | `RegistrarProvider`: single active row (partial unique constraint), encrypted credentials via `apps.core.crypto`, Django-admin-only management with no custom portal permission (same precedent, same reason: a rare "connect a provider" action, not routine staff work) |
| `apps.audit.services.record` | Every registration, transfer, renewal, cancellation, lock/unlock, nameserver and DNS change |
| Phase 02/03's plain-Form-not-ModelForm and `_apply_error`/redirect-on-action web view convention | `apps/domains/forms.py`, `apps/domains/views.py` |

## CHANGES

- **Registrar abstraction** (`apps/domains/adapters/`): `RegistrarAdapter` (ABC) defines exactly the roadmap's Provider Operations list - `check_availability`, `register_domain`, `renew_domain`, `transfer_domain`, `get_domain`, `update_nameservers`, `get_dns`, `update_dns`, `lock_domain`, `unlock_domain` - working with plain values, never ORM instances, so registrar-specific code never leaks into views/services. `ManualAdapter` is the only adapter shipped: **it makes no network calls and does not perform real domain registration.** It simulates the full lifecycle in our own database so every other piece (models, services, screens, tests) could be built and verified without a live registrar contract. This is a **deliberate, explicit architecture decision**, not a shortcut discovered later - see Known limitations.
- **Models:**
  - `RegistrarProvider`: the live, DB-configured registrar connection (mirrors `EmailProvider` exactly - single active row, encrypted credentials, admin-only).
  - `TldPricing`: register/renew/transfer/redemption price per TLD, with a min/max registration term. This is the domain-specific pricing model Phase 03 explicitly deferred (domains don't fit the billing-cycle shape used for hosting products).
  - `Domain`: belongs to `Client` (not directly to a `User`, matching the core business model); status (pending registration, pending transfer, active, expired, cancelled, failed); nameservers; transfer lock; encrypted transfer-in auth code; a partial unique constraint that blocks a second live claim on the same name while still allowing it to be re-requested after a cancellation or failure.
  - `DnsRecord`: A/AAAA/CNAME/MX/TXT/NS/SRV records per domain, with `priority` required for MX/SRV and forbidden otherwise.
- **Services** (`apps/domains/services.py`): registration and transfer are request-then-complete (a contact requests, staff completes against the registrar) so the flow works today without Cart/Checkout (Phase 06) or Billing (Phase 07/08) existing yet; both are idempotent - completing an already-active domain is a no-op, and a failed attempt can be retried. Self-service actions (nameservers, DNS, auto-renew, lock/unlock) are available to **any** contact of the client (owner, billing or technical), not just owner/billing, since none of them are financial. `get_active_registrar`/`get_adapter` raise a clear `ServiceError` when no registrar is configured, the same shape as the email service's "no provider configured" handling.
- **A genuine bug caught by testing:** `complete_registration`, `complete_transfer` and `renew_domain` were each wrapped in `@transaction.atomic`, including their failure branch, which does `domain.save(status=FAILED, ...)` then `audit.record(...)` then re-raises. Since Django rolls back an entire atomic block when an exception escapes it, that write and audit record were being silently discarded - a failed registration would show as still PENDING with no error message and no audit trail, invisible to staff. Fixed by removing `@transaction.atomic` from these three functions (each branch is already a single, inherently atomic `.save()`; nothing here needs multi-statement rollback protection). Caught by `test_registration_failure_sets_failed_and_allows_retry`.
- **API:** `/api/v1/domains/availability/` (public) and `/api/v1/tld-pricing/` (public read, staff write) for discovery; `/api/v1/domains/` for everything else - list/retrieve show all domains to `view_domains` staff and only the caller's own (via client contact) to everyone else, and every mutating action (`register`, `transfer`, `complete`, `cancel`, `renew`, `auto-renew`, `nameservers`, `lock`, `unlock`, `sync`, `dns`) is its own `@action` because each has different rules that don't fit a single `HasPortalPermission` per-HTTP-method check. Authorization for these actions lives in the service layer (`_require`/`_require_self_service`), which is why the view only requires the caller be authenticated.
- **Web:** a public `/domains/` search page (no login) with pricing on a hit; `/account/domains/` for customer self-service (list, detail, register, transfer-in, nameservers, DNS, auto-renew, lock/unlock); `/staff/domains/` for the operational side (list/detail, complete, cancel, renew, sync) plus `/staff/domains/tlds/` for pricing. Also fixed a small pre-existing Phase 03 gap while touching the shared nav: the "Plans" (and now "Domains") links were only shown to signed-in users even though both catalogs are public - moved them outside the authenticated block.
- **Enum stability:** added `ENUM_NAME_OVERRIDES` for the new choice fields, as in every prior phase.

## TEST PLAN

77 new tests, bringing the suite to 227 (all passing on SQLite locally; PostgreSQL/Redis verified by CI as with Phases 01-03).

- **Services (42):** domain-name/nameserver/DNS-priority/TLD validation; the unique-live-name constraint allowing reuse after cancellation; `RegistrarProvider`'s single-active constraint and credential encryption; the Manual adapter's availability check and renew-extends-from-current-expiry behaviour; availability/pricing error paths (unsupported TLD, no active registrar, inactive TLD); registration and transfer request/complete/cancel with permission checks, idempotency, and - specifically - the failure-then-retry path that caught the transaction bug above; renewal status/term validation and audit metadata; self-service permission checks (denied for a non-contact, allowed for *any* contact role including technical); lock/unlock toggling and audit; sync requiring a provider reference; DNS CRUD and permission; visibility scoping; search; TLD pricing CRUD, upsert semantics and visibility.
- **API (23):** public availability and TLD pricing (hides internal fields and inactive rows from the public); domain list/retrieve authentication and scoping, including a stranger getting 404 (matches the Phase 02 client-visibility precedent) versus a Support Agent (visible via `view_domains`, denied by the service layer) getting 403 for the same self-service action - a real distinction the tests specifically pin down; register/transfer/complete/cancel/renew authorization; auto-renew/nameservers/lock/unlock; DNS CRUD including the MX-without-priority rejection; sync requiring `manage_domains`.
- **Web (14):** public search rendering, availability and taken results, unsupported-TLD form error; customer pages require login, registration request, list/detail, 404 for another client's domain, and the full self-service action set; staff pages require permission, Support Agent can view but not complete, a full staff lifecycle walkthrough (TLD pricing → register → complete → renew → nameservers → DNS → sync), cancel, and the TLD list/status toggle.
- **Browser check:** headless Chromium at 1366, 768 and 375 px wide, covering the public search, customer registration request, staff completion, DNS record add, and the TLD pricing page. No page-level horizontal overflow and no JS/console errors at any width. (Two selector-ambiguity bugs were in my own test script, not the app - a `.row-form` class shared by two forms, and a "TLD Pricing" link that only exists on the list page, not the detail page I was still on - both fixed before the check passed.)

## PHASE 04 EXIT CRITERIA

| Criterion | State |
|---|---|
| Existing implementation inspected, no duplicates | ✅ |
| Registrar-specific code confined to the adapter layer | ✅ |
| Registration/renewal/transfer idempotent | ✅ tested explicitly, including the transaction-rollback fix |
| New behaviour and authorization tested, full suite green | ✅ 227 passing (SQLite) |
| Migrations created, `makemigrations --check` clean, schema valid | ✅ |
| Browser check (desktop, tablet, mobile) | ✅ |
| CI green on PostgreSQL + Redis | ✅ Run 36049054590 on `e614249`: migrations, 227 tests, Celery/Redis round trip |
| Commit after verification | ✅ `e614249`, pushed to `main` |

## Known limitations / deferred

- **No real registrar is connected.** The Manual adapter does not register real domains anywhere - it is local-only simulation for building and testing the system. **Before this goes live, a real registrar adapter (e.g. against a specific registrar's API/EPP interface) must be written and configured**, following the exact same `RegistrarAdapter` interface; no model, service or view changes should be needed. This is the single most important item in this report.
- **Single-label TLDs only** (`.com`, `.io`, `.dev`, ...). Multi-part public suffixes (`.co.uk`, `.com.au`) need a public-suffix list and aren't supported yet.
- **Registration and transfer aren't wired to billing.** Staff complete them manually today, representing "payment was received through some other means." Once Phase 07/08 exist, completion should fire automatically on payment, the same way Phase 05's WHM provisioning is described as triggered by a paid order.
- **No automated expiry/renewal-reminder job.** Phase 08 (proration) and Phase 11 (notifications, "expiry checks") own that Celery work; this phase only provides the `expired`/`cancelled`/`failed` states and the `renew_domain` operation for them to call.
- **DNS records here are informational once nameservers point elsewhere** - the UI notes this but doesn't hide the editor, in case the customer switches back to our nameservers.
- **No outbound-transfer auth-code retrieval.** `unlock_domain` (the first real step of transferring away) is implemented; generating/emailing an EPP code is registrar-specific and wasn't in the roadmap's explicit operations list, so it's deferred until a real adapter needs it.
