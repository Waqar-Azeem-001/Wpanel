# Phase 13 Gap Report: Affiliate Management

**Date:** 2026-09-25
**Baseline:** Phases 01-12 complete (`e39874e`)

## FOUND

- Nothing affiliate-related in the code: the roadmap lists Affiliates and Commissions among the data domains and an "Affiliate" section on the client profile, and the Known Work table said the client profile was "partly closed" waiting for cancellations and affiliates.
- **The money events already existed:** `invoice_paid` (sent when an invoice becomes fully paid) is the right trigger for a commission; but there was **no event for a refund**, so nothing could react to money going back.
- **Registration** (`register_user`, web form and API) created a client for every sign-up and was the one place attribution could be decided.
- Permissions had no affiliate area; the event registry, the daily-job pattern and the staff/customer page patterns were all reusable.
- Payments are manual (decision of 2026-09-25), so a payout to an affiliate is a *recorded* payment made outside the portal, not a gateway call.

## PARTIAL / BROKEN

1. **No refund signal.** A refund changed `amount_refunded` on the invoice and told nobody. Added `invoice_refunded`, sent from the one function that rebuilds an invoice's money (`recompute_invoice`), so manual refunds, cancellation refunds and gateway refunds all reach commissions.
2. **The client profile promised affiliate and cancellation records "as those modules are delivered".** Both now appear (a Cancellations section from Phase 12 and an Affiliate section), and staff can credit a missed referral from the profile.

## MISSING (now implemented)

Everything on the roadmap's Phase 13 list: affiliate accounts, referral links, attribution, commission rules, commission records, the statuses pending / approved / paid / rejected, payout history and reports, with the two commission types (percentage, fixed amount).

## REUSE

| Reused | How |
|---|---|
| `invoice_paid` signal, `recompute_invoice` | A paid invoice earns a commission; the new `invoice_refunded` signal is sent from the same place |
| `register_user` and its web/API entry points | Attribution happens inside sign-up, never able to break it |
| The Phase 11 event registry, `dispatch` / `notify_team` | Four affiliate emails and two team alerts, all registered |
| `billing.calculations.money`, `renewals.proration.add_months` | Half-up rounding; the months window for recurring commissions |
| The settings-singleton pattern (`BillingSettings`, `LifecycleSettings`) | `AffiliateSettings` |
| The service-layer / `run_action` / state-table / claimed-by-lock patterns | Every rule in `services`; views and API only call it |
| `AuthenticatedReadPermission`, `HasPortalPermission` | API permissions |

## CHANGES

- **New `apps.affiliates` app:** `Affiliate`, `Referral`, `Commission`, `Payout`, `AffiliateSettings`. Migrations `affiliates 0001`, `accounts 0002` (the two new permissions).
- **New permissions** `view_affiliates` / `manage_affiliates`: Manager and above have both; Support Agents have neither (they cannot see affiliate data at all).
- **Affiliate accounts.** A signed-in customer joins (accepting the programme terms, optionally giving payout details); active at once, or *awaiting approval* if the programme asks for it (default). Staff approve, reject (with a reason the applicant sees), suspend and reactivate; each move is audited and emailed where the affiliate needs to know.
- **Referral links** `/r/<code>/`: a random 8-character code with no look-alike characters (staff can change it to something memorable). The link sets an HttpOnly, SameSite cookie for a configurable number of days and redirects home (or to a local `?to=` path; anything off-site becomes home). **A dead, unknown, suspended or switched-off link answers exactly like a live one** but sets nothing and counts nothing, so codes cannot be probed. The last link followed wins; the same browser is not counted twice; only a count is kept (no address, no identity).
- **Attribution** happens once, at sign-up (web form reads the cookie, API takes `referral_code`) and is permanent: a later click never changes it, an affiliate is never credited with their own account, and a bad, stale or unapproved code is silently ignored so it can never stop someone registering. Staff can credit an existing client once (audited).
- **Commission rules.** A programme default (percentage or fixed amount, plus how many months a customer keeps earning: *0 = only the first paid invoice*) and an optional **per-affiliate rule** that overrides it. A fixed commission is capped at the sale.
- **Commission records.** One per invoice, **earned when the invoice is paid in full** (a part payment earns nothing until it is completed), worked out on the invoice *excluding tax and after discount*, and **frozen at that moment**: changing the rules later never rewrites earned commissions. Statuses: **Pending** (a hold period, default 14 days, as a refund window) -> **Approved** (automatically by the daily job when the hold ends, or by staff sooner) -> **Paid** (in a payout); staff can **Reject** a pending or approved one with a reason. A suspended affiliate earns nothing new and is not auto-approved or paid while suspended.
- **Refunds follow the money.** A refund lowers an unpaid commission in proportion (a full refund voids it, with the reason recorded and the original amount kept); one already paid out is **never silently taken back**: it is flagged "Refunded after payout" and staff are alerted.
- **Payouts.** Staff pay the affiliate outside the portal, then *record* it: method, transaction ID, date paid (never in the future), covering all approved commissions or those chosen, at least the minimum payout (default 50.00). Each commission can be in only one payout (a database rule plus a row lock), so a double-submit finds nothing left; the affiliate is told; the history is on both sides.
- **Reports.** Affiliate dashboard: link, visits, sign-ups, customers who paid, balances (approved / pending / paid), recent commissions and payouts. Staff overview: active affiliates, visits, sign-ups, commission totals by status and count, total paid out, top affiliates, what needs review (7 / 30 / 90 days or all time). **An affiliate never sees a customer's name, email or invoice**, only "Customer R00001" and the sale amount.
- **Screens.** Customer: *Affiliate* in the navigation (join, dashboard, commissions with status tabs, payouts, payout details). Staff: *Affiliates* (overview, affiliates list with search and status, affiliate page with approve/reject/suspend, own rule, code, record a payout, commissions, payouts, referred customers; commissions queue with approve-now / reject; payouts list; programme settings) and the client profile (referred-by, credit a missed referral, cancellations).
- **API.** `/api/v1/affiliate/` (join, read, payout details), `/affiliates/` (list, search, approve, reject, suspend, reactivate, `rule`, `code`, `payout`), `/commissions/` (list, filters, approve, reject), `/payouts/`, `/affiliate-settings/` (read: any signed-in person; write: `manage_settings`), `/affiliate-report/`; `referral_code` on registration.
- **Notifications:** affiliate approved, not accepted, commission approved (this one can be switched off), payout recorded; team alerts for a new application and for a refund after payout.
- **Settings:** the beat entry `approve-commissions` (06:00, also `manage.py approve_commissions`). No new environment variable, no credentials.

## Defects found and fixed while building it

1. **The settings API validated the body before checking permission**, so a non-admin sending a bad body got a 400 instead of 403. Found by a test; it now uses the same read-open / write-permissioned class as the other settings.
2. **My own test mistakes:** several assertions or setups I first wrote as `if False`, `... or True`-style placeholders (which check nothing) were replaced with real ones before running; a tax fixture written for the US never applied to clients with no country (the default rule is the one they get); and an early mutation check that "passed" only because a different Python interpreter had been used, so it was redone with the project's.

## TEST PLAN

**88 new tests: 1134 in total on PostgreSQL** (1120 on SQLite; the 14 concurrency tests skip there). All pass against a local PostgreSQL 17.5 before pushing.

- **Services (49):** settings validation, permission and audit; joining (approval, terms, disabled programme, one account, unique unambiguous codes); reviewing (approve, reject with a reason, suspend, reactivate, illegal moves, permissions); per-affiliate rules and their validation; code changes; payout details; live links; attribution (case-insensitive, never stops sign-up, suspended / off / unapproved codes, never the affiliate's own account, never re-attributed, staff credit once); **earning** (percentage on the ex-tax base, after a discount, fixed capped at the sale, own rule beats default, frozen when paid, first invoice only, the recurring window, part payments, who does not earn, repeated signal, no hold); **refunds** (voided, proportional, approved follows, paid flagged once, harmless without a commission); approval (early by staff, the daily job and the hold, suspended affiliates wait, refunded not approved); rejecting; **payouts** (records and pays, repeat finds nothing, every rule, chosen commissions, another affiliate's commissions refused); visibility; the report; stats; permissions.
- **Web and API (37):** the link (cookie flags and lifetime, counted once, case-insensitivity, local continue, never off-site, dead links identical but inert, last link wins); sign-up with the cookie (credited, cookie cleared, stale cookie fine) and via the API; the customer pages (offer, join, approval wait, closed programme, dashboard with **no customer identity**, history, isolation, login); the staff pages and permissions, overview, approve / reject / suspend, rule and code, commissions queue, recording a payout from the page (and refusing below the minimum), crediting a client from its profile, settings; the API for every endpoint and permission; the task, command and schedule.
- **Concurrency (2, PostgreSQL):** two simultaneous payouts for the same commissions pay once (verified: without the lock, both go through); the paid event arriving twice at once earns one commission.
- **Mutation checks:** eleven rules were broken in turn (tax in the base, first-invoice-only, the recurring window, suspended affiliates earning, the minimum payout, the payout lock, the refund-after-payout flag, the affiliate's own account, the hold period, cross-affiliate payouts, refund scaling) and each was caught.
- **Browser check:** headless Chromium at 1366, 768 and 375px: a customer applies (first without accepting the terms: refused); the manager sees the waiting application and approves it; an anonymous visitor follows an affiliate's link and signs up; the client's profile shows "Referred by ..."; the manager records a payout from the affiliate's page; the payouts and commissions pages update; the affiliate's dashboard, commissions and payout history show the link, balances and payout while **the customer's name and email appear nowhere**. No JavaScript errors, no overflow.

## PHASE 13 EXIT CRITERIA

| Criterion | State |
|---|---|
| Existing implementation inspected, no duplicates | ✅ |
| Affiliate accounts, referral links, attribution | ✅ |
| Commission rules (percentage, fixed) and records | ✅ frozen at payment |
| Pending, approved, paid, rejected; payout history; reports | ✅ |
| Authorization tested; full suite green on SQLite and real PostgreSQL | ✅ 1134 |
| Migrations, `makemigrations --check`, OpenAPI with no warnings | ✅ |
| Browser check (desktop, tablet, mobile) | ✅ |
| CI green on PostgreSQL + Redis | pending at time of writing |

## Known limitations / deferred

- **No fraud detection beyond the basics.** A person can register a second account through their own link (the portal cannot tell without an address or device fingerprint, which it deliberately does not keep); the hold period and the reject action are the defence, and staff see every referral.
- **Visits are a rough count** (bots and repeat browsers inflate it); conversion is shown as a guide only.
- **One level only:** no multi-tier commissions, no per-product commission rules (a rule applies to the whole invoice; renewal lines carry no product link to key on).
- **Commissions are in the store currency**; the platform has a single currency today, and a payout of mixed currencies is refused.
- **Payout details are free text** visible to the affiliate and to staff who manage affiliates; they are not encrypted (they are not credentials).
- **Payouts are records, not payments:** the portal never sends money, and there is no payout batch export or bank file (a Phase 14/15 candidate).
- **Coupon codes are not linked to affiliates** (a promo-code attribution route).
- **The client profile shows the referral but not per-client commission totals**; the affiliate's page has them.
- **Reports are the programme's own summary**, not date-ranged exports; the full reports module is Phase 14.
