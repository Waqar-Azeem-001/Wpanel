# Phase 11 Gap Report: Notifications & Email

**Date:** 2026-09-25
**Baseline:** Phases 01-10 complete (`98a9995`)

## FOUND

- **A working email pipeline from Phase 01:** `notifications.send_email` records an `EmailMessage` first, then a Celery task delivers it (retry-safe, idempotent, `sent` / `failed` / `queued`), through the email provider that is configured (and encrypted) in the admin, never in ENV. In production, with no provider configured, delivery fails loudly instead of printing to a console.
- **An in-app `Notification` model and API** (list, mark read) with a low-level `notify()` used by a dozen call sites.
- **Twenty-odd email templates** already existed, and every earlier phase called `notify()` / `send_email()` directly with a template name.
- **The events the roadmap lists** were already partly sent: registration, verification, orders, invoices, payments, tickets, renewals. Not sent at all: hosting suspension/termination/reactivation, domain registration, order cancellation, failed/rejected/refunded payments, overdue and due-soon reminders.

## PARTIAL / BROKEN

1. **No single place said what a "notification" is.** Each app decided for itself whether to email, whether to notify in-app, and which template. A typo in an event name or template silently sent nothing.
2. **Secrets were stored in the email log in plain text.** The hosting welcome email (cPanel username and temporary password), password-reset and verification links, and the welcome link were saved verbatim in `EmailMessage.body_text` and visible to anyone who could open the email log or the admin. **Security defect, fixed** (below).
3. **The staff were told about almost nothing.** A customer reporting an offline payment, a ticket arriving with no owner, a failed order, an accepted quote: all visible only if someone looked.
4. **A customer could not choose** what they were emailed about, and there was no way to see notifications in the portal.
5. **No open tracking, no statistics, no way to resend** a failed email or even to see failures without the database.
6. **Overdue reminders did not exist** (the roadmap's "overdue" event).
7. **Found while browser-testing:** issuing an invoice created **no in-app notification for the customer**, and the invoice email was filed against the *staff member who issued it* (and would have followed that person's preferences). Now the customer's owner and billing contacts get the in-app entry, the email goes once to the invoice's billing address, and the email is not attached to the staff member.

## MISSING (now implemented)

Everything on the roadmap's Phase 11 list: registration, verification, order, payment, invoice, overdue, hosting activation, domain registration, renewal, suspension, cancellation (of orders), ticket update, quote update, payment update; and email tracking: sent, failed, opened, open rate, with the caveat.

## REUSE

| Reused | How |
|---|---|
| `send_email` -> `EmailMessage` -> Celery `deliver_email` | Untouched in shape: everything still goes through it, so retries, idempotency and the DB-configured provider apply to every new event |
| `Notification` + `/api/v1/notifications/` | Extended (unread count), not replaced |
| `apps.core.crypto` (Fernet) | Protects secret email content until delivery |
| `audit.record`, `core.web.run_action`, `core.system.SYSTEM`, the `perm()` helper, `has_perm` | Audit trail, view plumbing, permission checks (`view_settings` / `manage_settings` gate the email log, as for the provider settings) |
| Celery beat, the sweeper pattern | Reminder and purge jobs |
| `notify()` | Kept as a thin legacy wrapper so nothing outside the migrated code breaks; new code uses `dispatch` |

## CHANGES

- **One registry of events** (`apps/notifications/events.py`). Each event has a key, a label, a category, an email template (or none: in-app only), and flags: *essential* (cannot be switched off), *sensitive* (carries a secret), and its defaults. `dispatch(event, ...)` is the only way business code communicates; an unknown event raises. A test fails if any code path sends an event that is not registered.
- **Every existing sender migrated** to `dispatch` / `dispatch_client` / `notify_team`: accounts, clients, hosting, domains, invoicing, payments, orders (service, fulfilment, lifecycle), renewals, support. No caller still names a template.
- **Categories and preferences.** A customer can turn email and in-app **off per category** (Orders, Services, Support; staff also see Team alerts) at *Notifications -> Preferences* or `/api/v1/notification-preferences/`. **Essential messages cannot be switched off** and the page lists them: account security, invoices and payments, order placed/cancelled, hosting login details, suspension and termination. Team alerts default to in-app only, so staff are not flooded with email.
- **Team alerts** to every staff member who holds the right permission: an offline payment reported, a ticket that arrives with nobody assigned (and who it was assigned to, when someone is), a customer reply on an assigned ticket, an accepted or declined quote, an order that failed to fulfil, and a persistently failing email. Nobody is told about their own action.
- **New customer emails:** hosting suspended / reactivated / terminated, domain registered, order cancelled, invoice due soon and overdue, payment failed / rejected / refunded. Each has a matching in-app entry and links back to the portal.
- **Secrets never sit in the log.** For a *sensitive* event, the readable body is replaced by a placeholder and the real content is stored **encrypted only until the email is delivered**, then wiped. An email that never gets delivered has its secret wiped after 24 hours (daily job). The email log, the API and the admin never show it, resend refuses when it has gone ("ask the customer to request a new one"), and the button is not offered.
- **Open tracking, honestly.** A 1x1 image (`/e/o/<random token>.gif`, `EMAIL_OPEN_TRACKING`) records the first open and the number of opens. It is **never added to security or secret emails**, an unknown token returns the same image (so it cannot be used to probe), responses are `no-store`, and every screen that shows an open rate states that it is *a signal, not proof of reading*: mail programs that block images hide opens, and privacy features and link scanners can register opens nobody made.
- **HTML alternative.** Every email now has an HTML part generated from the text (escaped, links made clickable) so mail programs render it properly; the text part is unchanged.
- **Failure alert.** After six failed attempts, admins get an in-app alert (at most one an hour) with a link to the log.
- **Payment reminders (dunning).** A daily job (08:00) sends one reminder for each open invoice with a balance: 3 days before it is due, then 1, 7 and 14 days after. Only the most advanced reminder that is due is sent, each kind at most once per invoice (unique constraint), so a second run is harmless and a stopped job never causes a burst. A switch in Billing settings turns it off. Also `manage.py send_invoice_reminders`. What happens to an invoice that stays unpaid (suspension) is Phase 12.
- **Screens.** Customer/anyone signed in: **Notifications** (bell with unread count in the navigation, all/unread, mark all read, opening a notification marks it read and follows its link, only local links are ever followed), **Preferences**. Staff (`view_settings`): **Email** overview (sent, failed, queued, open rate, by message type, 7/30/90 days, a failure banner), **Email log** (search, status, message type, detail with delivery data, and *Send again* for `manage_settings`).
- **API.** `/api/v1/notifications/unread-count/`, `/api/v1/notification-preferences/` (GET, PUT), `/api/v1/email-messages/` (list, filters, `search`, `stats`, `resend`; `view_settings`/`manage_settings`).
- **Migrations:** `notifications 0002` (tracking token backfilled for existing rows in three steps: nullable, fill, unique - **verified on PostgreSQL with 500 existing rows, 500 distinct tokens**; sensitive-content fields; `NotificationPreference`), `billing 0005` (`InvoiceReminder`, `BillingSettings.send_payment_reminders`).
- **Settings:** `EMAIL_OPEN_TRACKING` (default on) and two beat entries. `.env.example` documents the one new variable. No credential goes in ENV.

## Defects found and fixed while building it

1. **Secrets in the email log** (above): the most important finding of the phase.
2. **The invoice email and in-app entry:** the customer got no in-app entry and the email was attributed to the staff member who issued the invoice. Found in the browser check by an inbox that stayed empty.
3. **"Send again" was offered for an email whose secret had been removed**, then failed when clicked. The button now appears only when there is something to send, with an explanation otherwise.
4. **A migration that Django could not write itself:** a unique per-row token cannot be added by `makemigrations` (it asks a question that cannot be answered non-interactively). Hand-written in three steps and checked against real data.
5. **A DRF action attached to the class after it was defined** raised at import (`Expected function ... to match its attribute name`), the same mistake I had already rejected on the orders API. Made a proper method.
6. **An existing checkout test patched the old `notify`** and started failing; it now patches `dispatch`.
7. **My own test mistakes:** several assertions I first wrote as `... or True` (which assert nothing) were replaced with real ones, and a test of an inbox after opening notifications now expects the "caught up" state it should have.

## TEST PLAN

**60 new tests: 945 in total on PostgreSQL** (935 on SQLite; the 10 concurrency tests skip there). All pass against a local PostgreSQL 17.5 before pushing.

- **Registry, dispatch and preferences (24):** every event is well formed; no unregistered event can be sent; essential events ignore preferences; optional ones respect them; in-app and email channels are independent; the audience for team alerts (permission holders only, never the actor); **secrets** (encrypted at rest, wiped after delivery, wiped after 24 h if undelivered, absent from the log, admin and API, never open-tracked, resend refused once gone); open tracking (first/last open, count, unknown token, disabled setting, statistics and open rate over a period, *signal* caveat); HTML alternative escaping and links; the failure alert and its once-an-hour limit.
- **Wiring, reminders, web and API (36):** each business event reaches the right people and only them (a reported payment alerts billing staff and not the customer or agents; rejection, refund and gateway failure email the customer; an unowned ticket alerts the team, an owned one only the assignee; a customer can turn off ticket replies but never invoices; cancelled and failed orders; hosting suspend/reactivate/terminate; domain registration and renewal; quote decisions; an issued invoice reaches the customer once, in-app and by email, and not the issuer); **reminders** (which one is due for ten different distances from the due date, only the most advanced sent, once each and idempotent across runs, the next stage on time, not sent for paid, cancelled, draft or settled invoices, the balance not the total, the on/off switch, the task and the command); the inbox (own notifications only, another user's is 404, external links never followed, mark all read, navigation count, login required); the preferences page; the email log (permissions, filters, secrets shown as removed, resend, no second resend); the API (permissions, filters, stats, resend and its 400 on a delivered email, preferences and their validation, secrets never exposed); the tasks are scheduled.
- **Browser check:** headless Chromium at 1366, 768 and 375px: a customer sees the bell count, opens the inbox, marks all read, saves preferences (essential messages listed, team alerts absent), the open pixel is fetched and counted (`image/gif`, `no-store`; an unknown token still returns the image), and the customer is refused the staff pages (403); an admin sees the statistics with the open-rate caveat, the failed filter, a secret email whose content is shown as removed (verified: the password is nowhere in the page), a failed email that cannot be resent because its secret is gone, and one that can be resent. No JavaScript errors, no overflow.

## PHASE 11 EXIT CRITERIA

| Criterion | State |
|---|---|
| Existing implementation inspected, no duplicates | ✅ extended the Phase 01 pipeline; one registry, one `dispatch` |
| Registration, verification, order, payment, invoice, overdue, hosting activation, domain registration, renewal, suspension, cancellation, ticket, quote, payment update | ✅ each tested (cancellation = orders; service cancellation is Phase 12) |
| Sent / failed / opened / open rate, with the "signal, not proof" caveat | ✅ |
| Secrets not stored in the log | ✅ tested |
| Authorization tested; full suite green on SQLite and real PostgreSQL | ✅ 945 |
| Migrations (verified with existing data), `makemigrations --check`, OpenAPI with no warnings | ✅ |
| Browser check (desktop, tablet, mobile) | ✅ |
| CI green on PostgreSQL + Redis | pending at time of writing |

## Known limitations / deferred

- **No "dispute" object.** The roadmap's "dispute/payment update" is covered by payment received, failed, rejected and refunded; there is no chargeback/dispute record to notify about (a payment-gateway concern; see the open gateway decision).
- **Cancellation of a *service*** (as opposed to an order) is Phase 12; its emails will be added to the registry there.
- **Reply-by-email / email-to-ticket** remain out of scope: replies are made in the portal.
- **Only the SMTP provider kind** exists; other kinds (API-based senders) are not built. Open tracking is by image only; there is no click tracking and no bounce handling.
- **Templates are files, not editable in the admin.** Staff cannot yet customise wording; that is a Phase 15 (admin operations) candidate.
- **Reminder schedule is fixed** (3 days before, then 1, 7, 14 after); only on/off is configurable.
- **Emails are plain-text-first.** The generated HTML part is simple by design.
- **In-app notifications are not pushed live**; the count updates when a page loads.
