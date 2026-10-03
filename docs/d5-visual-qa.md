# Phase D5 Report: Visual QA & Polish

**Date:** 2026-10-03
**Baseline:** Phase 17 complete (`844ef9d`)
**Scope rule (v2 Section 08):** presentation-only - responsive behaviour, accessibility, empty/error states, print/PDF,
screenshots. No service, model, URL name or API contract was touched.

## Rule 1 first: most of this phase was already built

D5's own scope list overlaps heavily with work already done and verified in earlier phases: D7 (contrast, focus
states, dark/light tokens), D8 (4 sweeps x 212 pages x 6 roles x 3-4 widths, phone-menu keyboard handling), D1
(branded 403/404/400/500/maintenance pages) and E14 (the brand-tinted PDF redesign). Rule 1 inspection (reading
templates, CSS and tests, not re-running browser sweeps that already passed) found four small, concrete gaps still
open and confirmed everything else already closed - see the table below.

| Item | Status | Citation |
|---|---|---|
| Responsive elsewhere in the app | FOUND - no other table has this dense a row-actions cell | `static/css/shell.css:292-311` |
| Skip-to-content link, `lang`, alt-text policy, label association, landmarks, keyboard-operable menus | FOUND, already in place | `templates/layouts/_root.html`, `templates/components/topnav.html`, `static/js/shell.js:53-57` |
| Branded 403/404/500/maintenance pages | FOUND, already in place | `templates/403.html`, `404.html`, `500.html`, `maintenance.html` |
| List-view empty states (orders, tickets, domains, hosting, invoices, clients) | FOUND, already in place | every `{% empty %}` block already carries a real message |
| PDF as the sole, intentional "print" artifact for invoices/quotes | FOUND, already redesigned | `apps/billing/pdf.py` (E14) |
| Translation wrapping fraction (52% of templates) | FOUND, already correctly scoped as ongoing/Post-MVP | roadmap line 1001 |
| **Staff pricing table row-actions cramped at 375px** | **CLOSED this phase** | `templates/products/staff/product_detail.html` |
| **400 page had no dedicated regression test** (404/403/500/maintenance did) | **CLOSED this phase** | `apps/core/test_ui.py` |
| **Three secondary admin tables had no empty-state row** | **CLOSED this phase** | `clients/staff/tab_contacts.html`, `support/staff/departments.html`, `support/staff/kb.html` |
| **Invoice/quote action buttons would print along with the document** | **CLOSED this phase** | `templates/billing/{staff,customer}/{invoice,quote}_detail.html` |

## What was closed

1. **Pricing table @375px.** The table already scrolled sideways inside its own card rather than overflowing the
   page (the roadmap's own "no page overflow" note was accurate), but the two action buttons (Enable/Disable,
   Remove) were cramped into a narrow horizontally-scrolled cell. Reused the existing `.table-stack` component
   (already built for and used by `console/users.html` and `console/user_detail.html`, Rule 3) instead of inventing
   a new pattern: the table becomes a column of labelled rows below 575.98px, so every cell - including the actions
   cell - gets the table's full width. Browser-verified at 375px: no overflow, both buttons sit comfortably on their
   own row.

2. **A 400-page regression test.** 404/403/500/maintenance each already had one; 400 did not. Added
   `test_the_400_page_is_branded_and_offers_a_way_back`, triggered the same way Django itself triggers a real 400
   (`DisallowedHost` from an unrecognised `Host` header, via `client.get("/", SERVER_NAME=...)`), mirroring the
   existing 404 test's shape exactly.

3. **Three secondary empty-state gaps.** A client's contacts tab, the support departments admin table and the
   knowledgebase categories list rendered a silent empty table when there were no rows, unlike every other list in
   the app. Added the same `{% empty %}` row already used everywhere else. Low-risk, cheap, and these tables are
   realistically never empty in production (a client always has an owner contact; departments/categories are
   seeded during setup) - closed anyway since it was in scope and cost nothing.

4. **Print stylesheet for document pages.** `shell.css` already hides the chrome (`.chrome`, `.tabbar`) when
   printing, and `theme.css` already defines a generic `.no-print { display: none !important; }` rule - but no
   template used it, so printing an invoice or quote detail page would have carried its Edit/Issue/Delete/Cancel/
   Email buttons and the "Record a payment" form onto the page. Tagged those action clusters with `no-print` on
   both the staff and customer invoice/quote detail templates (four files). No new CSS needed - the rule already
   existed and simply had nothing to apply to.

## Not touched (correctly out of scope)

- `templates/layouts/document.html`, a print-first shell built in an earlier phase, is unused - D3 re-skinned
  invoice/quote pages onto the normal chrome shell instead (`docs/d3-client-area.md`). Informational only; wiring
  it up would be a presentation change nobody asked for, not a gap.
- Translation wrapping of the remaining staff-admin templates - already a deliberate "wrap as touched" ongoing
  policy, not a D5-specific task (roadmap's RTL/translation item is Post-MVP).

## Tests

**+2 tests**, all green: `test_the_400_page_is_branded_and_offers_a_way_back` (`apps/core/test_ui.py`) and
`test_interactive_controls_are_hidden_from_print` (`apps/billing/tests/test_web_documents.py`, checks `no-print`
appears on the staff draft invoice, staff unpaid invoice, customer payable invoice, staff draft quote and customer
acceptable quote pages). Full `apps/billing apps/products apps/clients apps/support apps/core` suite green, no
regressions. `manage.py check` clean.

**Browser (real Chromium):** 375px screenshot of the product pricing table confirms the stacked layout (labelled
rows, no overflow, `table-stack` media query active, `<thead>` hidden as expected); a real invoice detail page was
screenshotted once on-screen and once under `emulate_media(media="print")` - the second screenshot shows only the
document (lines, totals, bill-to, payments table), with the header, "Record a payment" card and action buttons
gone.

## D5 exit criteria

| Criterion | State |
|---|---|
| Rule 1 inspection across responsive/a11y/empty-error/print-PDF, citing existing work rather than re-doing it | ✅ |
| Four concrete gaps found, closed, tested | ✅ |
| No service, model, URL name or API contract changed | ✅ |
| Full relevant test suite green | ✅ |
| Browser-verified (375px stacking, print emulation) | ✅ |

## Known limitations (unchanged, not this phase's job)

- Screen readers were not used (same limitation already recorded in D7/D8).
- Antivirus scanning of ticket attachments, multi-part TLDs, and other long-standing deferred items are untouched -
  out of D5's presentation-only scope.
