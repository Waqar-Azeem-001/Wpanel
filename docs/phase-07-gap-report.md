# Phase 07 Gap Report: Billing & Invoices

**Date:** 2026-09-25
**Baseline:** Phases 01-06 complete (`bbf3617`)

## FOUND

- **`apps.billing` (configuration only)** - `PaymentMethod`, `TaxRule`, `Coupon`, written in Phase 06 with the explicit note that Phase 07 *extends this app* rather than creating a second one. Extended, as promised.
- **Orders** - `Order`/`OrderItem` already snapshot prices, discount, tax and the client's billing details "so invoices can be built from these". The invoice for an order is built from that snapshot, never recomputed.
- **Provider pattern** - `EmailProvider` and `RegistrarProvider` (DB-configured, encrypted credentials, admin-only, adapter classes). `PaymentProvider` follows it exactly.
- **Permissions** - `view_billing` / `manage_billing` already existed and were correctly granted (Support Agent: view; Manager, Admin: manage). No permission changes.
- **Audit, notifications, error format, shared web helpers, encryption** - reused as-is.
- Searched for any existing invoice, transaction, quote, PDF or gateway code: none.

## PARTIAL / BROKEN

None. (Housekeeping: the client profile's "Account records" placeholder and the staff order page's "invoices arrive later" note are now real.)

## MISSING (now implemented)

Invoices (with lines, discount, tax, due dates, gap-free numbering), transactions (payments and refunds), payment status, PDF invoices, quotes, billable items, a payment-gateway abstraction with an idempotent, signature-verified webhook, and every screen and API for them.

## REUSE

| Reused | How |
|---|---|
| Order snapshot | The invoice for an order distributes the order's stored discount and tax across its lines; `invoice.total == order.total` is enforced |
| `RegistrarProvider` / `EmailProvider` pattern | `PaymentProvider`: encrypted credentials **and webhook secret**, superuser-only in Django admin, adapter registry |
| `apps.core.crypto`, `audit.record`, `notifications.send_email` | Provider secrets, every state change, invoice/receipt/quote emails |
| `apps/core/web.py`, `AuthenticatedReadPermission` | Web action plumbing; payment-method API |
| `contact_role` | Who may act for a client |
| **One arithmetic, promoted not copied:** `apps/billing/calculations.py` | Roadmap rule "no duplicate billing calculations". The Phase 06 cart pricing engine and `Coupon.amount_for` were refactored to call it, so the cart, orders, invoices and quotes cannot disagree by a cent |
| **One snapshot function:** `billing_snapshot()` | Was private to `orders`; now shared and used by orders, invoices and quotes |

## CHANGES

- **Money rules (`calculations.py`).** `Decimal`, half-up, two places. Discount and tax are *allocated* to lines in whole cents (remainder to the largest line, deterministic, never negative) so the lines always sum exactly to the document totals. That is what makes an invoice re-derivable from its own records.
- **Invoices.** Draft → issue → (paid | partially paid | cancelled | refunded). Issuing takes the next **gap-free** number under a row lock (a rolled-back issue leaves no hole), sets issue/due dates from the billing settings, snapshots the billing details and freezes the lines. Issued invoices can only be paid, refunded or cancelled. **"Overdue" is derived** from the due date, never stored, so it cannot be stale. A zero-total invoice is settled when issued.
- **Payments as the single source of truth.** An invoice's paid and refunded amounts and its status are *derived* from its succeeded `Transaction` rows by `recompute_invoice`, always under the invoice row lock. Refunds never reopen an invoice. Money that arrives for an already-settled or cancelled invoice is recorded truthfully and flagged (`overpaid`) rather than dropped. Lock order is always invoice → transaction → order, so payment, refund and order-cancel cannot deadlock.
- **Offline payments.** Staff record a payment (with an idempotency key - a re-submitted form or retried API call cannot record it twice). Customers can *report* a payment (a pending transaction that changes nothing) and staff confirm or reject it.
- **Online payments.** `PaymentAdapter` (create payment, verify + parse webhook, refund) behind a registry. The customer is sent to the gateway; **the transaction only succeeds when the gateway's signed webhook says so** - never because the browser came back. The webhook verifies the signature over the raw body (constant-time compare, timestamp tolerance), records the event id (a redelivery is a no-op), checks amount and currency against the transaction (a mismatch is *not* applied), and rolls everything back on an unexpected error so the gateway's retry succeeds. `POST /api/v1/webhooks/payments/<provider_id>/` has no login and is authenticated by signature alone.
- **The test gateway.** The only adapter shipped: a simulated hosted page that signs its own webhooks (Stripe-style `t=…,v1=HMAC-SHA256`), so the *real* verification, idempotency and amount-checking code runs end to end without moving money. It is guarded by `ALLOW_TEST_PAYMENT_GATEWAY` (off by default, on in dev/test only), enforced when a provider is activated, when the adapter is built, and by the hosted page. **No real gateway is connected yet** - see Known Work.
- **Orders ↔ invoices.** Checkout issues the invoice inside the same transaction (a failure leaves no order). Paying it marks the order paid and fires an `order_paid` signal (the hook Phase 09 fulfilment will use); cancelling the order cancels the invoice and vice versa; an order with a part payment cannot be cancelled.
- **Quotes.** Draft → sent (numbered, emailed) → accepted / declined / cancelled; expiry is derived. Accepting issues the invoice with the quote's exact figures.
- **Billable items.** One-off charges recorded against a client and collected onto a draft invoice; the link to the invoice is what marks them billed.
- **PDF.** reportlab; reads only stored records; deterministic (same invoice, same bytes).
- **Tax exemption.** `Client.tax_exempt`, staff-only (a customer cannot grant it to themselves); honoured by the cart, orders and invoices through one function.
- **`manage.py verify_billing`.** Re-derives every invoice/quote figure, the paid/refunded amounts and statuses from the transactions, and the number sequences; exits non-zero on any disagreement. The tests use the same code.
- **Web.** Staff: invoices (list/search/filter incl. overdue, new via client search, edit drafts with a no-JavaScript line editor, issue, cancel, delete draft, record payment, refund, PDF), payments (confirm/reject reported payments), quotes, billable items, billing settings, payment methods can now be linked to a gateway; the **client profile "Account records"** section lists that client's orders, invoices, payments, quotes, domains and hosting (each section only if the viewer holds that module's view permission). Customer: invoices and quotes (list, detail, PDF, pay, report payment, accept/decline), the test-gateway page, an Invoices link in the navigation, and invoice links on order pages.
- **API.** `/invoices/` (CRUD for drafts, `issue`, `cancel`, `pdf`, `pay`, `record-payment`, `transactions`), `/transactions/` (`confirm`, `reject`, `refund`), `/quotes/`, `/billable-items/` (+ `invoice`), `/billing-settings/`, the webhook. Customers see only their own clients' *issued* invoices; someone else's invoice is a 404.
- **Emails:** invoice issued, payment received, quote sent; the order confirmation now names the invoice.
- **Migrations:** `billing 0002` (models), `0003` (seeds the number sequences and settings row), `clients 0002` (`tax_exempt`).

## Defects found and fixed while building it

1. **A `KeyError` on every manual invoice** (`line["order_item"]` for lines not coming from an order) - the first service test caught it.
2. **`overpaid` was 0 for a cancelled invoice that received money** (it compared against the total instead of zero) - caught by the late-payment test.
3. **The payment-method / tax-rule / coupon API `PUT` responded with the pre-update values** (the response was built from a stale instance). Latent since Phase 06; it only showed once a test asserted the returned `provider`. Fixed for all three.
4. **My first concurrency tests proved nothing.** They passed even with the row lock removed, because the transactions are too short to overlap. A mutation check exposed it; the tests now hold each lock for a moment so the threads genuinely interleave, and they *fail* if the invoice lock or the number-sequence lock is removed (confirmed against PostgreSQL). The two webhook race tests still pass without the invoice lock because the transaction-row lock and the event-id constraint independently protect them - defence in depth, noted rather than hidden.
5. **A hidden form field rendered a visible "Idempotency key" label**, and **the customer payment-method dropdown truncated its text** - both found by looking at the browser screenshots.
6. **Lock-order deadlock avoided by design:** cancelling an order originally locked order → invoice while the payment path locks invoice → order; reordered before it could bite.

## TEST PLAN

**164 new tests: 622 in total on PostgreSQL** (617 on SQLite; the 5 concurrency tests need real row locking and skip there). All pass against a local PostgreSQL 17.5 *before pushing*.

- **Arithmetic (16):** half-up rounding, tax, discount caps, allocation that always sums exactly (500 randomised cases), documents matching their lines.
- **Invoicing (35):** totals, percentage and fixed discounts, non-taxable lines, tax exemption; every invalid line/discount rejected *before anything is written*; permissions; drafts editable, issued frozen; numbering gap-free (and a failed issue burns no number); a zero-total invoice; tax re-applied at issue; cancel rules; visibility; billable items; the full quote lifecycle.
- **Payments (34):** partial → full; over-payment and non-positive/NaN/3-decimal amounts refused; idempotency key (replay returns the original, a different amount is a 409); receipts; the `invoice_paid` signal; report → confirm/reject; refunds partial and full; the gateway: pending transaction and its reuse, signed success, **redelivery is a no-op**, tampered body / wrong secret / missing / garbage / stale signature all rejected with nothing recorded, no secret configured verifies nothing, **amount and currency mismatch not applied**, unknown payments and event types ignored, a failed attempt then a retry, **money arriving after cancellation recorded and flagged and refundable**, gateway refunds, the test gateway unusable when disabled, secrets encrypted and absent from the audit trail.
- **Orders ↔ invoices (11):** invoice equals order to the cent (with coupon, tax and setup fees); paying marks the order paid and fires the hook; part payment; cancellation both ways; a free order settled at checkout; tax-exempt clients; snapshots survive client edits; a checkout that cannot invoice leaves no order.
- **API (21), web (30), PDF/admin/command/tax (12):** authentication, customer scoping (404s), idempotency header, filters including derived "overdue", webhook needs a valid signature and no login, the line editor (add, remove by clearing, half-filled rows), double-submit protection, PDF content and reproducibility, hostile text in the PDF, the admin form encrypts secrets and never shows them, `verify_billing` detects tampering and number gaps.
- **Concurrency (5, PostgreSQL):** the same webhook twice at once pays once; two different events for one payment apply once; the same idempotency key at once records one payment; concurrent payments can never overpay; concurrent issuing yields gap-free numbers.
- **Browser check:** headless Chromium at 1366, 768 and 375px: order → invoice → report an offline payment → second order paid through the test-gateway page → staff confirm, create a manual invoice with discount, issue, part-pay, refund, quote create/send, billable charge, client profile, settings → customer accepts the quote. No JavaScript errors and no overflow; it found defect 5.

## PHASE 07 EXIT CRITERIA

| Criterion | State |
|---|---|
| Existing implementation inspected, no duplicates | ✅ (arithmetic and snapshot promoted to one shared place) |
| Financial figures reproducible from permanent records | ✅ `verify_billing` + tests |
| Payment webhooks idempotent and signature-verified; credentials in the database, never ENV | ✅ (test gateway only - real gateway is Known Work) |
| Authorization tested; full suite green on SQLite and real PostgreSQL | ✅ 622 |
| Migrations, `makemigrations --check`, OpenAPI schema with no warnings | ✅ |
| Browser check (desktop, tablet, mobile) | ✅ |
| CI green on PostgreSQL + Redis | ✅ Run 36100938261 on `ac37961` (first run, no fixes needed - the local PostgreSQL run caught nothing new because it was done before pushing) |

## Known limitations / deferred

- **No real payment gateway is connected.** Only the simulated test gateway ships; a real one is one adapter class plus one `PaymentProvider.Kind`. Which gateway to use is a business decision (Stripe is not available in Pakistan). Note for that adapter: a real refund is a network call made while holding the invoice lock - restructure if the gateway is slow.
- **Fulfilment still needs a system actor** and the customer "request without paying" flows still exist (Phase 09). Paying now marks the order paid and fires `order_paid`; nothing is provisioned from it yet.
- **Any contact of a client can see and pay its invoices**; restricting financial documents to owner/billing contacts is a later refinement.
- **Renewals, recurring invoices, credit balances, proration** - Phase 08. **Overdue reminders and dunning** - Phase 11 (overdue is already derived and filterable).
- **Tax:** still one rule per country, tax-exclusive; staff cannot override the rate on a manual invoice. Tax-inclusive and regional rules deferred.
- **PDF text is Western-European only** (built-in fonts); other scripts print as "?".
- **Staff are not notified when a customer reports a payment** (they see it on the Payments page; an in-app staff notification belongs with the notification centre work).
- **Removing a line from a draft that came from a billable item does not release that item** (deleting or cancelling the invoice does).
- **Online payment attempts never expire**; an abandoned pending attempt stays pending until superseded by the gateway's answer or an invoice cancellation.
- **One store currency** (existing deferral).
