# E14: Nine Owner Requests (Quotes, Clients, Staff Permissions, Orders, Domains, Renewals)

**Date:** 2026-10-03
**Baseline:** Phases 01-16 (both slices), D0-D8, E1-E13, Phase 17 slice 1 complete, all local (not deployed).
**Source:** Two owner messages (one pasted from 2026-09-29, one typed same-session), nine distinct requests,
clarified with the owner before building where the request was ambiguous or touched already-documented behaviour
(see "Confirmed with the owner" below).

## Confirmed with the owner before building

- **Item 6 (upgrade proration)** explicitly reverses Phase 08's documented, tested design (fresh term + credit-
  as-discount on upgrade). The owner confirmed the new behaviour: the renewal date never moves on an upgrade;
  only the new plan's prorated cost for the remaining days is charged, less the old plan's unused value.
- **Item 1 (quotation PDF)** - the owner clarified "something else" as: an editable footer/terms block (mirroring
  the invoice's own, which already existed) and a short "social proof" line shown under the pricing.

## Inspection report (Rule 1) and what was built

### 1. Quotation PDF changes - FOUND (invoice_footer existed; quotes had no footer or highlight at all)

`apps/billing/models.py`: `BillingSettings.invoice_footer` already existed and was already wired into
`render_invoice_pdf`, but `render_quote_pdf` never passed any `footer_extra` - a quote's PDF had no customisable
text at all. Added `quote_footer` (bottom of the quote, mirroring the invoice) and `quote_highlight` (a short line
shown directly below the pricing table - `apps/billing/pdf.py`'s `_build()` gained a `highlight` parameter,
inserted right after the totals grid). Both editable from the existing **Setup → Billing → Settings** page
(`apps/billing/forms.py`, `apps/billing/invoicing.py:save_billing_settings`) - no new staff screen needed.
Verified by rendering a real PDF (reportlab, uncompressed text stream) and confirming both strings appear in the
right place, and that an invoice never shows the quote-only fields.

### 2. Client alternate/secondary email - MISSING (built)

`Client` had exactly one `email` field. Added `alternate_email` (blank-allowed `EmailField`) to
`apps/clients/models.py`, and to both `STAFF_FIELDS` and `CUSTOMER_FIELDS` in `services.py` - the staff and
customer account forms are generated from these tuples (`forms.fields_for_model`), so the field appeared on both
forms with zero template changes. `apps/notifications/services.py:dispatch_client` now sends a second, independent
copy of every client email to `alternate_email` when set (reusing the existing one-row-per-recipient
`EmailMessage`/tracking pipeline - no new delivery mechanism), skipping it if it would duplicate an address already
being emailed.

### 3. Client privileges / self-service sub-user invitations - PARTIAL (the missing half built)

`ClientContact` (owner/billing/technical roles) already existed, staff-managed only - the roadmap's own "Customer
self-service sub-user invitations" item was open. Added `apps/clients/services.py:invite_contact` - the self-
service counterpart to staff's `add_contact`, gated on *being an owner of this account* (not a staff permission;
customers hold none - `apps.accounts.roles`) rather than `manage_clients`. Deliberately owner-only, not billing/
technical: deciding who else gets access to an account is a bigger decision than what those two roles already
allow. New page section on the existing **Contacts** page (`clients_customer:contacts`) - an owner sees an "Invite
someone" form; everyone else still sees the same read-only list. Role changes and removal remain staff-only (not
part of this request).

### 4. Per-staff custom permissions beyond role - PARTIAL (Django's own mechanism existed unused; wired up)

`User(AbstractBaseUser, PermissionsMixin)` already carries Django's stock `user_permissions` M2M, and the default
`ModelBackend` (no custom auth backend) already honours it alongside group (role) permissions in `has_perm()` - but
nothing in the product set or displayed it. Added `apps.accounts.services.set_extra_permissions(actor, user,
codenames)`: looks up `Permission` rows for the `PortalAccess` content type (same lookup `sync_roles()` already
uses) and sets them directly on the user, gated the same way role assignment already is (`assign_roles`, never on
yourself, an admin-role account only by a Super Admin). New "Extra permissions" panel on the existing staff user
detail page (`console:user_detail`) - every portal permission as a checkbox, independent of the role dropdown
above it.

### 5. Invoice generation/sharing option at order placement - FOUND, always on (now optional for staff-placed orders)

Invoice creation itself is not optional - an order cannot be paid without one - but the *notification* was
unconditional on every order, customer or staff-placed. `apps/orders/services.py:checkout` gained
`notify_client=True`; a new `StaffCheckoutForm` (Add Order only - a customer checking out their own cart always
hears about it) adds an "Email the client now" checkbox, defaulting on, that staff can untick for a quiet/offline
order. The invoice is still created and issued either way. For sharing it later, added a manual "Email invoice"
button on the invoice's own staff page (`billing_staff:invoice_send`, calls the existing
`invoicing.notify_invoice_issued`) - the same function the automatic path already used, just invoked on demand.

### 6. Domain/hosting: upgrade keeps the renewal date, charges only the difference - FOUND, behaviour reversed (confirmed with owner)

Documented in `docs/phase-08-gap-report.md` and `apps/renewals/proration.py`'s own module docstring: an upgrade
charged the new plan's *full* term price, credited the old plan's unused value against it, and started a **fresh
term** from the moment it was applied. The owner's request is the opposite: the renewal date must never move;
only the new plan's prorated cost for the days actually remaining is charged, less the same unused-value credit.
Implemented as `apps.renewals.proration.prorated_cost` (the mirror of the existing `unused_credit`, for the plan
being moved *to* rather than from) and a change to `preview_upgrade`/`_apply_hosting`: while time remains on the
term, `term_start`/`expires_at` are left untouched and only the prorated value joins `term_paid` (a running total,
exactly like an on-time renewal already adds to it); an *already-expired* account still falls back to the old
"fresh term, full price" shape, since there is no existing term left to preserve and the alternative would be a
free upgrade. See "Caught while implementing" below for why that fallback exists.

### 7. Domain registration: manual or automatic - FOUND (renewal); MISSING (registration) - built

`Domain.auto_renew` already existed with full read/write support (customer toggle, API, admin) - renewal was
already covered. Registration itself was always synchronous and automatic the moment a paid order was fulfilled,
with no gate. Added `apps.domains.models.DomainSettings` (single row, mirrors `apps.lifecycle.models
.LifecycleSettings`) with one field, `auto_register`; `apps/orders/fulfilment.py`'s `_fulfil_domain_register` now
checks it before calling `complete_registration` - off, the order line still completes (paid, recorded) and the
domain waits in Pending Registration for the *same* "Complete" button a failed/retried registration already uses,
so no new completion mechanism was needed. New **Setup → Domain registration** staff page.

### 8. Active client: convert to Fraud/Closed before delete, not direct delete - PARTIAL (built)

`ClientStatus` had only Active/Inactive/Closed; the delete path (`apps.clients.services.delete_client`, Super
Admin only) was gated purely by *history* (any order, invoice, ticket...) - an account with zero history could be
deleted directly while still Active, with no status check at all. Added `ClientStatus.FRAUD` and a new, independent
gate in `delete_client`: only a client whose status is already `Fraud` may be deleted, regardless of history (the
history check still applies on top of it - marking something Fraud does not waive it). The delete page explains
which of the two reasons applies. Deliberately minimal: Fraud is a status label and a delete gate only - it does
not auto-suspend services or otherwise change behaviour, since that was not asked for and is a materially bigger
decision (the existing Closed status and the cancellation workflow already cover "stop new business / wind down").

### 9. Abuse department - FOUND, no code needed

`apps.support.models.Department` is already a plain, staff-configurable database table (not a fixed enum) with a
staff CRUD page at `staff/support/departments/`. Added a one-line data migration seeding "Abuse" as a default
department (`apps/support/migrations/0003_seed_abuse_department.py`), consistent with how Technical/Billing/
Sales/General were already seeded (`0002_seed_defaults.py`) - staff can add, rename or deactivate it like any other
department without a release.

## Caught while implementing (and fixed before it shipped)

- **Domain settings form, empty-POST bug**: the settings page's only field is a checkbox; unticking it and
  submitting posts zero keys, which is a real-but-empty `QueryDict`. The existing `request.POST or None` idiom used
  elsewhere in the project treats that as a GET (unbound form), so the setting could never actually be turned off.
  Fixed by binding on `request.method == "POST"` instead of truthiness - caught by this feature's own web test
  before it reached the owner.
- **Upgrading an already-expired account would have been free** under a first draft of the proration change: zero
  days remaining prorates *any* plan to zero, old and new alike, so a straight port of "prorate for remaining
  days" produced a `net_payable` of 0 for an expired service. Fixed by falling back to the pre-existing "fresh
  term, full price" behaviour specifically when the account has already expired (mirroring the renewal branch's own
  existing expired/not-expired split) - caught by deliberately testing that exact edge case, not by an owner report.

## REUSE

| Reused | How |
|---|---|
| `forms.fields_for_model` + `STAFF_FIELDS`/`CUSTOMER_FIELDS` tuples (`apps.clients`) | Adding `alternate_email` to both tuples was the entire form change - both the staff and customer edit pages picked it up with no template edits. |
| `apps.lifecycle.models.LifecycleSettings` (single-row settings, `load()`/`save_settings` shape) | Mirrored exactly for `apps.domains.models.DomainSettings`. |
| `apps.billing.models.PaymentProvider`'s existing `invoice_footer`/`BillingSettings` pattern | `quote_footer`/`quote_highlight` added the same way, on the same settings row and page. |
| `apps.domains.services.complete_registration` / the domain page's existing "Complete" button | The manual half of the auto-register toggle needed no new UI - the retry/complete action already existed for failed registrations. |
| `apps.billing.invoicing.notify_invoice_issued` (already existed, only ever called automatically) | Reused verbatim for the new manual "Email invoice" button. |
| `apps.renewals.proration.unused_credit`'s shape | `prorated_cost` is its direct mirror (same term/remaining-days inputs, same rounding), not a new calculation style. |
| Django's own `user_permissions` + default `ModelBackend` | No new permission-checking mechanism - `set_extra_permissions` only starts using plumbing that already shipped with `PermissionsMixin`. |
| `apps.support.models.Department` (already configurable) | "Abuse" needed a data migration, not a feature. |

## VERIFIED

20 new/updated test modules, 2157 total tests project-wide (0 failures, up from 2137 before this slice). Crawler
and navigation tests green (four new staff/customer routes, all correctly detected as either GET-reachable pages
now seeded into the shared fixture world's reach, or POST-only and exempt). Full regression run across
`apps.clients`, `apps.domains`, `apps.accounts`, `apps.console`, `apps.orders`, `apps.billing`, `apps.renewals`,
`apps.hosting`, `apps.support`, `apps.core` - all green. Browser-checked (Chromium, 1440 wide, a copy of the real
local database): the domain-registration settings page, the staff "Extra permissions" panel, the client delete
page in both its blocked (not Fraud) and allowed (Fraud) states, and the customer "Invite someone" form - no
console errors, no overflow. The quotation PDF was rendered for real (not a snapshot/mock) with both new fields
set, confirming the highlight line sits directly under the pricing and the footer at the very bottom, exactly as
asked.

## Known limitations / deferred (not asked for; noted so they are not silently assumed)

- No self-service role change or removal for client contacts - only inviting is self-service; staff still handle
  the rest, per the existing roadmap item.
- "Fraud" has no workflow beyond the delete gate (no auto-suspend, no dedicated review queue) - a label plus a
  safety gate, not a fraud-investigation feature.
- Domain auto-register is a single global switch, not per-TLD or per-product - the owner's request did not ask for
  per-plan granularity, and `auto_renew` (the renewal half) is already per-domain, which was judged sufficient.
- The invoice "email now" checkbox is Add Order only - a customer's own checkout is never silent, by design.
- Multi-currency, addon-to-product compatibility and the other pre-existing limitations noted in earlier phase
  reports are unchanged and unaffected by this slice.
