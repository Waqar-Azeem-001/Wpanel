# Phase 08 Gap Report: Renewals, Upgrades & Proration

**Date:** 2026-09-25
**Baseline:** Phases 01-07 complete (`d3be5d0`)

## FOUND

- **Invoices, transactions, the paid signal** (Phase 07) - the invoice is the financial record; a renewal or upgrade is "an invoice that, once paid, changes a service".
- **Catalogue pricing** - `get_effective_price()` for plans, `TldPricing.renew_price` for domains.
- **Service operations** - `domains.renew_domain` (registrar renewal) and `hosting.change_package` (WHM), both written as staff-only actions with the note "not yet wired to billing - will be called on payment".
- **Domain expiry** already stored (`Domain.expires_at`); **hosting had no term at all** (no cycle, expiry or amount paid), so there was nothing to renew or prorate.
- **A beat container** already exists in `docker-compose.yml` with an empty schedule.
- Searched for any existing renewal, upgrade or proration code: none.

## PARTIAL / BROKEN

None. (Two Phase 07 notes closed: "Recurring invoices / renewals" and "No automated expiry/renewal job".)

## MISSING (now implemented)

The paid term of a hosting account, renewal invoices for hosting and domains, upgrade invoices with server-side proration, applying a change when its invoice is paid, a nightly job that creates renewal invoices, and the screens/API for all of it.

## REUSE

| Reused | How |
|---|---|
| Phase 07 invoicing | Renewal and upgrade invoices are ordinary invoices (same numbering, tax, payment, PDF, email). `create_invoice` / `issue_invoice` were split so an already-authorised caller (a customer renewing their own service) can create and issue a draft without the staff permission check - one implementation, two entry points |
| `unused credit as a discount` | The upgrade credit is the invoice's existing **discount** (`calc.compute`), so no negative lines were needed and the invoice arithmetic is unchanged |
| `get_effective_price`, `get_tld_pricing` | Every renewal and upgrade price |
| `domains.renew_domain`, `hosting.change_package` | Not duplicated: each was split into a permission-checked wrapper and a core that a "system" caller (no user) can run after payment. This is the first answer to the "fulfilment needs a system actor" note from Phase 06 |
| `can_self_service` (hosting, domains) | Who may renew/upgrade: any contact of the client, or staff who manage the service |
| `invoice_paid` / `invoice_cancelled` signals | The renewals app subscribes; `billing` never imports hosting or domains |

## CHANGES

- **A new `apps.renewals` app** owns the logic, so the dependency runs one way (renewals → billing, hosting, domains). It holds `ServiceChange`: one renewal or upgrade of one service, tied to its invoice, with the figures **frozen at invoicing time** (price, period, the proration inputs and results, the service's expiry at that moment). At payment time nothing is recomputed and nothing comes from a browser.
- **The paid term of a hosting account** (`billing_cycle`, `term_start`, `expires_at`, `term_paid`). `term_paid` is what was actually paid for the term, excluding tax and setup fees: the ceiling on any credit. Until Phase 09 fulfilment starts terms automatically, staff record it (`manage_billing`, audited); an account without a term shows "being set up" and cannot be renewed or upgraded online.
- **The rules, written down and encoded once (`renewals/proration.py`, pure functions):**
  - Days remaining = whole calendar days to the paid-through date. **An expired plan has no remaining days, so no credit.**
  - Credit = amount paid x days remaining / days in the term, half-up to the cent, **never more than the amount paid.** (2,000 random cases assert `0 <= credit <= paid`.)
  - Renewing before expiry *adds* the new term to the paid period (no time lost, and the paid value and term length both grow, so a later credit is the average rate over the whole paid period); renewing an expired service starts a fresh term from payment (no back-billing).
  - **Explicit same-plan renewal rule:** a renewal is charged at the plan's *current catalogue price* for the account's cycle, with no setup fee and no coupon, taxed under the client's current tax rules, and the price is fixed once invoiced.
  - **Upgrade:** the new plan's *full* term price is charged, the unused value is credited against it (never below zero; credit beyond the new price is forfeited and *shown as such*), and a fresh term starts when the upgrade is applied. The paid value of the new term is the new price. Only upgrades are offered online - a cheaper plan is refused ("Downgrades are not available online").
- **"Invoice shows calculation":** the credit is a labelled discount line ("Credit for unused time on Starter (265 of 365 days)"), the invoice notes carry the formula in words, and the invoice page (customer and staff) shows the full breakdown - paid for the term, days remaining, credit, net payable.
- **Applying a change** happens when the invoice becomes fully paid, under row locks, exactly once. A part payment applies nothing. If the service can no longer take the change (WHM unreachable, registrar down, or the service changed since the invoice - a renewal happened, the plan was changed) the **payment stands**, the change is marked *Paid - needs attention* with the reason (shown to staff only; the customer is told their payment was received and we are finishing it), and staff can **retry** or mark it handled (e.g. refunded). A stale upgrade is *never* applied blindly: it checks the plan and expiry are what they were at invoicing time.
- **One open change per service** (a database constraint plus a row lock): asking to renew twice returns the same invoice; a renewal and an upgrade cannot both be open. Cancelling the invoice voids the change.
- **Nightly job** (`generate_renewal_invoices`, Celery beat 03:00, also a management command and a staff button): invoices every active hosting account with a term, and every active domain with auto-renew on, that expires within `renewal_invoice_days` (a billing setting, default 14) and has no open change. Idempotent; one failure never stops the rest; errors are reported.
- **Web.** Customer: a Billing term card on the hosting page (Renew, Upgrade), an upgrade chooser showing each plan's calculation, a Renew form on the domain page; invoice pages show the change and its status. Staff: a Renewals page under Billing (due for renewal, all changes with a status filter, a "needs attention" banner, "create due invoices now"), the term form, "Create renewal invoice" on hosting and domain pages (the old direct domain renewal is kept but relabelled "without an invoice"), and Retry / Mark handled on the invoice.
- **API.** `POST /hosting-accounts/{id}/renew/`, `GET|POST /hosting-accounts/{id}/upgrade/`, `PUT /hosting-accounts/{id}/term/` (staff), `POST /domains/{id}/renewal-invoice/`, `/service-changes/` (list, detail, `retry`, `dismiss`). The only inputs are *which* service, *which* plan and *how many years*.
- **`verify_billing`** now also re-derives every upgrade's credit from its stored inputs and checks: credit <= paid value, applied credit = min(credit, new price), net = price - applied credit, the invoice shows that discount, and no paid invoice has an unapplied change.
- **Migrations:** `hosting 0002` (term fields), `billing 0004` (renewal lead time), `renewals 0001`.

## Defects found and fixed while building it

1. **`set_hosting_term` crashed on a non-numeric amount** with a raw `InvalidOperation` instead of a validation error - found by the first bad-input test.
2. **A repeat failure was invisible.** `retry_change` originally returned normally when the retry failed again; it now records the new outcome and *raises* it, so staff are told why (the record is written outside the rolled-back block).
3. **A wrong expectation of mine:** I had worked 100 x 265/365 as 72.53; the code's 72.60 is correct. Noted because the test for it is now a worked example.
4. **My first concurrency mutation check was invalid** (the `sed` replaced the first matching line, in a different function). Redone precisely: with the service-row lock removed, the double-click and the renewal-vs-upgrade races fail; with the change-row lock removed, the double-delivered paid signal fails; the restored code passes all three.
5. **A form field added to billing settings (`renewal_invoice_days`) broke the Phase 07 settings test**, which posted without it - the test was updated; nothing was left unvalidated.
6. **Cosmetic:** the calculation text was missing a full stop before "New plan price" (seen in the browser screenshot).

## TEST PLAN

**107 new tests: 729 in total on PostgreSQL** (721 on SQLite; the 8 concurrency tests skip there). All pass against a local PostgreSQL 17.5 before pushing.

- **Proration (19):** month arithmetic at month ends and leap years; a worked credit; half a term; half-up rounding; expired, expiring-today and long-expired terms all give no credit; a term that has not started is worth all of it and no more; nothing paid; broken terms; 2,000 random cases showing credit never exceeds what was paid.
- **Services (61):** the term (permissions, seven kinds of bad input, audit); renewal at the *current* price with tax and no setup fee, the price fixed once invoiced, idempotent, extending from the old expiry, fresh start when expired, a part payment applies nothing, applying twice extends once, cancelling voids and allows a new one, who may renew, no term / wrong status / retired price, suspended accounts, a free renewal settled at once; domains (invoice, payment, term validation, rules, registrar failure -> paid invoice + failed change -> retry); upgrade calculation (exact figures), invoice shows the calculation, paying changes the plan and starts a new term with the right paid value, expired plans get no credit, credit capped by what was paid, credit beyond the new price forfeited *and said so*, six kinds of refused upgrade, one open change at a time, figures frozen at invoicing (tampering with the term afterwards changes nothing), a service that changed after invoicing is not upgraded, a WHM failure leaves the payment and a retryable change; the job (only what is due, only once, lead-time setting, auto-renew, unsuitable services, survives errors); `verify_billing` catching a tampered credit and an unapplied paid change.
- **API and web (24):** authentication on every endpoint; strangers get 404; **tampered credit, price, expiry, total and remaining days in the request are ignored** (checked on the invoice and the stored calculation); the term is staff-only; customers never see the internal failure reason; every page and button, including the failure banner and retry.
- **Concurrency (3, PostgreSQL):** a double-clicked renewal makes one invoice; a renewal and an upgrade at once leave one open change; the paid signal delivered twice at once extends once - each verified to fail when its lock is removed.
- **Browser check:** headless Chromium at 1366, 768 and 375px: hosting page -> renew -> pay through the test gateway -> applied; upgrade chooser with the credit -> invoice with the calculation -> pay -> now on the new plan; domain renew -> pay -> applied; staff renewals page, generate, hosting term card and term form. No JavaScript errors and no overflow. `verify_billing` on that database: consistent.

## PHASE 08 EXIT CRITERIA

| Criterion | State |
|---|---|
| Existing implementation inspected, no duplicates | ✅ |
| Remaining value calculated server-side; credit cannot exceed valid paid value; tampered credit and expiry ignored; expired plans get no credit; same-plan renewal follows explicit rules; invoice shows calculation | ✅ each tested |
| Authorization tested; full suite green on SQLite and real PostgreSQL | ✅ 729 |
| Migrations, `makemigrations --check`, OpenAPI schema with no warnings | ✅ |
| Browser check (desktop, tablet, mobile) | ✅ |
| CI green on PostgreSQL + Redis | ✅ Run 36103918412 on `4d00e37` (first run) |

## Known limitations / deferred

- **Terms are recorded by staff for now.** Nothing yet starts a hosting term automatically when an order is paid; Phase 09 fulfilment must call the same term logic (and still needs the full system-actor design; this phase only added system entry points for renewal and package change).
- **Refunds do not reverse a renewal or upgrade** (the service keeps the time / plan; staff adjust it by hand). Credit balances (carrying forfeited credit forward) are not implemented.
- **Only upgrades online**; downgrades and billing-cycle changes go through staff. Add-on and domain-term proration are out of scope.
- **Domain renewal is for ACTIVE domains only**; an EXPIRED (redemption) domain needs a staff action. Suspended hosting can be renewed but is not automatically unsuspended on payment (lifecycle, Phase 09).
- **Reminder emails, overdue handling, grace periods and automatic suspension** are Phase 09/11; the nightly job only creates the invoices.
- **The renewal price is the current catalogue price**, so a price rise reaches existing clients at their next renewal (by design, stated on the invoice); no price locking or grandfathering yet.
- **Applying a change makes its service call (WHM / registrar) while holding the invoice lock**; fine for the built-in adapters, worth restructuring for a slow real one.
