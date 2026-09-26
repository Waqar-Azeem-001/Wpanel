# Phase E1 — Owner tools: passwords, deleting, currencies, email health, bulk delete

Status: implemented locally (owner request, 2026-09-26). Nothing is deployed. Business rules live in services; the pages only call them.

## 1. What was asked and what was built

| Request | Built |
|---|---|
| Super Admin can set / reset / change a client's password | **Users › person › Password**: *Send reset link* (anyone with `manage_users`, or `manage_clients` for a customer) and *Set password* (**Super Admin only**: checked against the password rules, the person is signed out everywhere, they are emailed that it changed — the email never contains the password — and the audit entry records who did it, never the password). A *Generate* button makes a strong password in the browser only. The client's **Contacts** tab has the same *Send reset link* and a *Set password* shortcut. A Super Admin's own password, or another Super Admin's, cannot be set this way. |
| Delete a client | **Client › Delete this client** (Super Admin only). The person types the client's email; only a client **with no history** can go (issued invoices, sent quotes, orders, payments, domains, hosting, tickets, cancellations, coupon uses block it, and the page lists what blocks it and offers *Close account* instead). Unissued drafts, unbilled items, carts and contact links go with it; sign-ins that belong only to that client are deleted too unless unticked. Audited as `client.deleted`. |
| Currency USD / PKR / SAR, changeable when sharing an invoice | `apps/core/currencies.py` is the one list. A client's default currency is a select. A **draft invoice or quote has its own Currency select** (top of the form, before the prices); the label above the price column follows it. Blank = the client's default. **Amounts are never converted**: the prices typed are in the chosen currency. Issued documents keep their currency. A value already stored outside the list (old data) stays valid while left unchanged. |
| Emails not sending | Diagnosed on the owner's real database (read-only): the provider (SMTP, port 587) works; two emails failed with a one-off `TimeoutError`, and **nothing ever tried them again** (no worker locally). See section 2. |
| Manage / track emails | Email log: bulk *Send again* and *Delete*, *Send all failed again*, *Clear old emails* (7+ days, never touches waiting ones), per-email *Delete*, a **timeline** (queued → attempt failed → handed to the mail server → opened → clicked), the Message-ID, and clear warnings when no provider is active or an email only reached the fallback. Statistics add click rate. |
| Bulk delete everywhere sensible | Email log; **Notifications inbox** (delete selected, clear read — own only); **draft invoices** and **draft quotes** (list bulk bar + a Delete draft button on a quote); **Clients** list (set active/inactive/closed for anyone with `manage_clients`, delete for a Super Admin, each still refusing a client with history); **Audit log** (below). Reports are computed live from other records, so there is nothing stored to delete there. |
| Audit log | Search and filters as before, plus **Export CSV** (the filtered log, newest 5,000, for anyone who may read it) and, for a **Super Admin only**, *Clear old entries* (90+ days, all areas or one). The log is still never edited; the clearing is itself recorded (`audit.purged`, who, how many, how old) in an entry that is kept. |

## 2. Why emails looked "not sent" and what changed

* With no worker (local development runs Celery inline) a failed attempt was recorded but never retried, and `TimeoutError: timed out` from a slow mail server left the email `failed` for good. In development a failing task also raised into the page that triggered it.
* Now: development no longer lets a failed email break the page (`CELERY_TASK_EAGER_PROPAGATES=False` in `dev.py`; the row keeps the error). A **sweeper task every 10 minutes** (`retry_stuck_emails_task`, Celery beat) tries again any email that is still queued or failed 10 minutes after its last change, up to 8 attempts, and skips emails whose secret was wiped. Without beat (local), press **Send all failed again**.
* **No provider active** is now loud: a banner on the email log and statistics, and each email records `delivery = fallback` ("did not leave this machine") instead of looking sent.
* Each attempt is logged (`EmailEvent`) with its error text, so "why" is visible per email.

## 3. Tracking

* **Opens**: unchanged (a 1×1 image; a signal, not proof). One timeline step on the first open.
* **Clicks** (new, `EMAIL_CLICK_TRACKING`, on by default): links in the HTML body of non-security emails go through `/e/c/<token>/<n>/`, which counts the click and redirects to the address stored for link *n* of that email. The address comes from our database, never from the request, so it cannot be an open redirect; an unknown or mismatched link goes to `/`. Security emails (reset links, verification, login details) are never tracked or rewritten. Switch off with `EMAIL_CLICK_TRACKING=False`.
* Not done: bounce tracking (needs the mail provider to report back) and per-recipient device information.

## 4. Permissions

| Action | Who |
|---|---|
| Set a password, delete a client, clear the audit log, bulk-delete clients | Super Admin only (enforced in the service, not only hidden in the page) |
| Send a reset link | `manage_users`, or `manage_clients` for a customer; never to an inactive account, yourself or a Super Admin |
| Email log: resend, delete, clear old, retry failed | `manage_settings` |
| Email log, statistics, audit export | `view_settings` / `view_audit_log` |
| Delete drafts | `manage_billing` |
| Delete notifications | the signed-in person, their own only |

## 5. Verified

`apps/core/test_owner_tools.py` (38 tests): every rule above including the direct-URL cases (an admin posting to the Super Admin endpoints changes nothing, anonymous is sent to sign in, a customer gets 403), tracking counts and redirect safety, retries and the sweeper's limits, and purge limits. Mutation checks: removing the Super Admin check, the history check, the currency check, the link ownership check, the minimum retention, the queued-email guard, the attempt cap, the sensitive-email exclusion and the own-notifications filter each make a test fail. Browser checks are listed in the phase report.

## 6. Limits

* Setting a client's currency does not convert existing invoices; a client with a currency outside the three keeps it until changed.
* Deleting a client with history is deliberately impossible; close it instead.
* Click tracking rewrites only `<a href="http…">` in the HTML part; the plain-text part is unchanged.
