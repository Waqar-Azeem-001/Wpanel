# Phase D7 — Visual system, navigation and dashboards (second pass)

Status: implemented locally (owner request, 2026-09-26, after D6). Not deployed. UI, navigation and presentation only: no
model, service, permission, API or billing change.

## 1. Audit (what D6 left)

| Area | Finding | Decision |
|---|---|---|
| Tokens | One light set, one dark set, but status colours were bare text colours, dark mode had no elevated surface, brand-as-text (links, active menu items) measured 4.1–4.4:1 on white and on its tint, Bootstrap `.text-danger` etc. bypassed the tokens (3.95:1 in dark). | Rebuilt the token block (semantic names, tinted status backgrounds, `--elevated`, `--accent-ink`), mapped Bootstrap utilities to it, and tested contrast for both themes. |
| Rail | Dark navy for staff, light for customers (two designs), no workspace context, sections did not fold, profile lived only in the top bar, no way to choose light or dark. | One rail for everyone in the theme's own surface colour, workspace block, search field, foldable sections (remembered), account card with an appearance switch at the foot. |
| Dashboard | Eight identical bordered cards, each a stack of numbers, no order of importance; customer dashboard kept the left "Your info / Contacts / Shortcuts" column that reads like a billing-panel client area. | KPI strip, a "Needs attention" list, panels with different treatments (metrics, rows, feed), customer side panels on the right. |
| Tables, badges, forms | 16px radii and soft shadows everywhere, 15px base text, badges without a dot, big paddings. | 12px panels, 8px controls, 14.4px text, hairline borders, badges with a dot, comfortable 44px rows, tabular numbers. |
| States | "Loading…" text, blank-ish empty lists, no error state for a dashboard panel. | Skeleton loaders, explanatory empty states, an error state with "Try again". |
| Page headers | Each page invented its own header; no description line. | One header (title, one line of context, actions) restyled centrally, descriptions added to the 26 main pages. |

Already good and reused: the menu registry and its permission gating, the crawler, the `status_badge`/`money`/`fdate` vocabulary,
the modal confirmation, the auth split screen from D6, the Users area, Plus Jakarta Sans and the brand colours (still a
database record).

## 2. Design language

* **Type:** Plus Jakarta Sans at 14.4px, headings 650, tabular numbers wherever figures are compared.
* **Surfaces (light):** page `#f5f6fa`, panel white, quiet grey `#eff1f6`, hairline `#e2e6ee`. **Dark:** `#0a0e19` page, `#111726`
  panel, `#182033` raised, `#232d45` hairline. Elevation is a hairline first; only menus and modals float (shadow ≤ 60px).
* **Colour:** the brand primary is the one accent. Status colours are semantic tokens with their own tint (`--ok/--warn/--danger/--info`
  and `-bg`), each measured against its own tint in both themes. Restrained: no gradients except the sign-in panel, no glow.
* **Brand as text:** `--accent-ink` (the brand colour darkened 15%) for links and active menu items in light; the lightened
  accent in dark. Buttons keep the brand colour with white (light) or dark (dark) text.
* **Motion:** 120ms colour/background transitions, a 150ms chevron turn, a 180ms rail width change; all disabled by
  `prefers-reduced-motion`.

Token names (both themes redefine only values): `--bg --surface --surface-2 --elevated --text --muted --border --border-strong
--primary --secondary --success --warning --danger --info --ok-bg --warn-bg --danger-bg --info-bg --accent --accent-ink
--accent-subtle`.

## 3. Navigation (simplified after the owner's review of the first rail)

The first D7 rail had expanders everywhere (a chevron on every section and on every group, a nested list with a guide line, a
pasted-looking logo plate) and felt awkward. It was replaced the same day:

* **The rail is one flat list.** Brand (the favicon as a small mark plus the site name, and "Staff console" or "Client area"),
  a search field, quiet section headings (People, Commerce, Operations, Insights, System), one line per area, the account card at
  the foot. Nothing folds and nothing expands. A group's entry opens its first page. Only one entry is lit: the area you are in.
* **The pages of an area are a strip under the top bar** (`components/subnav.html`, class `groupnav`), generated from the same
  registry: Billing shows Overview, Invoices, Transactions, Quotes, Billable items, Renewals & upgrades, Coupons; the current page is
  underlined, an invoice keeps "Invoices" lit, and an area with a single page shows no strip. It replaced the five hand-written tab
  bars (billing, support, lifecycle, affiliates, notifications) and the duplicate link panels on the customer list pages, so a page is
  reachable exactly one way.
* **Search or jump to** (rail field, Ctrl/⌘ K, or the icon on phones) lists every page the person may open (sub-pages included) from a
  server-rendered list, so the flat rail loses nothing.
* Collapsed rail keeps icons, tooltips and dividers and is remembered; the account card opens the menu with Light / Dark / Auto;
  the phone drawer is the same flat list. Affiliates became a group (Overview, Affiliates, Commissions, Payouts) so its pages have a strip.

Role-specific menus are unchanged from D6 and still come from the registry: each role sees only what it may open, and each hidden
page is requested directly in the tests and refused.

## 4. Dashboards

**Staff overview** (`console:dashboard`): greeting by time of day, one line of context, the actions the person may take (the first is
the primary button), then

1. **KPI strip** (`summary` widget): Active clients, Active services, Pending orders, Unpaid invoices, Income this month, Open tickets,
   Failed orders. Each tile is shown only to people who may open its list, and its number equals the rows behind its link
   (tested). Money tiles are sums; their counts are in the sub-line.
2. **Needs attention** (`attention` widget): only conditions that exist right now, worst first: provisioning failed, overdue invoices,
   payments to confirm, urgent and unassigned tickets, cancellation requests, and health problems (no active email provider,
   registrar or server). Each row says what and why and links to the list; an empty list says "All clear".
3. Panels with their own shape: Orders (counts + latest), Money, Support queue, Provisioning failures, Domains expiring,
   Services and renewals, System health (status per check), Recent activity (a feed with plain-language actions).

Technical Staff see the same dashboard with only their tiles, rows and panels (no money, no reports).
No number is invented: everything is read from the same queries the lists use.

**Customer overview:** KPI strip (services, domains, quotes, tickets, invoices), the overdue banner, services and tickets tables,
domain search, and the account panels (info, contacts, shortcuts, affiliate) in a right-hand column.

## 5. States

* Loading: skeleton lines in each panel and in the KPI strip.
* Error: a panel that cannot load says so and offers "Try again" (re-requests only that panel). Nothing else on the page is blocked.
* Empty: every dashboard panel and the Users list says what would appear and, where there is one, what to do.
* Buttons can take `.is-loading` (spinner, no double submit).

## 6. Users

Segmented role filter with counts, search and status filter in one toolbar, table with avatar/role pill/status badge/last
sign-in; below 576px each row becomes a labelled stack. Detail: account and security, client accounts (orders, services,
domains, unpaid invoices, open tickets), activity feed, and only the actions the viewer may use (unchanged rules).

## 7. Accessibility

Tested in a real browser in both themes on 30 pages: text contrast as rendered (WCAG AA: 4.5:1, 3:1 for large text), a name for every
field, button and link, exactly one `h1` and one `main`, and a visible focus ring on the first 14 tab stops of the dashboard and sign-in.
Token contrast is also a unit test (`apps/core/test_design.py`). Keyboard: skip link, rail buttons and links, Esc closes the drawer,
Ctrl/⌘ K opens search, Enter opens the first match.

## 8. Not changed

Business logic, permissions, models, migrations, API contracts, provider handling, billing maths. The storefront (public top
bar, hero, plans) picks up the new tokens but keeps its layout. The in-page tab bars of the billing and support modules remain.

## 9. Deferred

Charts (there is no time-series data worth drawing yet; the KPI strip and lists carry the meaning); a domains-expiring KPI
(needs an "expiring" filter on the domains list so the number equals its list); moving the per-page "Search" forms to the shared
toolbar component; a full-page dark-mode visual regression suite (Phase D5).
