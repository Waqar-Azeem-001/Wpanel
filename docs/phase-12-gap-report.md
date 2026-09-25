# Phase 12 Gap Report: Cancellation & Service Lifecycle

**Date:** 2026-09-25
**Baseline:** Phases 01-11 complete (`18b7948`), plus `363f8c7` (manual payments record the date received)

## FOUND

- **Hosting** could be suspended, unsuspended and terminated by staff (`hosting.services`), always through the server adapter, audited, with the owner emailed. It had a paid term (`expires_at`, `term_paid`, Phase 08) but **nothing acted on it**: an unpaid account simply stayed Active forever.
- **Domains** had a status, an expiry, `auto_renew` and renewal invoices (Phase 08); no cancellation. A domain's only "cancelled" meaning was an abandoned *pending request*.
- **Renewals** were invoiced daily (Phase 08) with no notion of a service being cancelled; a paid renewal applied to an Active *or Suspended* account.
- **Refunds** existed as a billing operation on a payment (`payments.refund_payment`); with manual payments (decided 2026-09-25) a refund is a recorded transaction, not a gateway call.
- **Reminders** for unpaid invoices existed (Phase 11), and the Phase 11 report deferred what happens after them to this phase.
- Roadmap Known Work already listed **unsuspend-on-payment** as deferred.
- Searched for any cancellation or lifecycle code: none.

## PARTIAL / BROKEN

1. **An unpaid hosting account never lapsed.** Expiry, overdue and the reminders were all informational; a customer who never paid kept a live account, and staff had no list of who was late.
2. **A renewal invoice could be created for a service the customer wanted to end**, and would then be reminded about, then paid or ignored.
3. **A payment on a suspended account did not reactivate it.** Staff had to notice and unsuspend by hand.
4. **No record of *why* a service ended** and no way for a customer to ask.

## MISSING (now implemented)

Cancellation request, reason, immediate or end-of-term choice, admin review, approval, suspension, termination, domain handling, refund and final-billing handling, and the lifecycle `Active -> Renewal due -> Overdue -> Grace -> Suspended -> Terminated` with configurable timing: every item on the roadmap's Phase 12 list.

## REUSE

| Reused | How |
|---|---|
| `hosting.services.suspend_account` / `terminate_account` / `unsuspend_account` | The lifecycle job and every cancellation end a service through these, as the `SYSTEM` actor or the reviewing person: no second path to the server |
| `core.system.SYSTEM`, `audit.record` | Automatic actions are audited as "system" |
| `payments.refund_payment` / `refundable_amount` | The only way money moves; nothing here writes a transaction |
| `invoicing.cancel_invoice_internal` | Final billing cancels an unpaid renewal invoice; its cancel signal already voids the `ServiceChange` |
| `renewals.proration.unused_credit` | The *suggested* refund is the unused part of the paid term, by the same rule as an upgrade credit |
| The Phase 08 `ServiceChange` and Phase 09 `OrderItem` links | Find which payments paid for a service, to offer them for refund |
| The state-table + one `transition()` pattern (orders, tickets) | `lifecycle/transitions.py` |
| `notifications.dispatch_client` / `notify_team`, the event registry | Five customer emails and one team alert, all registered |
| Celery beat, the `manage.py` + dry-run pattern | `run_lifecycle` |

## CHANGES

- **New `apps.lifecycle` app.** `CancellationRequest` (one service, reason, timing, status, review, effective date, refund fields), `LifecycleSettings` (single row), `LifecycleNotice` (one final notice per paid term). Migrations `lifecycle 0001`, `hosting 0003` (`suspended_for_nonpayment`).
- **The stage is derived, not stored** (like "overdue" on an invoice). From the paid-through date and the status: *Active* (outside the renewal window), *Renewal due* (inside the Billing setting's window), *Overdue* (expired, before the grace point), *Grace period* (past it, still running, final notice sent), *Suspended*, *Terminated*; a domain can also be *Expired*. A service with no recorded term is Active and can never lapse. Shown on the hosting and domain pages, in the API (`lifecycle_stage`) and on a staff **Lifecycle overview**.
- **Configurable timing** (Lifecycle -> Timings, `manage_settings`): grace after 3 days, suspension after 7, termination after 30 (days after the paid period ends; validated to be in that order); switches for automatic suspension (on), **automatic termination (off)**, and lifting a suspension when paid (on). Termination deletes the customer's data, so it is only ever automatic if someone turns it on.
- **The daily sweep** (`run_lifecycle`, 05:00, also `manage.py run_lifecycle [--dry-run]`): carries out cancellations that are due; sends the **final notice** once per paid term when the grace period starts; suspends accounts that stayed unpaid; terminates those that stayed suspended *for non-payment* if switched on. It never suspends an account while the customer has a payment **reported and waiting for confirmation**, never terminates an account staff suspended for another reason, and one failing account never stops the rest.
- **Paying lifts a non-payment suspension.** A paid renewal sends a signal; if the suspension was automatic (`suspended_for_nonpayment`), the setting is on and the new expiry is in the future, the account is unsuspended. A suspension a person made stays until a person lifts it. If the server refuses, the payment stands and staff are left an audit entry.
- **Cancellation requests.** The **owner** of the account (not every contact) asks for a live service, with a reason (list, plus text for "Other") and, for hosting, *immediately* or *at the end of the paid period*. One open request per service (database constraint). The customer is emailed; staff who can manage that kind of service are alerted.
- **Review.** Staff who manage the service approve (choosing the timing) or decline (a reason, shown to the customer). **Approval does the final billing at once:** unpaid renewal invoices for the service are cancelled (a partly paid one is left and flagged for Billing), a domain's auto-renew is turned off (and restored if the request is withdrawn), and renewals and upgrades are refused for a service with an open request. Then an immediate cancellation ends the service now; an end-of-term one is scheduled for the day the paid period ends and carried out by the daily sweep.
- **Domain handling.** A registration cannot be cancelled early: it is set not to renew and ends (status Cancelled) when it expires. Ending a hosting account leaves the customer's domains alone.
- **Refunds.** Only for an immediate cancellation, only by someone who can also manage billing, and only from a *payment for that service* (its order or an applied renewal/upgrade). The form shows the suggested amount (the unused part of the term, ex tax; a suggestion, not a promise) and the refundable payments; the amount is checked against what is still refundable. It is executed through the existing refund, after the service has ended; if the refund fails the service stays ended, the request records the error and staff refund it from the invoice.
- **Carrying it out safely.** Ending a service is *claimed* with a lock-free update before the server is called, so two workers or two clicks never end it twice; a dead worker's claim expires after ten minutes; a service that already ended just completes the request; a failure keeps the request Approved with the error and a *Carry out now* button, and the daily sweep retries it. A request being carried out cannot be withdrawn.
- **Screens.** Customer: *Request cancellation* on hosting and domain pages (only for the owner), the request form (with a confirmation), the request page (status, note from the team, withdraw), *Cancellation requests*. Staff: **Lifecycle** in the navigation: overview (to review, scheduled, renewal due, overdue, grace, suspended, and the accounts in each), the cancellation queue (search, status, hosting/domain), the request page (approve with timing/refund/note, decline, carry out now, withdraw), *Cancel this service* on staff hosting/domain pages (cancels on the customer's behalf in one step), and the timings page.
- **API.** `/api/v1/cancellations/` (list with filters, create, `withdraw`, `approve`, `reject`, `retry`, `refund-options`), `/api/v1/lifecycle/settings/` (GET, PUT, PATCH), `/api/v1/lifecycle/overview/`, and `lifecycle_stage` on hosting accounts and domains. A customer never sees which staff member handled a request or an internal error. A stranger's service or request is 404.
- **Notifications** (registered in the Phase 11 registry, all essential): request received, approved, declined, domain cancelled, and **final notice before suspension**; a team alert when a customer asks. Hosting termination keeps using its existing email.
- **Settings:** the beat entry `run-service-lifecycle`. No new environment variable.

## Defects found and fixed while building it

1. **A part-paid renewal invoice was skipped silently** by final billing (found by a test): staff were not told it was left open. It is now reported in the audit trail and flagged.
2. **A renewal could be invoiced for a service that was being cancelled.** Blocked in the service layer (create, upgrade and the daily generator).
3. **A stale claim would have blocked a request forever** if a worker died mid-way; claims now expire.
4. **My own test mistakes:** two assertions written as `... or True` (which assert nothing) were replaced with real ones before running anything; a test that moved the service's expiry instead of the clock (a cancellation's end date is fixed at approval, on purpose) was corrected; Django 5 no longer has `timezone.utc` (caught in the manual-payments test I had just written).

## TEST PLAN

**97 new tests (64 services, 31 web and API, 2 concurrency): 1046 in total on PostgreSQL** (1034 on SQLite; the 12 concurrency tests skip there). All pass against a local PostgreSQL 17.5 before pushing.

- **Services (64):** the stage for every offset from the paid-through date, other statuses, no term, domains, and how settings move it; settings validation, permission and audit; requesting (only the owner, staff on behalf, reason/timing/service rules, one open request, a fresh one after it closes, the customer and team notified); approval at end of term and immediately, timing override, a lapsed account, a suspended account, **final billing** (open renewal invoices cancelled; a part-paid one left and flagged), no renewals or upgrades while a request is open, permissions, approving only once; declining and withdrawing (who, when, a domain's auto-renew restored, not once carried out); **refunds** (suggestion, only this service's payments, only when ending immediately, exceeding the payment, a foreign payment, a failed refund leaving the service ended); domains ending at expiry; **failure and retry, claims and stale claims, an already-ended service**; the daily job; **the lifecycle** (final notice once per term, suspension timing, switches and configurable timings, termination off by default, only a non-payment suspension is ever auto-terminated, a payment awaiting confirmation stops suspension, accounts without a term left alone, dry run, one failure not stopping the rest); **paying lifts a non-payment suspension** but never another kind, nor when switched off, and a server failure never undoes the payment; overview and visibility.
- **Web and API (31):** the customer flow through real forms (confirmation required, domain form without timing, one request, non-owners refused, strangers 404, the customer never sees the reviewer or an internal error); the staff overview, queue filters, approve, approve with a refund, a refund payment that was not offered, decline, an agent who can read but not act, a failed request needing attention and retried, withdrawing, cancelling on behalf (with a failing server), the cards, the timings page and its validation; the API (create/list/scope/filters, permissions on every action, refund options, settings, overview, `lifecycle_stage`, authentication); the command and the task.
- **Concurrency (2, PostgreSQL):** two reviewers approving one request approve it once; two workers ending one service end it once. Each fails when its lock (or claim) is removed.
- **Mutation checks:** nine rules were broken in turn (claim, owner-only, suspension flag, final billing, pending-payment guard, termination flag, refund timing, repeated notice, auto-renew restore) and each was caught by a test.
- **Browser check:** headless Chromium at 1366, 768 and 375px: a customer sees the stage and the suspension date on a lapsed account, sends a hosting request (first without the confirmation: refused) and a domain request (no timing question); a manager sees them on the overview and queue, approves both, cancels the lapsed account immediately on the customer's behalf; an admin changes the timings (an out-of-order value is refused); the customer then sees the approval (and never the reviewer's address), withdraws the domain request and sees the other account terminated. No JavaScript errors, no overflow.

## PHASE 12 EXIT CRITERIA

| Criterion | State |
|---|---|
| Existing implementation inspected, no duplicates | ✅ ends services only through the existing hosting/refund services |
| Cancellation request, reason, immediate/end-of-term, review, approval | ✅ |
| Suspension, termination, domain handling, refund/final billing | ✅ |
| Lifecycle Active -> Renewal Due -> Overdue -> Grace -> Suspended -> Terminated, configurable | ✅ |
| Authorization tested; full suite green on SQLite and real PostgreSQL | ✅ 1046 |
| Migrations, `makemigrations --check`, OpenAPI with no warnings | ✅ |
| Browser check (desktop, tablet, mobile) | ✅ |
| CI green on PostgreSQL + Redis | pending at time of writing |

## Known limitations / deferred

- **Domains are never suspended or terminated by the lifecycle**, and their status is not flipped to Expired by it: the registrar owns what happens after expiry (grace, redemption), and flipping the status would stop the existing renewal of a just-expired domain. Only cancellation acts on domains.
- **Automatic termination is off by default** and deletes data when on; there is no backup or "hold before delete" step yet (Phase 17).
- **Refunds are a staff decision.** The amount is suggested, never automatic, and covers the paid term ex tax (tax and setup fees are refunded only by entering a larger amount); no refund is offered for an end-of-term cancellation.
- **A cancellation's end date is fixed at approval.** Extending the term by hand afterwards does not move it (withdraw and re-approve).
- **One paid term per suggestion:** the suggestion uses the current term's paid value; older terms' payments can be refunded by entering the amount.
- **The timings are global**, not per product or per client; the reminder schedule (Phase 11) and these timings are separate settings.
- **No "win-back" or retention offers, exit survey analysis or cancellation reports** (reports are Phase 14).
- **A customer cannot cancel an order or a pending service** through this flow (a pending request is cancelled by staff, as before); it is for live services.
