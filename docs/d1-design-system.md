# Phase D1 Report: Design System & Layout Shell

**Date:** 2026-09-25
**Baseline:** Phase D0 complete (`573ec2c`)
**Scope rule (v2 Section 08):** presentation only. **No URL name, service, model behaviour or API contract was changed** (the one new model, `BrandSettings`, is new configuration, and the one new API, `/api/v1/brand/`, is additive).

## What was found (D0) and what D1 did about it

| D0 finding | D1 result |
|---|---|
| One layout for every page; hand-written menu of up to 17 links | **Three shells** (public, client, staff) plus a document shell and an email shell, all built on one root skeleton. Every existing page moved into the right shell **without being edited**: `base.html` is now a two-line alias (`{% extends layout_template %}`) and a context processor chooses the shell per request |
| No framework, one 172-line stylesheet, no tokens, no focus styles | **Bootstrap 5.3.3 + Bootstrap Icons 1.11.3, vendored** with their licences, our design tokens on top (`theme.css`), a compatibility layer (`legacy.css`) that restates the old class names on those tokens, visible focus rings, automatic dark mode |
| htmx loaded from a CDN and unused | **htmx 2.0.4 vendored**; no page loads anything from the internet (tested) |
| 53 hand-coloured status badges | **One `{% status_badge %}` tag** with the roadmap's colour map; all 53 templates converted by script (nothing skipped) |
| Bare Django error pages | **Branded 403, 404 and 400** inside the shell, a **standalone 500** (no request, context or database), and a dependency-free **maintenance page** |
| Brand not configurable | **Brand settings in the database** (name, logo, favicon, colours, support email, footer, date and money formats), edited in Setup, used by pages, emails and the mobile API |
| Ten unlabelled form fields | Fixed (labels added; a test now guards the pages) |
| No component library | Built (below) |

## The shells

| Shell | Used for | Structure |
|---|---|---|
| `layouts/public.html` | Store, domain search, knowledgebase, sign-in, registration, anyone not signed in | Brand-coloured top bar (Plans, Domains, Help, Sign in, Create account), content, footer |
| `layouts/client.html` | Signed-in customers | Top bar with **Services / Domains / Billing / Support menus**, Open Ticket, Affiliates, Cart, notification bell and an account menu (profile, account, **cancellation requests**, change password, notification preferences, sign out) |
| `layouts/staff.html` | Signed-in staff | Dark top bar with **grouped menus** (Clients, Orders, Billing, Support, Reports, Utilities), a **Setup** (gear) menu (products, addons, domain pricing, servers, payment methods, tax, coupons, billing settings, departments, lifecycle timings, affiliate settings, brand), the bell and the account menu; every entry is shown only if the person may open it |
| `layouts/document.html` | Printable documents (invoice, quote) | No navigation, print-first (adopted by the document screens in D3) |
| `layouts/email.html` | HTML emails | Table-based with inline styles, brand header (colour, logo), footer; every email now uses it |

All shells share: skip-to-content link, one `<main id="main">`, messages in one place, breadcrumbs (rendered when a page supplies them), footer from the brand settings, the confirmation modal, and the scripts. The navigation is still hand-written markup in three component files; **D2 replaces it with the menu registry**.

## The components (Section 9.4)

Navbar (three variants) · status badge tag · stat tile (whole tile is a link) · page header · URL-based tabs · empty state (message plus one action) · pager (moved to `components/`, 19 pages updated) · form include (Bootstrap classes, required markers, invalid state, error text linked with `aria-describedby`, checkbox layout) · confirmation modal (any form or button with `data-confirm` asks first) · messages (mapped to Bootstrap alerts, announced to screen readers) · breadcrumbs · timeline/activity feed · footer · loading indicator (htmx) · formatters (`money`, `fdate`, `fdatetime`, driven by the brand settings, in the site's time zone) · document and email shells.

## Brand settings (Rule 6: brand is configuration)

- **Editable** at *Setup -> Brand* (`view_settings` to look, `manage_settings` to change) and through `GET`/`PATCH /api/v1/brand/` (GET is public so a mobile app can theme itself before sign-in).
- **Colours are checked for readability:** buttons put white text on the primary colour, so a colour with less than **4.5:1 contrast** against white (WCAG AA) is refused, with the ratio in the message. The darker hover shade, the tint and the lighter dark-mode shade are derived from it.
- **Logo and favicon are validated by content:** PNG, JPEG, GIF, WebP (ICO for the favicon), size- and dimension-limited, and **never SVG** (an SVG can carry script). They live in the database and are served from named routes (`/brand/logo/`, `/brand/favicon/`) with `nosniff`, a sandboxing Content-Security-Policy and ETag caching, so there is no filesystem or web-server dependency.
- The colours reach the stylesheet only through `/brand.css` (a tiny cacheable route), so there is no inline style and a future Content-Security-Policy stays simple. The settings are cached and the cache is cleared on every change; if the cache or database fails, pages fall back to the defaults instead of erroring.
- Emails take the name, colour, logo, footer and support address from the same place.

## Accessibility and other standards

- Visible keyboard focus everywhere; skip link is the first tab stop; menus open with the keyboard; status is never colour alone (text label always shown); every form control now has a label; automatic light/dark following the visitor's setting.
- **Internationalisation:** every string in the shells and components is wrapped for translation; the 118 existing page templates will be wrapped as D3/D4 re-skin them (recorded below).

## Defects found while doing it

1. **Dark-mode links were unreadable** (dark blue on near-black): Bootstrap takes link colour from an RGB token I had pointed at the dark brand shade. Found by looking at the screenshots, not by a check; fixed with a lightened brand token.
2. **The mobile menu button icon was invisible** (I inverted an icon Bootstrap's dark bar already draws in white). Found in the same review; fixed.
3. **Required checkboxes had no required marker.** Found by a test; fixed in the form include.
4. **A weak test:** my first 403-page test accepted the navigation bar's brand link as "a way back", so deleting the page's own button went unnoticed. Found by a mutation check; the test now asserts the button.
5. My own recurring shell issue: backslash escapes are mangled by the shell tool, which broke a Python edit (caught immediately by the syntax error and redone with the editor).

## Tests

**+73 tests (branding 38, design system 35): 1293 on PostgreSQL (1279 on SQLite; the 14 concurrency tests skip there).** All existing tests still pass unchanged apart from moved markup.

- **Branding:** defaults; derived colours; a broken database never breaks a page; cache and invalidation; audit; permissions; hex validation; **contrast refusal**; every accepted image type recognised by content; SVG, executables, HTML, fake PNGs, oversized files and dimensions all refused, and a bad upload saves nothing; serving headers and ETag; the token stylesheet; the staff page including a real upload and its error messages; the public API and its permissions; the brand on every page and in emails.
- **Design system:** every status in the roadmap map has its colour, unknown statuses are neutral, labels are escaped; money and dates follow the brand format and the site's time zone; form fields, required markers, invalid state and hidden fields; each kind of visitor gets their shell; **every link in each role's navigation opens (customer, agent, manager, admin, superuser, anonymous), which is Rule 5.6 as a test**; the menus hide what a role may not open; the account menu, sign-out form and bell; branded 404 and 403, error pages with a broken brand, the standalone 500 (zero database queries), the dependency-free maintenance page; no page loads anything from the internet; every static file a template names exists; the vendored assets and licences are present; the old stylesheet is gone; the document and email shells; and **no unlabelled controls** on the five pages that had them.
- **Mutation checks:** nine rules were broken in turn (contrast not enforced, image dimensions, brand cache, a status colour, uncoloured unknowns, a menu entry ignoring its permission, staff in the client shell, dropped required marker, a 403 page without its way back) and each was caught (one only after strengthening its test).
- **Browser check** (headless Chromium, three roles x 375 / 768 / 1280 px, plus dark mode, keyboard and menus): **zero findings** across every sampled page (no overflow, JavaScript errors, failed requests, unlabelled fields or external assets; one `<h1>` per page; dark preference applied; skip link first and visible; visible focus indicator; first menu opens; mobile menu button opens the navigation). Screenshots were reviewed by eye, which is how defects 1 and 2 were found.

## D1 exit criteria

| Criterion | State |
|---|---|
| Existing pages render inside the new shells | ✅ all of them, unedited (alias base) |
| Shells, components, tokens, status badge tag, formatters, brand settings, error and maintenance pages, focus styles | ✅ |
| No runtime CDN; assets vendored with licences | ✅ tested |
| Menu entries appear only if permitted and all open | ✅ tested for six visitor kinds |
| Whole suite and the link-integrity tests green; browser check at 375 / 768 / 1280 px | ✅ |
| CI green | see tracker |

## Known limitations (all scheduled)

- **`legacy.css` is a bridge.** The 118 page templates still use their old class names (`.card`, `.field`, `.stats` and so on); D3 (customer) and D4 (staff) re-skin each screen onto the Bootstrap components and delete the rules they stop using. No new rules go into it.
- **The navigation is hand-written** in three component files; D2 generates it from the menu registry with the link crawler as the gate.
- **Sidebar panels, tab pages, breadcrumbs on real pages, the customer dashboard, the staff dashboard, global search and the client-profile tabs** are D3/D4 work (the components exist; the screens do not).
- **Page-level strings are not yet wrapped for translation** (shells and components are); wrapped screen by screen in D3/D4.
- **Dark mode follows the visitor's system setting**; there is no manual toggle.
- **HTML emails are the text email in a branded frame**, not separately designed per event.
- **`ManifestStaticFilesStorage`** (fingerprinted assets, a build failure on a missing file) is D2.
- Vendored third-party files (Bootstrap, Bootstrap Icons, htmx) are minified builds; updating them is a manual replace (versions are recorded in the roadmap).
