# Phase D2 Report: Link Integrity Harness

**Date:** 2026-09-26
**Baseline:** Phase D1 complete (`2219a55`)
**Scope rule (v2 Section 08):** no URL name, service, model behaviour or API contract was changed. New: a menu registry, a crawler and its test data, the retired-paths map, a startup check and a production storage setting.

## What D2 delivers

| Roadmap item | Result |
|---|---|
| Menu registry (Rule 5.3) | `apps/core/navigation.py` is the **only** place a menu entry is written. The three navbars, the account menu, the Setup menu, the active-page highlight and the breadcrumbs are all drawn from it. `manage.py check` (and so the deploy) **fails** if an entry names a URL that does not exist, a permission that does not exist, a missing parent, an empty group, a duplicate key and so on (`core.E001`) |
| Crawler as a CI gate (Rule 5.5) | Eight roles (anonymous, customer owner, billing contact, technical contact, support agent, manager, admin, super admin) each start at `/` and follow every link, GET form, `hx-get`, script, image and stylesheet on a site holding **one object in every status**. Any 404, 500, 403 (a link to a page the person may not open) or dead link (`#`, empty, `javascript:`, an anchor that does not exist) fails the build |
| Coverage floor | Every named page that answers GET must be reached by some role; the only exceptions are listed in the test with a reason (tracking pixel, referral link, test gateway, email-only pages, retired paths) |
| Email links (Rule 5.4) | Every address in every email the fixture sends starts with `SITE_URL`, resolves to a page, and **opens for the person it was sent to** (reset links show a password form; the tracking pixel is a GIF). A lint forbids a hand-typed `http(s)://` anywhere in templates or non-test code. A deploy check (`core.E002`) refuses a `SITE_URL` that is not `https://`, has a trailing slash or names localhost |
| PDF links | Every invoice (all seven statuses), every quote (all five) and every report PDF is fetched; none contains a link or annotation |
| Redirects (Rule 5.7) | A `RETIRED` map (`apps/core/redirects.py`): `/login/`, `/register/`, `/store/…`, `/knowledgebase/…` answer 301 to the current page, keep the query string and cannot be turned into an open redirect |
| Safe `next` | Nine hostile targets (`//evil`, `https://evil`, `///evil`, `/\evil`, `javascript:`, `data:` and so on) are ignored, both as a form field and in the address; ten protected pages send a visitor to `/account/login/?next=<page>` and back |
| Fingerprinted static files (Rule 5.4) | `ManifestStaticFilesStorage` in production settings; a test runs `collectstatic` with it into a temporary directory, and checks every `{% static %}` name in every template is in the manifest. A missing file named from a stylesheet now fails the image build instead of a page |

## The menus now

| Audience | Top level | Grouped under |
|---|---|---|
| Public | Plans, Domains, Help, Sign in, Create account | none |
| Customer | Home, Open Ticket, Affiliates, Cart, Notifications | Services (hosting, orders, order new), Domains, Billing (invoices, quotes), Support, and the account menu (profile, your account, **cancellation requests**, change password, notification preferences, sign out) |
| Customer without a client account | Home, Plans, Help, Notifications | account menu |
| Staff | Clients, Orders, Billing, Support, Reports, Utilities, Setup (gear), Notifications | each group shows only what the person may open; a group with nothing openable is not drawn; Django admin appears in the account menu for Super Admin only |

An entry is shown **exactly** when the person may open it: tested in both directions (every shown link opens; every hidden staff entry is refused when requested directly).

## Defects found while building it

1. **A real broken link (Rule 5.6):** the Servers page linked to Products for a Support Agent, who cannot open Products (403). Found by the crawler on its first run; fixed by showing the link only with `view_products`.
2. **My own menu used `<a href="#menu-…">` for the dropdown toggles**, which is the kind of link Rule 5 forbids. Found in the browser pass by reading the menu hrefs (the crawler's first version considered the anchor valid because the target id existed); the toggles are now real `<button>` elements, and the crawler now also reports dead anchors.
3. **Crawling changed the data it was testing:** signing a person in updates `last_login`, which silently invalidates that person's emailed password-reset link, so the reset link tested "400". Found by the email test; the crawler now signs in without touching `last_login`.
4. **The fixture's fake status for a notification** (`ticket.new`) tripped an existing test that guards the event registry; fixed by using a registered event.
5. **Breadcrumbs appeared on the profile page** (the home page has none); excluded from the registry crumbs.
6. **Test-infrastructure trap:** a module-scoped fixture that builds the world only works when the test database is already set up; the module is marked `django_db` so it also runs alone.

7. **The new deploy check would have failed CI** (its `check --deploy` step runs with the default `http://localhost:8000`). Caught before pushing by running the CI command locally; the step now sets a production-style `SITE_URL`. **For the live server, `SITE_URL` must be set to the public https address** (no trailing slash) or `manage.py check --deploy` fails; `.env.example` says so.

## Tests

**+106 tests (registry 34, crawler and harness 29, redirects 43): 1399 on PostgreSQL (1385 on SQLite; the 14 concurrency tests skip there).**

- **Registry (34):** valid by construction; every kind of fault detected (12 parametrised, including a duplicate key); a broken registry fails `manage.py check`; visibility per role for customer with and without a client account, anonymous, agent, manager, admin, superuser; a feature flag; the bell's count; hidden-if-and-only-if-forbidden; active state; breadcrumbs; no navigation template contains a link of its own.
- **Crawler (29):** the world has all eight roles; eight crawls (one per role) find nothing broken; the coverage floor; the crawler itself is proved on a deliberately broken site (404, 500, 403, a broken form action, a broken `hx-get`, a missing image, `#`, empty and `javascript:` links are reported; off-site, `mailto:` and good anchors are not); the parser and relative-link rules; the email, lint, deploy-check and PDF tests above.
- **Redirects, `next`, static files (43).**
- **Mutation checks:** eight faults were introduced in turn and each was caught: the products link shown to everyone, a link to a page that does not exist, a customer page linking to a staff page, a menu entry removed so its page becomes unreachable, an open redirect after sign-in, a retired path answering 302, a hand-typed address in an email, and production without fingerprinted storage.
- **Browser pass** (headless Chromium, anonymous / customer / manager at 375, 768 and 1280 px, plus dark mode, keyboard and menus, and the new checks: breadcrumbs, the active page marked in the menu, every menu link an internal path): **zero findings**. Menu screenshots were reviewed by eye.

## D2 exit criteria

| Criterion | State |
|---|---|
| Menu registry drives every menu; startup check | ✅ |
| Crawler green for every role, on a world with one object per status | ✅ |
| Template lint green (no hand-typed addresses; no hand-written menu links) | ✅ |
| Email, PDF, redirect, safe-`next` tests | ✅ |
| Fingerprinted static files and a `collectstatic` test | ✅ |
| Tracker "Links" column filled for D0–D2 | ✅ |
| CI green | ✅ run 36180365989 on `da099e8` (first run) |

## Known limitations (all scheduled)

- **Fixture depth:** statuses that need a real provider round trip (a hosting account genuinely provisioned by WHM, a domain genuinely registered) are set directly on the record; the pages are what is under test, and the flows have their own tests.
- **Phases 01–13 "Links" column** is back-filled with the D2 crawl (all eight roles, all statuses) rather than a per-phase crawl at the time.
- **The crawler does not submit forms** (only GET forms and links). POST flows are covered by each phase's tests; a redirect after a POST is covered by them too.
- **Only the pages a role can reach by clicking are crawled.** A page reachable only by typing its address is covered by the coverage floor, which lists it as an exception with a reason.
- **Google/third-party links** do not exist (the site loads nothing from outside; tested in D1), so external link checking is not needed.
