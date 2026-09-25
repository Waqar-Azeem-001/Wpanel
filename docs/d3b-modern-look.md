# Phase D3b Report: Modern Look and the Web Host Era Brand

**Date:** 2026-09-26
**Baseline:** Phase D3 complete (`d5535f2`)
**Why:** the D1 design was basic. The owner asked for a modern look, the branding of https://webhostera.pk, and the plans taken from that site.
**Scope rule:** presentation and configuration only. No URL name, service, model behaviour or API contract changed (one migration: help text on two brand fields).

## What changed

| Area | Result |
|---|---|
| **Brand (Rule 6: brand is configuration)** | A new command, `python manage.py seed_webhostera`, loads the brand into the database through the normal services: name **Web Host Era**, primary blue `#2a6af2` (the logo's blue, nudged from `#2c6ef8` so white text passes 4.5:1), accent lime `#c8fc35`, footer text, the **logo** and a **favicon** cropped from it. Everything stays editable in Setup > Brand. The generic defaults for a new install are unchanged |
| **Typography** | **Plus Jakarta Sans** (SIL Open Font License, licence file included), vendored as two variable-font files; no font comes from the internet. The site's own font (Gilroy) is commercial, so a close geometric match was chosen |
| **Look** | New tokens: soft surfaces and shadows, 16 px panels, pill badges, stronger headings, focus rings. **Navigation**: a light, sticky bar with the logo for visitors and customers; the dark navy bar for staff (logo on a white chip); pill-shaped active items; softer dropdowns. Stat tiles with icon badges, sidebar panels with a coloured current-page bar, cleaner tables (small-caps headers, no heavy borders), navy footer |
| **Storefront** | A real **front page** for visitors at `/` (hero with a domain search, trust points, the four cheapest plans as pricing cards, Secure / Fast / Reliable). The **plans page** is a hero plus pricing cards (sorted cheapest first, features as a check-list from the plan description). The **domain page** is a hero search with **price tiles** for every extension. Customers still land on their dashboard, staff on their profile |
| **Plans and prices (from the website)** | 8 plans: Shared Starter Rs 600, Shared Grow Rs 750, Shared Digital Rs 975, WordPress Rs 708, Business Rs 750, Reseller Basic Rs 1,099, Improved Rs 2,049, Maximized Rs 2,524 (monthly), with their feature lists and resource limits; **35 domain extensions** with the site's prices (`.pk` family two-year minimum); a manual registrar and one placeholder server so plans are orderable |
| **Accent colour rule** | The accent is now checked to carry **white or dark text**, whichever reads (a lime accent carries dark text); only a mid-tone is refused. The stylesheet exposes `--brand-accent-text` |
| **Store currency** | Prices are rupees, so `STORE_CURRENCY=PKR` was added to the local `.env` (git-ignored) and the storefront formats prices with it. Tests pin `USD` so they never depend on a developer's `.env` |

## Decisions and things to confirm (owner)

- **Longer-term prices:** the site's pages disagree with each other (the headline "Rs 600/mo" versus a "3-year term Rs 667/mo, total Rs 24,012" on the same plan), so only the **monthly headline price** was loaded. Add 1-, 2- and 3-year prices in Setup > Products when decided.
- **Reseller plans:** the home page describes a 32 GB dedicated-style reseller; the reseller page lists Basic / Improved / Maximized. The three named plans were loaded.
- **No invented data:** support email, phone and server details were not on the site in a usable form and are **not** set. The server is a placeholder (`server1.invalid`); set the real WHM server in Setup > Servers.
- **Payment methods, add-ons (monitoring, migration, maintenance) and SSL** were not loaded (no prices published).
- **Claims on the front page** ("Free SSL certificate", "Free website migration", "7-day refund on hosting") come from the site's own text; edit `templates/public/home.html` if any changes. The "99.9% uptime" claim was left out.
- **Trademark/asset note:** the logo and text belong to the owner's own business; they are configuration in the database, not part of the product's code.

## Defects found while doing it

1. The brand contrast rule would have refused the site's own lime accent (it required white text on it); changed as above, with tests.
2. **The local `.env` leaked into tests** (`STORE_CURRENCY=PKR` broke a USD test); tests now pin the currency.
3. The link lint caught a web address in a management-command docstring (the rule "no hand-typed addresses in code" applies to everything).
4. Found by looking at screenshots: heading weights too light, a price wrapping over two lines, the domain price heading unreadable on the dark hero, navigation items wrapping, the plan detail page duplicating features with ugly limit names. All fixed.

## Tests

**+37 tests: 1517 on PostgreSQL** (one parametrised case skips by design). Seed: brand, colours, logo and favicon served safely, 8 orderable plans with the site's prices, 35 extensions, idempotent, brand-only option, currency warning, editable afterwards. Storefront: front page, cheapest-first order, hidden plans hidden, empty shop, plan cards, domain search and tiles, navigation bar with the logo for each audience, font vendored and fingerprinted in production, no external URLs in the stylesheets, dark-mode logo chip. **Nine mutation checks** all caught. Browser pass: 35 customer pages x 3 widths, dark mode, keyboard and menus: **0 findings**; public, customer and staff screenshots reviewed by eye.

## Known limitations

- Staff screens still use the compatibility stylesheet (they are re-skinned in D4), so they share the new colours, fonts and navigation but not yet the new card layouts.
- Dark mode follows the system setting; the logo sits on a white chip there because its tagline is dark grey.
- Only one weight/style of the font family is loaded (variable roman); no italics.
