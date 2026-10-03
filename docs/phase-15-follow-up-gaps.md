# Phase 15 follow-up: four gaps found by a pre-Phase-18 documentation audit

**Date:** 2026-10-03
**Baseline:** Phase 17 (both slices) and D5 complete, all local.
**Why this exists:** before starting Phase 18 (Testing & Production Readiness), the owner asked for the project's
original v2 specification (`new_phase.md`) and every doc in `docs/` to be read end to end and cross-checked against
the current roadmap and the actual code - not just the roadmap's own running summary of itself. That audit found
four genuine, previously-untracked gaps (three of them traced back to Phase 15's own feature list, never closed)
and three stale rows in the roadmap's Open/Deferred table describing bugs that had actually been fixed two to three
weeks of project time ago and never marked Closed. This document covers the four real fixes; the stale rows were
corrected directly in the roadmap (Section 26) with no code change, each citing the current code that proves it.

## Audit findings that led here

Reading `new_phase.md` (the pristine v2 spec, predating every D/E-track addition) against the current state found:

- **Maintenance mode** - `new_phase.md`'s Phase 15 feature list and Rule 5.8 both name a maintenance-mode toggle
  "served by Nginx"; D0 flagged it `Missing (Phase 15/17)`; it was never built, and never even added to the
  Open/Deferred table, so it had been silently missing since D0 - three phases past its own target.
- **Reminder schedule is fixed** (Known Work, target "Phase 15") - Phase 15 went 🟢 without ever revisiting it, and
  the row was never flagged for re-triage the way the roadmap's own convention requires.
- **Terms-of-service acceptance at checkout** (Known Work, target "Phase 15") - same pattern: Phase 15 closed, item
  untouched, never re-triaged.
- **Web cart needs exactly one client; no client chooser** (Known Work, target "Phase 15 / D3") - same pattern.

Three further rows were corrected as stale documentation, not rebuilt (the code already does what they describe):
customers' cancellation-requests menu link (fixed D1/D2, confirmed today via `apps/core/navigation.py:228`), the
Server/WHM adapter item (fixed Phase 05, confirmed via that phase's own report), and the branded-error-pages
crawler row (each page already has its own direct test; D5 closed the one real gap in it, a missing 400 test).

## 1. Maintenance mode

**FOUND:** `templates/maintenance.html` (D1, dependency-free, tested) existed with nothing ever rendering it.
**MISSING:** a toggle, anything that showed the page, and any Nginx wiring at all.

- `BrandSettings.maintenance_mode` (new field) - kept on the brand settings row because it is *already* read on
  every single request (the context processor), so checking it costs nothing extra; reuses that model's existing
  cache-and-invalidate, audited-change machinery outright (`TEXT_FIELDS`, `save_settings`) rather than a new
  settings model for one flag.
- `apps.core.middleware.MaintenanceModeMiddleware` (last in `MIDDLEWARE`, so every other cross-cutting concern -
  security headers, CSRF, sessions - still applies to both the maintenance response and the ordinary one): shows
  `maintenance.html` (503) for any web page while it is on, except a signed-in staff member (so the switch is never
  locked behind itself), the sign-in page and the brand/static assets it needs to render styled, and the whole API
  (`/api/...`) - this is a customer-facing "closed for maintenance" notice for the web site (Phase 15, Admin
  Operations), not an infrastructure outage gate; a storefront integration or future mobile client has no reason to
  go down because staff flipped it on.
- **A second, lower-level layer for Nginx**, since the roadmap says the page should be "served by Nginx" even when
  Django itself cannot safely be reached (mid-deploy): `MAINTENANCE_FLAG_PATH` (blank by default; local dev has no
  Nginx in front) - saving the setting creates or removes a flag file there (`apps.branding.services
  ._sync_maintenance_flag_file`, best-effort, never blocks the actual toggle on a filesystem error).
  `deploy/nginx/wpanel.conf` checks for that file and serves a static copy of the same page
  (`deploy/nginx/maintenance.html`, generated from the Django template by a new command,
  `manage.py render_maintenance_page`, so the two never drift apart by hand) without proxying to Django at all.
  `docker-compose.yml` gained the shared `maintenance` volume. This half is config-only and cannot be
  live-verified in this environment, consistent with the project's own existing, separate "Docker Compose stack run
  end to end" limitation (no Docker on the dev machine) - the Django-level mechanism above is the real, tested
  feature; this is defence in depth for when the stack is eventually run for real.

**Redesigned the same day (owner request: "maintenance page sahi design karoo"):** the first version was a plain
grey box with two lines of text. Still fully self-contained (the D1 rule stands: no template logic beyond a single
`{# #}` comment, no external stylesheet/font/image/script - `apps/core/test_ui.py
:test_the_maintenance_page_depends_on_nothing` is unchanged), it is now a centred card with a soft shadow, an
inline-SVG gear icon in a tinted badge (no `<img>`, so no `src=`), a three-dot "working on it" animation, full
light/dark support via `prefers-color-scheme` (matching how every other page in the project already follows the
visitor's system theme), `prefers-reduced-motion` turning the animations off, and a `<meta http-equiv="refresh"
content="60">` so the page does what its own text asks the visitor to do. **A real bug caught while redesigning
it:** Django's `{# #}` comment tag cannot span more than one line - the first redesign draft wrote the explanatory
comment across two lines, which meant Django stopped recognising it as a comment at all and rendered its raw text
(braces included) at the top of the visible page. Caught by actually rendering the template (not just reading its
source, which is all the original dependency test checks) and comparing to the generated Nginx copy. Fixed by
keeping the comment on one line, and a new regression test added alongside the existing one:
`test_the_maintenance_pages_own_comment_actually_renders_away` renders the template for real and asserts no stray
`{#`/`#}` survives - the original test alone would not have caught this, since it only reads the template's source
text, never what it renders to.

## 2. Configurable reminder schedule

**FOUND:** `apps.billing.reminders` already sent one reminder per invoice per stage, never twice
(`InvoiceReminder`'s unique constraint) - only the four stage boundaries themselves (`-3, 1, 7, 14` days from the
due date) were a hardcoded tuple, matching the Known Work item exactly ("only on/off is configurable").

- `BillingSettings.reminder_schedule_days` (a plain comma-separated string, e.g. `-3,1,7,14` - a text field like the
  rest of that settings page, not a new editor, since staff already edit everything else on that page the same
  way), validated in the model's own `clean()` (whole numbers, no duplicates, at most 12, each within a year of the
  due date).
- `InvoiceReminder.kind` switches from four named stages (`due_soon`/`overdue_1`/`overdue_7`/`overdue_14`) to the
  day offset itself as text (`"-3"`, `"1"`, ...), since an arbitrary, staff-chosen list of days needs a naming
  scheme that works for any day, not four fixed names. A data migration
  (`billing/migrations/0009_rename_reminder_kinds_to_day_offsets.py`) renames any existing rows so the "never send
  the same stage twice" guarantee holds exactly as before for reminders already sent.
- The customer-visible wording is unaffected: still exactly two email events (`invoice.due_soon` for a negative
  offset, `invoice.overdue` otherwise), as before - only the number of days overdue in the text changes, same as a
  staff-added day like `21` would read naturally today.

## 3. Terms-of-service acceptance at checkout

**FOUND:** Policy pages already existed end to end (E8: `BrandSettings.policy_links`, shown in the footer, on
sign-in/register, and at checkout as passive text - "By placing an order you agree to our policies: ..."). What was
missing was *acceptance*: nothing required a customer to confirm it, and nothing recorded that they had.

- `CheckoutForm.agree_to_policies` (required only when at least one policy page is configured - nothing to agree to
  otherwise, so the checkbox is not even shown; `StaffCheckoutForm` removes it outright, since no one but the
  client can agree to the client's own policies, matching the existing `notify_client` precedent for "Add Order is
  not the customer's own checkout").
- `apps.orders.services.checkout()` refuses with `policies_not_agreed` when policies are configured, the actor is
  not staff, and the box was not ticked - checked in the service layer (not only the web form), so the same rule
  protects the API (`policies_agreed` on `CheckoutInSerializer`) and the guest-checkout path, not just one template.
- **Snapshotted, like the order's billing details are**: `Order.policies_agreed_at` / `Order.policies_snapshot`
  (`[{"title", "url"}, ...]`) record what was actually agreed to and when, so a later edit to the policy pages never
  rewrites what a given customer saw and accepted - the same reasoning the order's billing-details snapshot already
  uses for the client's address.
- `/api/v1/brand/` gained `policy_links` (it carried every other brand field already; this was the one still
  missing), so an API-only client can show the same thing the web checkout does before asking for agreement.

## 4. Web cart client chooser

**FOUND:** `apps.clients.services.single_contact_client(user)` already existed exactly for "the one client this web
user acts as" - and already returned `None` for both "zero clients" and "more than one," with the cart's own
`cart_unavailable.html` then showing one message for both: "Ordering is available to customer accounts... contact
support" - genuinely wrong advice for an agency contact who manages several real accounts and simply needs to pick
one, not get their account linked by support.

- `apps.clients.services.contact_clients(user)` (new) - every client the user is a contact of; `single_contact_client`
  now calls it internally rather than repeating the same base query (Rule 3).
- `apps.orders.views._cart_client(request)` - the existing single-client resolution, falling back to whichever
  client the person most recently chose this session (`request.session["cart_client_id"]`) when they are a contact
  of more than one; still `None`, unchanged, for zero or for "more than one, nothing chosen yet."
- A new page, `orders_customer:choose_client` (`/cart/choose-client/`): a plain list of the person's own accounts,
  shown by `_cart_unavailable_response()` instead of the old "contact support" page exactly when there is more than
  one (the zero-account case still shows the original page, unchanged) - reached from `/cart/`, `/checkout/`, and
  every add-to-cart action redirects there the same way. The `next` parameter is restricted to a same-host path
  (same rule the project's other `next` handling already uses), never an open redirect.
- **Not built, by design:** a search box over every client in the system (that is the existing staff "Add Order"
  picker, a different, staff-only tool for a different job - Rule 3 says reuse, not "use the biggest hammer
  available"); this chooser only ever lists the handful of accounts the signed-in person is themselves a contact
  of.

## REUSE

| Reused | How |
|---|---|
| `BrandSettings` / `apps.branding.services` (cache, audit, `save_settings`'s changed-fields tracking) | Maintenance mode's field, toggle and audit trail - no new settings model. |
| `apps.billing.models.BillingSettings`'s existing settings-page form/service pattern | `reminder_schedule_days` added the same way `renewal_invoice_days` already works. |
| The order's existing billing-details snapshot (`billing_snapshot()`) | `policies_snapshot`/`policies_agreed_at` are the same "freeze it at checkout time" shape, not a new mechanism. |
| `notify_client`'s staff-exemption precedent (Add Order) | `agree_to_policies` is exempted for a staff actor the same way, for the same reason. |
| `apps.clients.services.single_contact_client`'s existing query | `contact_clients()` factors it out instead of adding a second, parallel query. |
| The project's existing same-host `next` validation (sign-in, bulk actions) | The chooser's own `next` parameter. |
| `python manage.py route_inventory`'s "regenerate, do not hand-edit" convention | `render_maintenance_page` follows the same pattern for the Nginx copy of the maintenance page. |

## VERIFIED

32 new tests across `apps/branding/tests/test_branding.py`, `apps/core/test_maintenance.py` (new), `apps/core
/test_ui.py` (one new test for the redesigned page's own rendering, alongside the existing dependency test),
`apps/notifications/test_wiring_web_api.py`, `apps/billing/tests/test_web_documents.py`,
`apps/orders/tests/test_checkout.py`, `test_web.py`, `test_guest_shop.py` and `test_api.py` - full project suite
green (2201 tests, up from 2169 at D5).
`manage.py check` and `makemigrations --check` both clean. Crawler green (the new client-chooser page is correctly
allowlisted - only reachable by a customer who is a contact of more than one account, a state the shared crawl
world's single-client fixture owner does not model; covered instead by this slice's own web tests). Migrated the
real local database (`billing.0008`/`0009`, `branding.0007`, `orders.0004`) and regenerated `docs/route-inventory.md`
(the new `/cart/choose-client/` route).

**Browser-checked end to end on a copy of the real local database** (Chromium, 1280 wide): turned maintenance mode
on from Setup > Brand - an anonymous visitor got the real branded "We will be right back" page (503), the sign-in
page stayed reachable, a signed-in staff member saw the site completely normally; turned it back off and every page
returned to normal. The billing settings page showed the reminder-days field with its default `-3,1,7,14`. Added a
second real client to the database and made the existing owner contact of both - `/cart/` correctly offered the
chooser ("Beta Co" and "WHE", each with its contact email) instead of the old "contact support" message, and
choosing one carried through to the cart. No console errors, no overflow.

**The redesigned maintenance page itself** was additionally screenshotted standalone (Chromium, the actual rendered
output - not the source file, which is how the comment bug above was caught) at 480px and 1280px, in both light and
dark colour schemes: the card, icon and dot animation render correctly at both sizes, dark mode swaps to the dark
token set correctly, and the page the user actually sees carries none of the `{# #}` comment text that the first
draft briefly leaked.

## Known limitations

- The Nginx side of maintenance mode (the flag file, the static page copy) is config-only, unverifiable without
  running the Docker Compose stack - the existing, separate "Docker Compose stack run end to end" limitation
  applies here too.
- `agree_to_policies`'s checked state is not preserved across the guest checkout's own country-refresh request (an
  existing limitation of that refresh mechanism for any checkbox, not new to this feature) - a visitor who changes
  their country after ticking it simply re-ticks it before placing the order.
- The cart client chooser remembers a choice for the browser session only (not permanently) - an agency contact
  shopping for a different account next time chooses again, which is the same cost staff already pay every time
  through the "Add Order" client picker.
