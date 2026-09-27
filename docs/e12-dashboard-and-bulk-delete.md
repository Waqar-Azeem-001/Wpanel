# E12: dashboard KPI rows, arranging the panels, and bulk delete

**Date:** 2026-09-27

Three requests from the same message: line up the KPI strip in even rows instead of however many happen to fit,
let a person arrange the panels below it to their own taste, and offer "with selected → delete" wherever a list can
already be deleted from one at a time.

## The KPI strip lines up in even rows

The strip used `flex: 1 1 150px`: each tile wrapped at a fixed 150px, so however many happened to fit a given
screen width made the row (10 then 4, for the 14 tiles a Super Admin sees, on a typical desktop width) - the two
rows never lined up with anything meaningful. The fix keeps the same flex-wrap approach (a grid leaves an
incomplete last row's trailing cells blank, which is exactly what Phase E11 fixed before this) but sizes each tile
as an even fraction of the row - 2 per row on a phone, 3, 4, then 7 per row from 1200px up - so a full strip always
wraps at a whole number of tiles, and 14 tiles line up as 7 and 7. A short last row still grows to fill the width
either way, so the no-blank-box fix from E11 still holds.

## Arranging the dashboard panels

Below the KPI strip (which stays fixed - it is one thing, not a stack of panels to reorder), every panel now has a
small up/down control in its own header. Moving a panel saves the order to that person's own account
(`User.dashboard_widget_order`, a plain list of widget keys) and nothing else changes for anyone else looking at
the same dashboard. A widget key that no longer exists, or that this person can no longer see, is simply dropped
from their saved order rather than carried along as a gap, so adding, removing or renaming a widget in the registry
never needs a migration or a data fix-up. The KPI strip itself is never in this list and never moves.

`apps/console/widgets.py`: `visible_widgets(user)` now returns the strip first, then panels in the person's saved
order (falling back to the registry's own order for anything unmentioned); `move_widget(user, key, direction)` does
the swap and save, and does nothing for an unknown key, an unknown direction, or a panel already at that edge.
`console:widget_move` (`apps/console/views.py`) is the POST-only endpoint the two buttons hit, returning to
wherever the request came from (checked the same way every other referer-based redirect in this project is, so a
hostile `Referer` cannot be used to send someone somewhere else).

## Bulk delete, wherever one-at-a-time delete already exists

The project already has a shared, well-tested bulk-action helper (`apps.core.bulk`) and pattern (`data-select-all`
checkbox in the header, `name="ids"` checkboxes per row, a "With selected" bar) used by invoices, quotes, clients,
the email log and the ticket queue. Three more lists that could already delete one record at a time, but had no
bulk form yet, now do:

- **Store categories** (`catalog_staff:category_bulk`) - a category still holding a product or add-on is left alone,
  same rule as deleting one.
- **Billable charges** (`billing_staff:billable_bulk`) - a charge already collected onto an invoice is left alone,
  same rule as deleting one.
- **Knowledgebase articles** (`support_staff:kb_article_bulk`).

Each reuses the exact service call the single-delete button already used, so nothing about *what* can be deleted
changed - only that it can now be done for several records in one action, with the same per-record failure
reporting every other bulk action in the project already has (a record that cannot be deleted is named in the
"could not be changed" message, the rest still go through).

### Deliberately not touched

- **Payment methods, email providers, registrars**: these are small, settings-style lists (a business typically has
  one or two of each) shown as cards, not table rows - the bulk-select pattern needs a table, and the practical
  benefit of ticking several cards at once is close to nil for a list this short. Single delete stays.
- **Domains, orders, hosting accounts, transactions, servers, renewals**: none of these support deleting a record
  at all today, by design - they are financial, provisioning or audit records (Phase 16's credential/audit
  principles apply here too). Adding a bulk-delete button to one of these would mean deciding, for the first time,
  that the underlying record should be hard-deletable at all - a data-integrity decision, not a UI convenience, so
  it was not made unilaterally here. If any of these should in fact be deletable (e.g. a domain entered by mistake,
  never registered), say which one and the same reusable pattern drops in quickly.

## VERIFIED

`apps/core/test_owner_tools3.py` (the KPI strip's CSS), `apps/core/test_console.py` (12 new tests: default order,
moving up/down and it persisting past a fresh request, the edges doing nothing, an unknown/not-visible key being
ignored, a stale saved key never resurfacing a panel someone has since lost the permission for, the controls needing
POST and staff, and the open-redirect check on the referer), `apps/core/test_lists.py` (7 new tests: each of the
three bulk deletes doing the right thing and reporting the rest, and each needing its list's own manage permission).
Browser-checked (Chromium, 1440 and 390 wide): the KPI strip at 7 and 7, moving a panel and reloading to confirm it
stuck, and the full categories bulk-delete flow (tick two, confirm, deleted, success message) - no overflow, no
console errors at either width. Full suite passes; migrated the local `db.sqlite3` for the one new field
(`dashboard_widget_order`).
