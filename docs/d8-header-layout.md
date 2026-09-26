# Phase D8 — A different layout: one navy header, menus, hero band

Status: implemented locally (owner request, 2026-09-26: "the layout is still not liked; make everything a bit different and
modern"). UI only: no service, model, permission or API change. This replaces the left rail of D6/D7.

## 1. Why

After D7 the owner still disliked the layout. Looking at the screens again the honest problems were: two bars stacked at the top
(an empty top bar and a group strip), a sidebar that looked like every admin template, and inner pages (client profile, lists,
forms) that did not match the new shell (two wrapped rows of tabs, oversized headings, plain toolbars). The owner did not pick one
of three offered directions ("make everything different, modern"), so the direction below was chosen and built end to end.

## 2. The design

* **One header block on the brand navy** (deeper in dark), full width, sticky: brand mark and name, the menus, search, bell (and cart),
  account. Under it, in the same block, the pages of the area you are in. The brand's lime is the only highlight (active section
  bar, primary action, the account avatar, badges).
* **Menus, not a sidebar.** The registry's sections are the menus (People, Commerce, Operations, Insights, System); a menu opens a
  panel listing its areas and, under each, its pages (Commerce: Orders / Billing / Products, each with its pages in three
  columns). Areas with no section (Overview, and the customer's areas) are direct links or small dropdowns. Hover-to-switch once one
  is open, click to open, Esc or a click elsewhere closes, Tab moves into the panel. Everything is generated from
  `apps/core/navigation.py`; a person sees only what they may open.
* **Phones:** the same menus are a sheet (burger, Esc, backdrop, links close it), the search is an icon, customers also get the
  bottom bar. No overflow at 390px.
* **Hero band** at the top of both dashboards: a navy card with the greeting, the actions (primary in lime) and the KPI tiles on
  translucent glass. Below it the needs-attention list and the panels on the light canvas.
* **Every inner page** picks up the same look without editing its template: 14px cards with a hairline, page headers with a large
  title and a line of context, tabs as one scrollable pill track (no more two wrapped rows), toolbars as a panel, quieter tables
  with room at both ends, detail lists as labelled rows. Breadcrumbs are the first line of the page.
* **Tokens:** the header and hero use `--chrome-*` and `--hero-*` (tested for contrast in both themes); everything else is unchanged
  from D7, including Light / Dark / Auto in the account menu.

## 3. What was removed

The left rail and the top bar (`components/rail.html`, `topbar.html`), the collapse behaviour and its storage key, the rail
account card (the account menu is now in the header), and the "Collapse" control. The group strip stays (now in the header).

## 4. Verified

Automated: the whole suite on SQLite and PostgreSQL, the link crawler for nine roles (every page still reachable by links),
navigation tests rewritten for the header (sections as menus, areas and pages inside, one section lit, strips, only permitted
pages listed, brand, breadcrumbs), design tests (contrast of the header and hero tokens in both themes, no literal colours in
components, theme choice, focus and reduced motion).
Browser (real Chromium): menu open/close/switch/Escape/click-away/keyboard, palette, theme switch, sticky header, phone sheet and
dashboard; overflow and JS errors on the sweeps; text contrast, control names and focus on 30 pages in both themes.

## 5. Not changed / limits

Screen readers were not used. The hero text is measured through its tokens (its gradient is not a flat colour, so the
in-browser contrast script skips it). Deferred: a per-user choice between this header and a side rail; a menu description line
under each area; customising which KPI tiles show.
