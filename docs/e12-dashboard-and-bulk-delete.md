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

## The same day, extended further on request: menus and every remaining list

Two menu items opened a page directly with nothing listed under them, unlike every sibling in their section
(`apps/core/navigation.py`): **Users** (People) now has **All users** underneath it, and **Hosting** (Operations) now
has **All hosting accounts** underneath it - both go to the exact page the item always opened, just reached the same
way as every other group in the mega-menu.

The bulk-delete rollout was then asked for on the remaining lists too. What each one actually got depends on what a
*hard delete* would mean for that kind of record:

- **Payment methods, email providers, registrars** (`billing_staff:payment_method_bulk`,
  `console:email_provider_bulk`, `console:registrar_bulk`): genuine bulk delete, same as categories/billables/
  articles. These are shown as cards, not table rows, so there is no `data-select-all` box - each is checked
  individually (the standard list is short enough, in practice, that this is not a loss) - using a checkbox and
  button that reference an external `<form>` by its `id` (the HTML `form="..."` attribute) rather than nesting one
  form inside another, since each card already holds its own small forms (the "Shown/Hidden" toggle, single delete).
- **Domains, orders, hosting accounts** (`domains_staff:bulk`, `orders_staff:bulk`, `hosting_staff:bulk`): a real
  hard delete would fail for nearly every row that is ticked - almost every domain is referenced by the order item
  that bought it, almost every order has an invoice, almost every hosting account is referenced by the order item
  that created it, and Django's own `on_delete=PROTECT` on each of those relationships refuses the delete outright.
  Rather than ship a "Delete" button that mostly just reports failures, the bulk action is the verb that already
  works regardless of that history and already means "get rid of this" for each of these: **cancel** for a domain
  still pending or failed, **cancel** for an order still awaiting payment (or failed/fraud, for staff), **terminate**
  for an account that is active or suspended - the exact same rule, and for hosting the exact same real call to the
  server, as doing it one at a time already had.
- **Servers** (`catalog_staff:server_bulk`, plus a single-item `server_delete` that did not exist before): a new,
  genuine delete - safe because `HostingAccount.server` is `PROTECT`, so a server still hosting an account simply
  refuses (`apps/products/services.py:delete_server`), the same shape as a category still holding a product.

### Still deliberately not touched: transactions and renewals

- **Transactions** are the record of money actually received (or refunded). `Invoice.amount_paid`/`amount_refunded`
  are *derived from* the succeeded transactions against it - deleting one would not be refused by anything (no
  `PROTECT` sits in front of it), it would silently leave an invoice's own totals wrong and remove the one audit
  trail a business has for "this payment happened". This is a different kind of risk from everything above - the
  others fail loudly and safely if attempted on the wrong record; this one would not fail at all, it would corrupt
  bookkeeping quietly. Not added without asking specifically about this one.
- **Renewals** (`ServiceChange`) is the same kind of record as an order or a transaction, not a different case: each
  row is "tied to the invoice that pays for it" (its own docstring) via `on_delete=PROTECT`, and its frozen figures
  are what a later billing audit re-derives a renewal's proration from (`apps/renewals/services.py`). There is not
  even an existing single-item "cancel" for a person to click today, only an internal `void_changes(invoice)` the
  system calls when the invoice itself is cancelled - so this is not "a bulk-delete pattern that would mostly fail
  and cancel is the real verb" the way domains/orders/hosting were; it is squarely the same financial-audit-record
  case as transactions, and held back for the same reason and the same explicit ask.

## VERIFIED

`apps/core/test_owner_tools3.py` (the KPI strip's CSS), `apps/core/test_console.py` (12 tests for arranging panels:
default order, moving up/down and it persisting past a fresh request, the edges doing nothing, an unknown/not-visible
key being ignored, a stale saved key never resurfacing a panel someone has since lost the permission for, the
controls needing POST and staff, and the open-redirect check on the referer), `apps/core/test_lists.py` (21 tests
across every bulk action added: categories/billables/articles/servers/payment-methods/email-providers/registrars
deleting the right rows and reporting the rest, domains/orders/hosting cancelling or terminating only what the single
-item rule already allowed, and every one of them needing its own manage permission), `apps/core/test_setup.py`
(the menu's "All users"/"All hosting accounts" labels). Browser-checked (Chromium, 1440 and 390 wide): the KPI strip
at 7 and 7, moving a panel and reloading to confirm it stuck, the People and Operations mega-menus showing the new
sub-items, the categories and payment-methods bulk-delete flows end to end (tick, confirm, deleted, success message)
- no overflow, no console errors at either width. Full suite passes; migrated the local `db.sqlite3` for the one new
field (`dashboard_widget_order`) - nothing else in this document needed a schema change.
