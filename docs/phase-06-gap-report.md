# Phase 06 Gap Report: Cart & Checkout

**Date:** 2026-09-25
**Baseline:** Phases 01-05 complete (`869ed40`)

## FOUND

- **Catalogue pricing** - `products.services.get_effective_price()` was written in Phase 03 explicitly as "the single lookup later phases (cart, checkout, renewals) must call". Domain pricing lives in `domains.TldPricing` with `check_availability()`.
- **Clients** - billing details on `Client`, plus `contact_role` and `single_contact_client` for "which account is this user acting for".
- **Permissions** - `view_orders`/`manage_orders` and `view_billing`/`manage_billing` already existed and were already granted correctly (Support Agent: view both; Manager: view + manage both). No permission changes.
- **Notifications, audit, error format, API foundation** - reused as-is.

## PARTIAL / BROKEN

None. (Housekeeping, not a defect: the public plan page said "Ordering opens soon - Phase 06"; it now has an order form.)

## Scoping decision (worth reading)

The roadmap's flow diagram runs `Cart → Checkout → Payment → Invoice → Order`, but its own *phase definitions* split that work: Phase 06's feature list is hosting/domain selection, addons, duration, custom duration, customer information, discounts, tax, **payment method** and **final total**; **invoices, transactions, payment status and PDFs are Phase 07**, and the order-lifecycle screens are Phase 09. I followed the phase definitions, so **Phase 06 ends with an `Order` awaiting payment**: the customer chooses a payment method and is told how to pay, but nothing is charged, invoiced or provisioned yet. This is recorded in Known Work rather than quietly stretched.

## MISSING (now implemented)

The cart, the pricing engine (discounts and tax), checkout, orders, payment-method/tax/coupon configuration, and the customer and staff screens for all of them.

## REUSE

| Reused | How |
|---|---|
| `get_effective_price` | Every hosting and add-on price - never a number from a request |
| `domains.get_tld_pricing` / `check_availability` | Domain registration and transfer prices, and the availability check |
| `contact_role`, `single_contact_client` | Who may order for which client; the client a web user is acting for |
| `notifications.notify` / `send_email` | Order confirmation (in-app + email) |
| `audit.record` | `order.placed`, `order.cancelled`, and every payment-method/tax/coupon change |
| `PublicReadPermission` pattern | Generalised (not duplicated) into `AuthenticatedReadPermission` for payment methods |
| **Promoted, not copied:** `apps/core/web.py` | `apply_form_error`/`run_action` were about to become a fifth copy in the view modules; the two new apps share one implementation (older apps left as they are) |
| **Promoted, not copied:** `encode_option`/`decode_option` in `products.models` | One implementation of "a billing option as a form value (`annual`, `custom:4`)", used by both the catalogue page and the cart forms |

## CHANGES

- **`apps.billing` (configuration only):** `PaymentMethod` (what customers see at checkout: name, instructions, order, active), `TaxRule` (one per country; a blank country is the default rule; tax is added on top), `Coupon` (percentage or fixed; validity window, usage limit, once-per-client, minimum subtotal). Staff pages under `/staff/billing/`, a staff API, and read-only admin. **Phase 07 extends this app** with invoices and transactions rather than creating a second billing app. Payment methods are deliberately separate from payment *gateways* - Phase 07 attaches a live-configured gateway adapter to a method.
- **`apps.orders`:** `Cart`/`CartItem`, `Order`/`OrderItem`, `CouponRedemption`.
  - **A cart stores selections, never prices.** A cart item is "this plan, this billing cycle, this domain" - there is no price column, and no form or serializer has a price/total/discount/tax/expiry field. Every figure is recomputed on demand by **one pricing engine** (`apps/orders/pricing.py`).
  - **Pricing engine:** hosting and add-on prices come from the catalogue per billing cycle (including configured custom durations); add-ons must share the hosting plan's cycle or be a one-time charge; domains are TLD price × years (transfers a flat price including a one-year extension); then the coupon is applied to the subtotal, then tax to the discounted amount, all `Decimal` rounded half-up. Non-strict mode (cart pages) reports problems per line and still prices what it can; strict mode (checkout) refuses on any problem.
  - **Checkout** recomputes everything from the catalogue and refuses an invalid cart, an inactive payment method, a retired plan, a domain no longer available, or a coupon that is no longer valid - it never silently drops a discount. It runs in one transaction with row locks on the cart and coupon (so two carts can't both redeem a single-use code), and a slow registrar availability lookup happens *before* the locks are taken. The order carries a **snapshot** of every price, the tax rule and the client's billing details, so later edits to the catalogue or the client never rewrite history.
  - **Double-submit safe:** the first checkout closes the cart; a repeat is told "already checked out" (it checks the database, not a stale in-memory object) and creates no second order.
  - **Unpaid orders hold their domain names** (a soft reservation) until they are paid or cancelled, so a name can't be sold twice; cancelling releases the name and the coupon use.
  - **`Order` was created once with the roadmap's complete lifecycle vocabulary** (draft → pending payment → paid → processing → provisioning → active, plus fraud/failed/cancelled/suspended/terminated), but Phase 06 only uses `pending_payment` and `cancelled`; Phases 07 and 09 add the transitions and screens to this model.
- **Customer web:** the plan page and the domain-search result now have "Add to cart"; `/cart/` (line items, add-ons offered for the chosen billing cycle, coupon, transfer form, totals), `/checkout/` (billing details with a link to the existing account page to edit them, payment methods with instructions, notes) and `/account/orders/` (list, detail with payment instructions, cancel). A cart-count link in the navigation.
- **Staff web:** `/staff/orders/` (search by `O000123`, client, email, domain or coupon; filter by status; detail; cancel) and `/staff/billing/` (payment methods, tax rules, coupons - each a list plus an upsert form and an active toggle).
- **API:** `/api/v1/cart/` (get, `items/`, `coupon/`, `checkout/`), `/api/v1/orders/` (list, detail, `cancel`), `/api/v1/payment-methods/`, `/api/v1/tax-rules/`, `/api/v1/coupons/`. Users belonging to more than one client must say which (`client_id`) - it is an error to guess, since an order must never land on the wrong account. Someone else's client, cart item or order looks exactly like one that doesn't exist (404). The transfer authorization code is stored encrypted and is never returned by any endpoint or shown on any page.

## Defects found and fixed while building it

1. **Misleading error on a repeated checkout.** Found by the smoke test: submitting a checkout twice created no second order (good) but the message said "domain is in another order" instead of "already checked out", because a stale in-memory cart still looked open. Checkout now asks the database first; covered by a test.
2. **An empty POST body skipped validation.** `request.POST or None` treats an empty body as "no form submitted", so a checkout with nothing chosen showed no error. Checkout now binds the form on every POST; covered by a test.
3. **Mobile horizontal overflow on the order pages (found by the browser check).** CSS-grid children default to `min-width: auto`, so the items table stretched its card past a 375px screen. Fixed globally so wide tables scroll inside their own card.
4. **Two cosmetic layout faults (found by looking at the screenshots):** numbers left-aligned under right-aligned headers (a CSS specificity slip), and a side card so narrow on tablets that "Bank transfer" wrapped letter by letter. Both fixed.
5. **Checkout was completely broken on PostgreSQL (found by CI, then reproduced and confirmed locally).** Checkout locked the cart with `select_for_update().select_related("client", "coupon")`; the coupon is an optional foreign key, and PostgreSQL refuses `FOR UPDATE` across the nullable side of a join (`FeatureNotSupported`). SQLite silently ignores row locking, so all 455 local tests passed while every real checkout would have failed. It now locks the cart row alone. I confirmed the diagnosis by putting the old code back against a real PostgreSQL 17.5 and watching it fail with exactly that error.
6. **Upserts validated *after* inserting (found on PostgreSQL).** The two new billing upserts, and - as latent defects - `domains.set_tld_pricing` (Phase 04) and `products.set_price` (Phase 03), used `get_or_create()` followed by `full_clean()`. `get_or_create` INSERTs the unvalidated input first: SQLite accepts an over-long value and validation then rejects it, but PostgreSQL rejects it at the INSERT with a raw database error (a 500, not a friendly 400). All four now look up, validate, then write; each has a regression test. Two smaller length guards were added for the same reason (an order line description and the billing name are truncated to their column sizes).
7. **A test-isolation lesson repeated:** the new tests initially tripped over a leftover flash message in the shared test-client cookie jar; the assertion now checks the order link rather than the text.

## TEST PLAN

**161 new tests, 458 in total** (the suite before this phase was 297 - my Phase 05 report said 296, an off-by-one). All pass on SQLite **and, new this phase, against a real local PostgreSQL 17.5** (see below); CI runs PostgreSQL + Redis as before.

- **Pricing engine (46):** every price per billing cycle including a configured custom term; unknown and disabled cycles; hidden/retired plans and plans without a WHM package; add-on cycle rules (same cycle, one-time, orphaned, from another cart); domain price × years, term limits, unsupported TLDs, taken domains, and that availability is only re-verified when asked (not on every cart view); transfers (flat price, code required, code stored encrypted, existing domain rejected, one year only); duplicates; totals; percentage and fixed coupons (capped at the subtotal); the order of operations (discount, then tax on the discounted amount); half-up rounding with worked figures; tax by country → default → none, and inactive rules; non-strict vs strict; every coupon rule (unknown, case-insensitive, expired, not yet valid, inactive, minimum, usage limit counting only non-cancelled orders, once per client) and a coupon that lapses after being applied being flagged rather than silently kept.
- **Cart and checkout (29):** who may shop (non-contact denied; any contact role and staff with `manage_orders` allowed; Support Agent denied); one open cart per user and client; `resolve_client` (ambiguity is an error, a stranger's client looks missing); nobody can touch another user's cart; the order's figures equal the pricing engine's, to the cent; add-ons stay attached to their hosting line; the cart closes and the coupon redemption is recorded; billing details are snapshotted and survive later client edits; audit, notification and email content; the auth code stays encrypted; empty cart, missing/inactive payment method, repeated checkout, a closed cart, a plan retired after adding, a domain taken after adding, a price changed after adding (the order carries the *new* price), an expired coupon, a single-use coupon contested by two carts, and **a failure part-way through leaving no order, no items, no redemption and the cart still open**; domain reservation and release; cancellation rules and permissions; visibility and search.
- **API (31):** authentication on every endpoint; the priced cart; each item kind's required fields; **prices, totals, discounts, tax and status sent by the client are ignored** (checked on both the cart and checkout); the auth code never appears in any response; multi-client users must name the client; a stranger gets 404; staff can shop on a client's behalf; coupons; checkout and its errors; order scoping, search and filters; orders are read-only apart from cancelling; Support Agent can read but not cancel.
- **Web (29):** signed-out vs signed-in plan and search pages (every configured term offered, including custom durations); the whole cart flow; tampered price fields have no effect; bad selections give a message and add nothing; add-ons offered only for the right cycle; other customers' items/orders are 404; coupon apply/remove/lapse; the nav count; checkout with its billing details, methods and instructions; refusing when something changed since adding; a technical contact can order; order list/detail/cancel; the staff pages and their permissions.
- **Billing configuration (23):** coupon maths and windows, tax validation, upserts, audit, every write needing `manage_billing`, customers seeing only active payment methods, the payment-method API being signed-in-only, coupons and tax rules being staff-only, and the staff pages.
- **Browser check:** headless Chromium at 1366, 768 and 375px covering the full journey - anonymous visitor → sign in → plan page → cart (hosting, add-on, domain from search, transfer, coupon) → checkout → order with payment instructions → cart emptied - then staff orders, an order, and all three billing pages. No JavaScript errors, no page-level overflow, and it confirmed the transfer code never appears on a page. It caught defects 3 and 4 above.

## PHASE 06 EXIT CRITERIA

| Criterion | State |
|---|---|
| Existing implementation inspected, no duplicates | ✅ (two would-be duplicates promoted to shared code instead) |
| Prices, totals, discounts, tax, duration and credit are never taken from the browser | ✅ tested at the service, API and page level |
| One pricing engine; orders snapshot its output | ✅ |
| Checkout atomic and safe against a double submit | ✅ tested, including a mid-checkout failure |
| Authorization tested; full suite green | ✅ 458 passing on SQLite and on real PostgreSQL 17.5 |
| Migrations created, `makemigrations --check` clean, OpenAPI schema valid with no warnings | ✅ |
| Browser check (desktop, tablet, mobile) | ✅ |
| CI green on PostgreSQL + Redis | ⏳ First run (`c5470b8`) failed on the two PostgreSQL-only defects above; fixed, re-running |
| Commit after verification | ⏳ |

## A change to how I verify

This phase showed that SQLite hides real PostgreSQL failures, and CI logs can't be read without authentication, so guessing from a red CI run isn't good enough. I set up a portable PostgreSQL 17.5 (kept outside the repository) and ran the entire suite against it - that is how the two defects above were reproduced and the fixes proven. From now on each phase is run against PostgreSQL locally *before* pushing. The recipe is saved in my project notes.

## Known limitations / deferred

- **No payment, invoice or fulfilment yet - by the roadmap's own phase split.** An order sits at "pending payment" until Phase 07 (payments, invoices, transactions) and Phase 09 (lifecycle) exist. Nothing is provisioned from an order yet.
- **Fulfilment needs a "system" actor.** The domain and hosting services authorise a *user*; provisioning after a payment confirmation has no user. Phase 07/09 must decide how to run those services as the system (with an audit trail) - flagged now so it isn't a surprise.
- **The customer "request without paying" flows still exist** (register/transfer a domain, request hosting - the domain search page keeps a secondary link to it). They bypass checkout and should be retired once fulfilment is wired, so there is one way to buy.
- **Signed-in carts only.** Guest (anonymous) carts are deferred; the public pages ask visitors to sign in to order.
- **Discounts apply to the first payment only**, with no per-product restrictions or recurring discounts (renewals arrive with Phase 08).
- **Tax is deliberately simple:** one rule per country (or a default), added on top, calculated for the whole order. No tax-exempt clients, tax-inclusive pricing or regional (state) rules yet - Phase 07 will allocate tax to invoice lines.
- **One store currency** (`STORE_CURRENCY`); the existing multi-currency deferral applies.
- **Unpaid orders hold domain names until cancelled** - no automatic expiry yet (a scheduled job, Phase 08/17).
- **No terms-of-service acceptance** at checkout.
- **The web cart needs the user to belong to exactly one client** (the API takes a `client_id`); a client chooser is Phase 15 polish. **Staff can build a cart for a client via the API**; a staff "Add Order" screen is Phase 09.
