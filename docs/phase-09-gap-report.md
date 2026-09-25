# Phase 09 Gap Report: Order Management & Lifecycle

**Date:** 2026-09-25
**Baseline:** Phases 01-08 complete (`9b4385b`)

## FOUND

- **`Order` with the full lifecycle vocabulary** (created once in Phase 06 with all eleven statuses) but only `pending_payment`, `paid` and `cancelled` were ever used, each set by hand in a different place.
- **Staff order screens** (list, detail, cancel) and the customer order pages from Phase 06.
- **Three debts explicitly handed to this phase** in earlier gap reports: (1) a paid order provisions nothing, and the domain/hosting services authorise a *user*, so background work had no way in (the "system actor" problem); (2) the customer "request without paying" flows still bypassed checkout; (3) nothing started a hosting term when an order was paid (Phase 08).
- **Domain and hosting services** already had everything fulfilment needs (`request_hosting`, `complete_provisioning`, `request_registration`, `complete_registration`, `request_transfer_in`, `complete_transfer`, suspend/unsuspend/terminate) - and Phase 08's `renewals` app for terms.
- Searched for any existing fulfilment, state machine or fraud handling: none.

## PARTIAL / BROKEN

Two things that only worked by accident of never being exercised together: `mark_order_paid` and `cancel_order` each wrote the status and their own audit event directly, with no rule about which moves were legal. Both now go through the state machine below.

## MISSING (now implemented)

The order state machine, automatic fulfilment of a paid order, staff actions (fraud, retry, suspend, reactivate, terminate), the roadmap's screens (All / Pending / Active / Fraud / Cancelled / Add Order) and the customer-facing progress view.

## REUSE

| Reused | How |
|---|---|
| Domain/hosting service functions | Fulfilment calls the *same* functions staff use; there is no second provisioning code path |
| `renewals` (Phase 08) | Starts the hosting term after provisioning, through one new system entry point (`start_hosting_term`), idempotent |
| The cart and checkout machinery | **Add Order** is the client's own cart logic driven by staff (`get_open_cart` already allowed `manage_orders`): same pricing engine, same checkout, same invoice - no parallel order-building code |
| `invoice_paid` / `order_paid` signals | Payment triggers fulfilment; `billing` never learns about provisioning |
| Audit, notifications, the Phase 07/08 invoice and integrity machinery | Unchanged |

## CHANGES

- **The state machine (`orders/lifecycle.py`).** One table of legal moves, and one function, `transition()`, that every status change in the system goes through. It refuses an illegal move (409 `invalid_transition`) and writes an audit event with the old and new status - "every state transition must be auditable" holds by construction. The order page shows the resulting history.

      Draft -> Pending Payment -> Paid -> Processing -> Provisioning -> Active <-> Suspended -> Terminated
      Pending Payment / Failed / Fraud -> Cancelled;   Pending / Paid / Failed <-> Fraud;   Processing / Provisioning -> Failed -> (retry) Processing
- **The system actor (`core/system.py`).** Background work passes `SYSTEM` to the ordinary services rather than getting an unchecked side door. It holds every permission, so it is only ever constructed by code (never reachable from a request or an API field), and audit records it as "system". This answers the question Phases 06-08 kept deferring.
- **Fulfilment (`orders/fulfilment.py`).** Once a payment commits, a Celery task runs Paid -> Processing -> Provisioning -> Active: hosting lines are created, assigned a server, provisioned on it, given their paid term; domain lines are registered or transferred in; add-ons are recorded against their plan. It is **idempotent and resumable**: every order line remembers what it became and whether it is done, so a retry only redoes what is outstanding and can never create a second account or domain. The claim is taken under the order's row lock (two workers cannot fulfil one order), and the slow WHM/registrar calls run outside any lock. A failing line fails the order *without undoing what worked* ("Failed", with the reason), and one broken line never stops the others. A broker outage cannot undo a payment (the task is queued after commit; a **sweeper every 5 minutes** picks up any paid order that was never queued and resets any order stuck mid-way to Failed so staff can retry).
- **The hosting term starts automatically**, with the paid value taken from the invoice line for the plan (after its share of any discount; excluding setup fee, tax and add-ons) - so a later upgrade credit is based on what was really paid.
- **Staff actions:** flag fraud (nothing is fulfilled while held, even if the invoice is paid) and release it; retry a failed fulfilment; close a failed or fraud order; suspend / reactivate / terminate an active order, cascading to its hosting accounts. A cascade that cannot finish (a server unreachable) leaves the order unchanged and names the failing service, so it can be re-run; each step skips what is already done.
- **Screens.** Staff: group tabs with live counts (All / Pending / Active / Fraud / Cancelled), the order page (fulfilment status per line with links to the created hosting/domain, the failure reason, actions appropriate to the current status, the invoice, the full history), and **Add Order** (client search -> the client's cart built by staff -> place). Customer: the order page now says where it is ("Setting up your order", "We are finishing your order", "under review", "Your services" with links) - and never shows internal failure detail.
- **The "request without paying" flows are retired** (the debt from Phases 04-06): the customer pages for requesting hosting, registering and transferring a domain are gone, and creating a hosting account or domain is now a staff or system action *in the service layer itself* - so it holds for the API as well as the pages. There is one way to buy.
- **API.** Orders carry `status_reason` and per-line fulfilment (`fulfilment_status`, and the failure reason for staff only); `?group=` filter; `POST /orders/{id}/fraud|clear-fraud|retry-fulfilment|suspend|unsuspend|terminate/`; `GET /orders/{id}/timeline/` (staff).
- **`verify_billing`** also checks orders: an order that is Active/Suspended with an unfulfilled line, paid-looking with an unpaid invoice, awaiting payment with a paid invoice, or a "done" line with no service attached or pointing at another client's service.
- **Migrations:** `orders 0002` (status reason, per-line fulfilment and links). New setting `ORDER_AUTO_FULFIL` (on; off under test settings so tests of other behaviour can pay an order without provisioning).

## Defects found and fixed while building it

1. **An order could become Active with a line still unfulfilled.** An add-on read a stale copy of its plan's status (loaded before the plan was processed), stayed pending, and the order still went Active. Fixed twice over: the add-on re-reads the plan from the database, *and* an order is never activated unless every line is done. Caught by the first end-to-end test; `verify_billing` now checks for it too.
2. **The Add Order page crashed on any error** (`error_text` was used but never imported). Found by the error-path test written to prove that hostile input is reported cleanly.
3. **Mobile overflow on the staff order page:** the action forms (a reason box plus a button, side by side) did not wrap on a 375px screen. Found by the browser check; fixed globally so any such action group wraps.
4. **A stranger got 403 instead of 404** on an order's timeline (the permission check ran before the lookup); reordered so someone else's order looks exactly like one that does not exist.
5. **A serializer-name collision** (`ReasonSerializer` existed in `billing`) that the schema validation reported; renamed.
6. **A concurrency check that must fail without its lock:** with the order-row lock removed from the fulfilment claim, both racing tests fail (a second account and domain are created); with it, they pass.
7. **Six existing tests encoded the retired behaviour** (a customer creating a hosting account or domain directly). They now assert the opposite - customers are refused, staff and the system are not.

## TEST PLAN

**59 new tests: 788 in total on PostgreSQL** (778 on SQLite; the 10 concurrency tests skip there). All pass against a local PostgreSQL 17.5 before pushing.

- **Fulfilment (34):** a paid order becomes Active with its hosting (server, package), domain (2-year expiry) and add-on recorded; the term starts with the right paid value (an exact worked figure with coupon, tax and setup fee); every transition is audited with from/to and the system as actor; transfers (auth code cleared); running it again does nothing; nothing is queued when disabled or unpaid; a dead broker never undoes a payment and the sweeper recovers; a failing line fails the order but keeps what worked, and after the cause is fixed a retry finishes it with nothing created twice; a registrar failure then retry; an unexpected error in one line is contained; **eight illegal moves refused** and change nothing; the table has no way out of the final states and every status is reachable; a fraud hold stops fulfilment even when paid, and releasing it resumes; permissions; staff can close a failed/fraud order and customers cannot; suspend/reactivate/terminate follow the hosting; a failed cascade leaves the order unchanged and can be re-run; the sweeper resets stuck orders and leaves healthy ones; the system actor is recorded as no user; `verify_billing` catches each inconsistency.
- **Screens and API (19):** group tabs and their counts and filters; Add Order visible only to those who can manage orders; the order page (history, fulfilment, buttons per status); every action from the page; the customer's message for each status **without internal detail**; links to the created services; the whole Add Order flow ending in a paid, fulfilled order (with a coupon, a transfer whose code never appears, and a check that a posted price is ignored); error reporting; the API's fulfilment fields hidden from customers; the group filter; every action, permission and 409; the timeline; a stranger's order looks missing.
- **Concurrency (2, PostgreSQL):** two workers, and three, on one paid order create one hosting account and one domain, and the history shows a single run in order.
- **Browser check:** headless Chromium at 1366, 768 and 375px: a customer pays by card and the order activates by itself with its hosting page showing the started term; an order for a plan with no server fails and the customer sees a reassuring message; staff open it, see the reason, assign a server, retry, and it becomes Active; fraud hold and release; suspend and reactivate; Add Order end to end. It found defect 3. `verify_billing` on that database: consistent.

## PHASE 09 EXIT CRITERIA

| Criterion | State |
|---|---|
| Existing implementation inspected, no duplicates | ✅ |
| Screens: All, Pending, Active, Fraud, Cancelled, Add Order | ✅ |
| Lifecycle Draft -> ... -> Active plus Fraud/Failed/Cancelled/Suspended/Terminated | ✅ one table, one function |
| Every state transition auditable | ✅ enforced by `transition()`, tested |
| Authorization tested; full suite green on SQLite and real PostgreSQL | ✅ 788 |
| Migrations, `makemigrations --check`, OpenAPI schema with no warnings | ✅ |
| Browser check (desktop, tablet, mobile) | ✅ |
| CI green | pending push |

## Known limitations / deferred

- **Add-ons have no provisioning step**; they are recorded against the plan and marked done when the plan is. Add-on-specific provisioning (and staff adding add-ons or custom-month cycles in Add Order) is deferred.
- **Suspend/terminate act on hosting only.** Domains are registrations and are left as they are; terminating an order does not cancel a domain.
- **A refund does not cancel or terminate what was fulfilled**; staff do that by hand (the invoice page and the order actions are both one click away).
- **No automatic fraud scoring** - fraud is a staff decision (Phase 14/16 reporting and security can add signals).
- **No proactive alert when fulfilment fails** beyond the "Failed" state, the filtered list and the audit trail; staff notifications belong with the notification centre (Phase 11).
- **`Draft` orders are unused** (a cart plays that role); the status stays in the vocabulary.
- **Suspended hosting is still not unsuspended by paying a renewal**; reactivation is a staff action (or the order action), by design for now.
- **Fulfilment makes its WHM/registrar calls in a background task, one line after another**; fine at this scale, worth parallelising or batching if orders grow large.
