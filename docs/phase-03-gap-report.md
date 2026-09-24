# Phase 03 Gap Report: Products, Pricing & Addons

**Date:** 2026-09-24
**Baseline:** Phases 01-02 complete (`3b4e183`)

## FOUND

Phases 01-02 provide users, roles (`view_products`/`manage_products` and `view_hosting`/`manage_hosting` already existed and were already granted to Manager), the audit log, the API foundation, and the client/contact pattern this phase's public-vs-staff visibility split reuses.

## PARTIAL / BROKEN

None - nothing product-related existed yet.

## MISSING (now implemented)

Everything: the catalogue models, pricing, the server-mapping placeholder, and staff/public screens.

## REUSE

| Reused | How |
|---|---|
| `accounts.view_products`/`manage_products`, `view_hosting`/`manage_hosting` | Already existed and already granted to Manager; no permission changes needed |
| `audit.services.record` | Every create/update/status/price/server change, including the "price changes" category called out in Phase 16 |
| `HasPortalPermission` | Extended (not duplicated) with a new `PublicReadPermission` in `apps.core.permissions` - safe methods open to everyone, writes still gated by the same `required_permissions` shape |
| `apps.clients.services.clients_for_user` pattern | Mirrored as `visible_products_for_user`/`visible_addons_for_user` (staff see everything, everyone else sees active only) |
| Phase 02's plain-Form-not-ModelForm convention | `apps/products/forms.py` |
| `apps.core.utils` | New `unique_slug()` helper, written generically for reuse by later phases too |

## CHANGES

- **Models** (`apps/products/models.py`):
  - `Product`: name, slug, type (shared/WordPress/reseller/VPS/dedicated - **domain deliberately excluded**, see Known limitations), description, status, resource limits (JSON), servers (M2M), WHM package name, auto-provision flag, default-auto-renew flag.
  - `Addon`: name, slug, description, status.
  - `CatalogStatus` (active/hidden/retired) is shared by both, so Product and Addon don't each invent their own visibility vocabulary.
  - `PriceEntry` (abstract): billing cycle (monthly/quarterly/semi-annual/annual/biennial/custom/one-time), custom month count, price, setup fee, active flag - subclassed as `ProductPrice` and `AddonPrice`. One-time is addon-only (products reject it in `clean()`); a product/addon can have at most one price row per (cycle, custom-months) combination.
  - `Server`: name, hostname, IP, status, max accounts, notes. **Deliberately minimal** - no credentials. Phase 05 extends this *same model* with encrypted WHM connection details (the pattern already used by `notifications.EmailProvider`); it must not create a second server model.
- **Services** (`apps/products/services.py`): create/update/status for products, addons and servers; `set_price`/`remove_price`/`set_price_active` (upsert semantics - editing an existing cycle's price updates the same row rather than creating a duplicate); `get_effective_price(item, cycle, months)` - the single authoritative lookup later phases (cart, checkout, renewals) must call rather than trusting a client-submitted price; `set_product_servers`; visibility and search helpers.
- **API:** `/api/v1/products/` and `/api/v1/addons/` are public for GET (only active items, a trimmed public shape) and require `manage_products` for POST/PATCH; nested `/prices/` and `/prices/{id}/` sub-resources on both. Neither supports DELETE on the catalogue item itself (no `destroy` action) - a price row can be removed, but not the whole product/addon. `/api/v1/servers/` requires `view_hosting`/`manage_hosting` and is never public.
- **Web:** a public `/products/` catalogue (list + detail, active items only, pricing table) with no login required; staff pages under `/staff/products/`, `/staff/addons/`, `/staff/servers/` for CRUD, status, server mapping and price management, including an HTMX-free but consistent list/detail pattern matching Phase 02's client pages.
- **Enum stability:** added `ENUM_NAME_OVERRIDES` for the four new choice fields so the generated OpenAPI schema (and future mobile clients) get stable names instead of auto-generated ones.

## TEST PLAN

52 new tests, bringing the suite to 150 (all passing on SQLite locally; PostgreSQL/Redis verified by CI as with Phases 01-02).

- **Services (22):** slug generation and uniqueness, default-hidden status, resource-limit validation (rejects non-dict, rejects negative ints, accepts `null`/unknown keys), permission checks for product/addon/server create, audited updates (changed-fields-only) and status transitions (no-op doesn't double-audit), server mapping, price upsert semantics, custom-cycle month validation, one-time rejected for products / allowed for addons, negative price rejected, `get_effective_price` ignoring inactive rows, price removal, and - specifically - proof that server permissions are gated by `manage_hosting`, not `manage_products` (a user with only one of the two is tested against both).
- **API (18):** anonymous sees active-only with the trimmed public shape and gets 404 for hidden items; customer vs staff serializer shape; writes blocked for anonymous/customer; staff create with server mapping; invalid type/resource-limits rejected; support agent (no `view_products` in the role matrix) still gets read access but the public shape, and is denied writes; audited update/status; no delete on the item itself; price list hides inactive from the public but not from staff; add/toggle/delete a price; one-time rejected for a product via the API; addon CRUD and status; servers require `view_hosting`/`manage_hosting` and 404/403 for everyone else, audited status change.
- **Web (12):** public list/detail show active only and 404 hidden items with no login; staff pages need login and permission (customer 403, support agent 403 for products, manager 200); create/edit/status round-trip; invalid resource-limits JSON surfaces a form error instead of a 500; price add/toggle/remove; server mapping checkboxes; addon create/status/price; server pages split correctly on `view_hosting` vs `manage_hosting`; navigation shows the staff Products link only with permission, while the public Plans link always shows.
- **Browser check:** headless Chromium at 1366, 768 and 375 px wide, covering the public list and detail pages, staff product create → price → activate, addon create, and server create. No page-level horizontal overflow and no JS/console errors at any width.

## PHASE 03 EXIT CRITERIA

| Criterion | State |
|---|---|
| Existing implementation inspected, no duplicates | ✅ |
| Public and staff endpoints share one service layer | ✅ |
| Server-side pricing (never trust client input) | ✅ `get_effective_price` is the single lookup point; prices/setup fees are always read from the DB row, never accepted as a total from a request |
| New behaviour and authorization tested, full suite green | ✅ 150 passing (SQLite) |
| Migrations created, `makemigrations --check` clean, schema valid | ✅ |
| Browser check (desktop, tablet, mobile) | ✅ |
| CI green on PostgreSQL + Redis | ✅ Run 36045316721 on `3697eac`: migrations, 150 tests, Celery/Redis round trip |
| Commit after verification | ✅ `3697eac`, pushed to `main` |

## Known limitations / deferred

- **Domain products are out of scope here on purpose.** Phase 04 (Domain Management) will define its own TLD-keyed pricing model for register/renew/transfer, not a `Product` row - conflating the two would violate Rule 3 (one implementation) once Phase 04 lands.
- **Server model has no credentials.** Phase 05 adds encrypted WHM connection fields to the *same* `Server` model (do not create a second one).
- **No multi-currency pricing.** All prices are in a single implicit store currency for the MVP; a currency field may be added to `PriceEntry` if multi-currency is required later - this is a schema change, not a new model.
- **No product-to-addon compatibility restriction.** Any addon can be attached to any product at checkout time (Phase 06); a restriction (e.g. "SSL only for shared hosting") can be added later without breaking existing data.
- **Row-action buttons on the staff pricing table are cramped on narrow (375px) screens** - the table scrolls horizontally within its own container (same pattern as Phase 02's client table), so there is no page-level overflow, but the Disable/Remove buttons need a horizontal swipe to reach. Minor polish, deferred to Phase 18.
- **Public catalogue has no "add to cart" - by design.** Phase 06 (Cart & Checkout) wires the buttons; building them now would mean redoing them once checkout exists.
