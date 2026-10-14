# H1: Websites and Hosting Dashboard

**Source:** next-generation roadmap, H1. **Date:** 2026-10-05. Local only.

## Rule 1 inspection

The customer "My Services" page was a plain table (domain, product, next due, status) with a status-filter sidebar;
the service detail page showed username and disk/bandwidth as text. Plan, primary domain, status and usage were already
stored on `HostingAccount`; SSL state and PHP version are not stored anywhere (they belong to H6/H7 and come from a
provider). Nothing to rebuild: the new page reuses the existing view, filter sidebar, URL names (`hosting_customer:*`),
status badge and upgrade/ticket links.

## What was built

- `apps/hosting/dashboard.py`: `website_cards(user, accounts)`: per account the plan, status, due date, storage and
  bandwidth meters (unknown usage shows "Not available", never zero; no limit shows "unlimited"; amber from 80%, red
  from 95%, always with the figures as text), quick actions, and SSL and PHP asked of the hosting-control gateway
  **only when the account's provider declares those capabilities**. Today no provider does, so the card shows neither
  (a test proves they appear once a provider reports them).
- `templates/hosting/customer/list.html`: responsive card grid (1, 2, 3 columns), pending accounts show a setup note,
  actions: Manage (the existing service page for now), Upgrade (active only), Get help.
- `_meter.html` plus a small `.usage-meter` rule in `static/css/theme.css`: a plain `<progress>` element (accessible,
  no inline style, themed for light and dark).
- The view keeps its `accounts` and `sidebar` context, so the status filters and their tests are unchanged.

## Verified

Hosting, client-area, crawler and design suites pass (new tests: meter maths, card content, pending state, ownership
scoping, SSL and PHP appearing when a provider reports them). The project's rule against inline styles caught my first
version of the bar; fixed with the plain element.

## Not done / next

- Not yet looked at in a real browser at 375 / 768 / 1280 px (Rule: browser-test changed UI). Do this before marking
  H1 fully closed.
- "Manage" still opens the existing service page; the tabbed management workspace arrives with H2 onwards, each
  section appearing only when the account's capabilities include it.
- The side-menu label is still "My hosting"; renaming it to "Websites" (the roadmap's wording) touches navigation tests
  and is left for the owner to confirm.
- Next slice: **H2 File Manager**. It needs the Files capability on a provider: the manual adapter can get a
  safe, sandboxed local implementation for development; the real cPanel provider waits for H14.


## Completed after the roadmap check
The website card's more-actions menu now offers **quick actions** straight to the tools the account's server supports (File manager,
Email accounts, MySQL databases, DNS records, Backups), only for live accounts.
