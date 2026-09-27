# Phase 17 Gap Report: Reliability & Failure Handling (Slice 1)

**Date:** 2026-09-27
**Baseline:** Phases 01-16 (both slices) and D0-D8 complete, all local.

Phase 17's own scenario list - registrar unavailable, WHM unavailable, payment callback delayed, provisioning
failure, duplicate webhook, network timeout, worker failure - reads like a list of things a hosting business's
earlier phases would already have had to survive to reach Phase 14 (Reports) at all. Rule 1 inspection confirmed
that: most of it is already built, to a genuinely high standard, as part of the phases that shipped the features
themselves. This slice closes the concrete gaps that inspection found, and starts the automation-visibility work
Section 11.2 calls for. What remains is listed at the end.

## FOUND (already in place, built alongside earlier phases)

- **Idempotent, resumable order fulfilment** (`apps/orders/fulfilment.py`, Phase 09): paid -> processing ->
  provisioning -> active, with each order line recording what it became and whether it is done, so a retry after a
  failure only redoes what is still outstanding and can never create a second hosting account or domain for a
  line. The claim on an order (paid/failed -> processing) is made under a row lock, so two workers can never fulfil
  the same order at once.
- **A safety-net sweep for stuck orders** (`sweep_stuck_orders_task`, every 5 minutes): queues fulfilment for any
  order paid but never picked up (a broker outage after payment), and fails - so staff can retry - any order stuck
  mid-fulfilment for 30+ minutes because the worker that had it died. This is "worker failure" and "network timeout"
  from Phase 17's own scenario list, already handled.
- **A broker-outage-safe enqueue**: fulfilment is queued on the database transaction's own commit, and a broker
  failure at that moment is swallowed and logged rather than raised - the sweep above is the backstop, so a payment
  is never lost even if the queue is briefly unreachable.
- **Duplicate webhooks** (`apps/billing/payments.py:handle_webhook`, Phase 09): a verified event is recorded by the
  gateway's own event id *first*, inside the same transaction as applying it; a redelivery hits a unique-constraint
  `IntegrityError` and is reported back as "duplicate" without being applied twice. If applying an event fails
  unexpectedly, the whole transaction (including the event record) rolls back, so the gateway's own retry gets a
  clean second attempt - "payment callback delayed" and "duplicate webhook" both already handled.
- **WHM/registrar failures during provisioning**: `_fulfil_item` catches `ServiceError` and any other exception per
  line, records `FulfilmentStatus.FAILED` with the error, audits `order.item_failed`, and lets every other line on
  the order keep going - one broken line never stops the rest, and the order lands in `Failed` (retryable by staff)
  rather than a half-finished `Active`.
- **Email delivery retries** (`apps/notifications/tasks.py`, Phase 11): `autoretry_for=(Exception,)`,
  `retry_backoff=30` (capped at 30 minutes), `max_retries=5`, `dont_autoretry_for=(ImproperlyConfigured,)` (a
  misconfigured provider is a permanent failure, not a temporary one) - `Request -> Execute -> Failure -> Retry?`
  from Phase 17's own diagram, already implemented for the one job in the codebase that most needed it.
- **Nine Celery beat jobs already scheduled** (`config/settings/base.py`): renewal invoices, reminders, commission
  approval, lifecycle (grace/suspend/terminate), guest-cart purge, stuck-email retry, sensitive-email purge,
  ticket auto-close, and the order sweep above.

## MISSING (now implemented in this slice)

- **Automation status** (Section 11.2: "last run of each Celery beat job, failures") and a **job-queue status page**
  (Section 11.1's Utilities list) did not exist - the only way to know whether a scheduled job had ever run, or
  whether a worker was even listening, was to check the server directly.
  - `apps.core.models.TaskRun`: one row per Celery task name (not a growing log - `apps.audit` already records
    business events; this is telemetry about the jobs themselves), updated by `apps.core.celery_signals` connecting
    to Celery's own `task_prerun`/`task_postrun`/`task_failure` signals in one place, so no task anywhere has to
    remember to report itself - including every task built in earlier phases, and any built from here on.
  - `console:automation` (**Insights -> Automation**, `view_settings`): whether a worker answered a ping just now,
    every entry in `CELERY_BEAT_SCHEDULE` with its last start/finish/result (or "Never run yet."), and a table of
    recent on-demand jobs (order fulfilment, etc.) run outside that schedule.
  - The dashboard's own System Health widget gained a **Background jobs** check (round-trips the existing
    `apps.core.tasks.ping` task with a 3-second timeout) - "worker failure" now shows up where a Super Admin
    already looks for "is anything broken right now", not only on a dedicated page.
- **WHOIS lookup** (Section 11.1's Utilities list) did not exist. `apps.domains.whois`: a raw WHOIS query (RFC
  3912, no third-party library) - asks IANA's own WHOIS server which server is authoritative for the name's TLD,
  then asks that server about the domain. Not a registrar operation and needs no provider credentials, so it works
  today even though the real registrar adapter is still open (a pre-existing, unrelated deferred item). New page:
  **Operations -> Domains -> WHOIS lookup** (`domains_staff:whois`, `view_domains`).

## PARTIAL / DEFERRED to a later Phase 17 slice

- **Scheduled hosting usage sync**: `staff_hosting_sync_usage`/`staff_hosting_sync_status` exist as one-at-a-time
  staff actions; there is no Celery beat job calling them across every active account. Deferred because the
  roadmap's own note still applies - the WHM adapter has never been verified against a real server (a pre-existing
  deferred item), so a scheduled sync would mostly call an adapter no one has confirmed actually works.
- **Expiry of abandoned online payment attempts, and of unpaid orders that were never paid**: both stay open/pending
  indefinitely today. Not done in this slice - it changes what "abandoned" means for real customer orders and
  deserves its own pass (an order holding a domain name is a different question from a stale payment attempt).
- **Backup / hold-before-delete before automatic termination**: `run_lifecycle`'s auto-terminate branch calls
  `hosting_services.terminate_account` directly, exactly as the existing deferred item said - off by default, and
  deletes data immediately when turned on. Not changed in this slice: adding a hold step changes what "terminate"
  means for the accounts already relying on the current behaviour, so it is scoped as its own piece of work.
- **Attachment retention / cleanup**: still open: ticket attachments are kept indefinitely with their ticket.
- **Real registrar adapter and WHM adapter verification against a live server**: pre-existing, unrelated deferred
  items, both still open ("before launch").

## REUSE

| Reused | How |
|---|---|
| Celery's own `task_prerun`/`task_postrun`/`task_failure` signals | `TaskRun` is updated from these, not from changes to any individual task - the same mechanism covers a task written in any app, including ones from earlier phases, without touching their code. |
| `apps.core.tasks.ping` (already existed, unused until now) | Both the health widget's new check and the automation page's "is a worker listening" line round-trip this one task. |
| `apps.domains.models.domain_tld` | The WHOIS lookup's TLD extraction reuses the same helper `Domain.save()` already uses, and inherits its documented single-label-TLD limitation rather than adding a second one. |
| The status badge component (`{% status_badge %}`) | Both new pages use it for Running/Success/Failure exactly like every other status in the project - no new colours invented. |
| The System Health widget's existing check pattern (`_check(name, fn)`) | The new "Background jobs" check is one more entry in the same list, not a new mechanism. |

## VERIFIED

`apps/core/test_automation.py` (9 tests: a successful and a failing task both get recorded via the real Celery
signals - not a mock of them; the health widget's new check and its failure path; the automation page's permission
gate, its listing of every scheduled job including ones that have never run, a job's result appearing once it has
run, and on-demand jobs landing in the right table), `apps/domains/tests/test_whois.py` (11 tests: the two-step IANA
lookup, a bad/dotless name refused before any network call, an unknown TLD's clear error, a network failure turned
into `WhoisError` - both via an injected fake and via a real refused connection to `127.0.0.1:43` - case/trailing-dot
handling, and the staff page's permission gate and rendering). `apps/core/test_navigation.py` updated for the new
menu entry. Browser-checked (Chromium, 1440 and 390 wide): the Automation page showing a live "Running" worker, nine
scheduled jobs all correctly "Never run yet." on a fresh database, and the `ping` task's own run appearing in
"Other recent jobs" after triggering it - and the WHOIS page returning a **real** record for `example.com` from the
public WHOIS system (not mocked) - no overflow, no console errors at either width. Full suite passes.
