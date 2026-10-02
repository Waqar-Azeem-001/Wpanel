# E13: External / WordPress Storefront Integration

**Date:** 2026-10-02 (slice 1), 2026-10-03 (slice 2: webhook invalidation, per-storefront overrides)
**Baseline:** Phases 01-16 (both slices), D0-D8, E1-E12 and Phase 17 slice 1 complete, all local (not deployed).
**Source:** `WP_Requirement.md` (owner-supplied 16-step specification, pasted in full and followed point by point).

The brief: let one or more external WordPress sites display a chosen slice of the catalog (plans, prices, addons,
domain pricing) and send customers back into this project's own cart/checkout - without ever letting WordPress
calculate a price, hold a staff credential, or become "another backend". Rule 1 inspection first, reusing every
existing piece that already fit (catalog, pricing, audit, RBAC, navigation, DRF versioning) rather than building a
second copy of any of them.

## Inspection report (Rule 1)

| Area | State | Notes |
|---|---|---|
| A reusable, multi-site storefront concept | MISSING | Nothing like `ExternalStorefront` existed; the project's only "external site" concept was the public catalog itself, which has no notion of a connected caller at all. |
| Per-product "publish externally" | MISSING | `Product` had no such field; added `publish_externally` (global switch, Step 3). |
| Per-storefront publish/assignment | MISSING | Added `StorefrontProduct` (Step 3's "never duplicate the Product record" - a link row, not a copy). |
| Product pricing engine | FOUND - REUSE | `ProductPrice`/`PriceEntry` (`apps/products/models.py`) is the one and only price source; the storefront API reads it directly, computes nothing. |
| Domain pricing | FOUND - REUSE | `apps.domains.models.TldPricing` is already the authoritative register/renew/transfer price table; no second one created (Step 6). |
| Addon catalog | FOUND - REUSE | `apps.products.models.Addon`/`AddonPrice` already exist; exposed read-only as-is. |
| Addon-to-product compatibility | MISSING (project-wide, pre-existing) | Confirmed via `grep` that no such rule exists anywhere in `apps.products`; per Step 7, documented as a limitation (see Known limitations) rather than invented here. |
| Public catalog API (`/api/v1/products/`, `/api/v1/addons/`, `/api/v1/tlds/`) | FOUND | Already anonymous-readable via `PublicReadPermission`. Deliberately **not** reused for the storefront surface itself (see Problem solving, below) - it has no concept of "which storefront", no per-storefront scoping, and no storefront-specific authentication, and bolting those onto a public anonymous endpoint would have been a bigger, riskier change than a small parallel surface that reuses the same underlying data. |
| Credential/secret storage | FOUND - REUSE (pattern) | `MFARecoveryCode.code_hash` already established the "one-way hash, never recoverable" pattern for a secret this project issues itself; the storefront secret uses the same `django.contrib.auth.hashers` functions. `apps.core.crypto` (reversible) was deliberately **not** used - that mechanism exists for *third-party* secrets (e.g. a registrar password) this project must decrypt to act on; a storefront secret is one we issue and only ever compare, never decrypt. |
| Audit log | FOUND - REUSE | `apps.audit.services.record` used for every administrative action; its existing `_scrub()` already redacts any metadata key literally named `secret` as an extra belt-and-braces measure (the raw secret is in any case never placed in metadata - see Security). |
| RBAC / permissions | FOUND - REUSE | `view_providers`/`manage_providers` (storefront connections) and the existing `manage_products` (publishing a plan) already existed and already have the right shape - no new permission codename invented. |
| Staff navigation registry | FOUND - REUSE | `apps/core/navigation.py`'s `item()` registry; added one entry, no new mechanism. |
| Order/checkout flow | FOUND - REUSE | `catalog:product_detail` (public, by slug) is the existing "configure and add to cart" landing page; `order_url` is built with `reverse()` + `settings.SITE_URL`, identical to every other absolute link in the project (Rule 5). |
| DRF versioning / router | FOUND - REUSE | `config/api_urls.py`'s `NamespaceVersioning` + `DefaultRouter`; the new endpoints are just more entries in the same file. |

## CHANGES REQUIRED (what this slice built)

### Models

- **`apps/products/models.py`**: `Product.publish_externally` (`BooleanField`, default `False`) - the single
  global kill-switch. Kept on `Product` itself (not in the new app) because it is a plain fact about the product,
  the same shape as `auto_setup`/`default_auto_renew` already on that model - not a per-storefront setting.
  Migration: `apps/products/migrations/0008_product_publish_externally.py`.
- **`apps/storefronts/models.py`** (new app):
  - `ExternalStorefront` - name, site URL, integration type (`wordpress` today, extensible), status
    (active/disabled), `public_id` (not secret - safe to display), `secret_hash` (one-way), optional
    `webhook_url` (reserved, unused this slice), notes, `last_accessed_at`.
  - `StorefrontProduct` - the publish link: storefront + product (unique together), optional per-pair
    `title_override`/`description_override`, `featured`, `sort_order`. Deleting either side cascades the link
    only, never the product or the storefront itself.
  - Migration: `apps/storefronts/migrations/0001_initial.py`.

### Services (`apps/storefronts/services.py`) - all business logic, both the staff UI and the API call these

`create_storefront`, `update_storefront`, `set_storefront_status`, `rotate_secret`, `authenticate_storefront`,
`publish_product`, `unpublish_product`, `set_product_storefronts`, `products_for(storefront)` (the single
authoritative "what can this storefront see" query: published to it **and** `publish_externally=True` **and**
`CatalogStatus.ACTIVE` - all three, enforced in one place).

### API (`apps/storefronts/api.py`, `apps/storefronts/authentication.py`, `permissions.py`, `throttling.py`)

All under `/api/v1/storefront/...`, versioned the same way as the rest of the API:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/storefront/products/` | Every plan published to the calling storefront |
| GET | `/api/v1/storefront/products/<slug-or-id>/` | One plan |
| GET | `/api/v1/storefront/categories/` | Only categories holding a plan published to this storefront |
| GET | `/api/v1/storefront/addons/` | Every active addon (global, not per-storefront - see Known limitations) |
| GET | `/api/v1/storefront/domains/pricing/` | Every active TLD's register/renew/transfer price (global) |

No POST/PUT/PATCH/DELETE exists anywhere on this surface - read-only by construction, not just by convention.

**Authentication** - `StorefrontKeyAuthentication`, a dedicated DRF `BaseAuthentication`:

```
Authorization: Storefront <public_id>:<secret>
```

Never a staff/customer session or JWT. On success, `request.user` is `AnonymousUser()` (never a real account -
so a storefront credential can never be mistaken for, or inherit the permissions of, a staff or customer login)
and `request.auth` is the `ExternalStorefront` row; every view reads the caller from `request.auth` only.
`IsStorefrontAuthenticated` additionally requires `request.auth` to actually be an `ExternalStorefront` instance,
so no other authenticator's result can satisfy this permission by accident. Registered with drf-spectacular
(`StorefrontAuthScheme`) so `/api/v1/docs/` documents the scheme instead of silently ignoring it.

**Rate limiting** - `StorefrontRateThrottle` (a `ScopedRateThrottle` keyed by the storefront's own `public_id`,
not by IP, so one storefront's traffic cannot exhaust another's allowance), scope `storefront`,
`THROTTLE_STOREFRONT` env var (default `120/min`).

**Response shape** (`StorefrontProductSerializer`) - id, slug, type, name/description (the storefront's own
override if one was set, otherwise the plan's own), category, `pricing` (keyed by billing cycle, each entry
`amount`/`setup_fee`/`currency`/`months`, read straight from `ProductPrice` - no second calculation), `features`
(the existing `limit_items`), `featured`, `sort_order`, `order_url` (absolute, built via `reverse()`), `updated_at`.
No internal id leakage beyond the plan's own public `id`/`slug`; no cost fields, no server assignment, no
provisioning detail.

### Staff UI

- **Setup -> External storefronts** (`staff.providers.storefronts` in the navigation registry, `view_providers`):
  list (`storefronts_staff:list`), connect (`:create` - shows the raw secret **once**, in the success message,
  never stored or shown again), detail (`:detail`), edit (`:edit`), disable/enable (`:status`), rotate credential
  (`:rotate` - same once-only display). All gated by `view_providers`/`manage_providers`, matching the existing
  "Providers" group's pattern exactly (registrar, payment methods, email providers).
- **Product detail page, "External storefronts" section** (`manage_products` + `view_providers`): the global
  "Publish externally" checkbox lives on the edit form (`templates/products/staff/product_form.html`); the detail
  page (`templates/products/staff/product_detail.html`) adds an "Available on" checkbox per active storefront, one
  shared Featured toggle and Sort order field (Step 12's own mockup shows one shared pair per product, even though
  the underlying model supports a fully independent pair per storefront - the simple form writes the shared values
  to every checked storefront; the richer per-pair capability is already in `publish_product()` for a future,
  more detailed screen if ever needed), posting to `storefronts_staff:product_publish`.

### Audit events

`storefront.created`, `storefront.updated`, `storefront.status_changed`, `storefront.credential_rotated`,
`storefront.product_published`, `storefront.product_updated`, `storefront.product_unpublished`. Every one
recorded through the existing `apps.audit.services.record`; none ever carries the raw secret (it is generated,
returned to the caller, and hashed before anything is written to the database or the audit row - there is no
code path in which the raw value could even reach `metadata`).

## REUSE

| Reused | How |
|---|---|
| `django.contrib.auth.hashers` (`make_password`/`check_password`) | Storefront secret storage - same one-way mechanism as `MFARecoveryCode`, not the project's separate reversible `apps.core.crypto`. |
| `ProductPrice`/`AddonPrice`/`TldPricing` | The only prices the API ever returns; no second pricing table or calculator. |
| `apps.audit.services.record` | Every storefront administrative action. |
| `view_providers` / `manage_providers` / `manage_products` | No new permission codename. |
| `apps/core/navigation.py`'s `item()` registry | One new entry, same mechanism as every other menu item. |
| `reverse()` + `settings.SITE_URL` | `order_url`, exactly like every other absolute link in the project (Rule 5). |
| `config/api_urls.py`'s `NamespaceVersioning` + `DefaultRouter` | The new endpoints are additions to the existing file, not a new API root. |
| `apps.core.permissions` pattern (a dedicated `BasePermission` per API "kind") | Mirrored for `IsStorefrontAuthenticated` rather than overloading `HasPortalPermission`/`PublicReadPermission`. |

## VERIFIED

- **Automated**: `apps/storefronts/tests/test_services.py` (15), `test_api.py` (23), `test_web.py` (10) - 48 new
  tests, covering (per Step 15 of the spec): published-vs-unpublished visibility, the global switch vs. the
  per-storefront link independently, per-storefront isolation (one storefront never sees another's plans or
  overrides), price authority (figures match `ProductPrice` exactly, no drift), credential issuance/validity,
  disabling a storefront stops authentication immediately, rotating invalidates the old secret immediately, the
  raw secret is never present in any audit row or re-displayed after creation/rotation, cross-API isolation (a
  storefront credential is rejected by every staff-only endpoint), `order_url` resolves to a real, working page,
  staff permission gates on every storefront screen and the publish endpoint, and audit coverage for every listed
  event. Full project suite: **2114 tests, 0 failures** (up from 2066 before this slice).
- **Link crawler** (`apps/core/test_crawler.py`): green, including the new `storefronts_staff:*` screens - seeded
  one real storefront and one published product into the shared fixture world (`apps/core/harness.py`) so the
  detail page is genuinely reachable by a role, rather than allowlisted as unreachable.
- **Live HTTP round-trip** (beyond pytest's test client): ran the real dev server against a scratch database,
  issued a real credential through the actual service layer, and called `/api/v1/storefront/products/` over real
  HTTP with `Authorization: Storefront <public_id>:<secret>`. Confirmed: a published+switched-on plan is returned
  with correct pricing/category/order_url; a plan merely existing (not linked to this storefront) is absent; a
  plan linked but with the global switch off is absent; a direct detail lookup for an unlinked plan returns `404`
  (not a 403 that would confirm its existence); missing/wrong/unknown credentials all return `401` identically;
  disabling the storefront mid-session invalidates its credential on the very next request; and the same
  credential is rejected by an unrelated staff endpoint (`/api/v1/clients/`).
- **Browser** (Chromium, 1440 and 390 wide, scratch database): External storefronts list (empty state), "Connect
  a storefront" (secret shown once in the flash message), storefront detail (connection info, disable/rotate,
  published-plans table, edit form), and the product detail page's new "External storefronts" widget (publish
  warning when the global switch is off, "Available on" checkboxes, featured/sort order, save). No console
  errors, no horizontal overflow at either width.

## Known limitations (carried forward, not invented by this slice)

- **Addon-to-product compatibility** does not exist anywhere in the project yet (pre-existing, already tracked in
  Section 26 of the roadmap as "Deferred - Re-triage"); the storefront addon endpoint therefore offers every
  active addon to every storefront, exactly as the public catalog API already does, rather than inventing a new
  rule here.
- **Single global store currency** (`settings.STORE_CURRENCY`) - also pre-existing; the storefront API reports
  this one currency on every price, same as the rest of the project.
- **No reveal-after-creation for a storefront secret, by design** - a lost or leaked secret is rotated, never
  retrieved; this is the spec's own Step 14 requirement, not a gap. The same is true of the webhook signing
  secret added in slice 2.
- **No per-storefront addon or domain-pricing scoping** - Steps 6 and 7 of the spec never asked for either; adding
  them would be scope not requested.
- **Webhook pings cover publish/unpublish and content changes to an already-published plan** (name, description,
  status, price, and the global `publish_externally` switch - see slice 2 below) **but not every conceivable
  change** - e.g. a category's own name changing does not currently ping the storefronts of the plans in it. The
  trigger list can grow the same way (a signal in `apps/storefronts/signals.py`) without touching `apps.products`.

## Slice 2 (2026-10-03): webhook invalidation and per-storefront overrides

Both items the slice 1 report above listed as deferred were implemented, at the owner's request immediately
following that report, rather than left for later:

**Webhook-based cache invalidation (Step 11).** `ExternalStorefront` gained a `webhook_secret_encrypted` field
(reversibly encrypted via the project's existing `apps.core.crypto` - the same mechanism `PaymentProvider` already
uses for a gateway's webhook secret in `apps/billing/models.py` - because, unlike the inbound API credential, this
one must be read back in full every time *we* sign an outgoing delivery, not merely compared). A new
`StorefrontWebhookDelivery` model records every attempt (event, status, attempts, HTTP status, error).
`apps/storefronts/webhooks.py` holds the SSRF guard (`assert_safe_webhook_url`: rejects anything not `https`,
resolves the hostname, and refuses a private/loopback/link-local/reserved/multicast/unspecified address - checked
again on every send, not only when the URL was saved) and the signing function (`t=<unix time>,v1=<hmac-sha256
hex>`, the same shape as the project's existing gateway-webhook signing in `apps.billing.gateways`, so there is one
convention for "how this project proves it sent something", not two). `apps/storefronts/tasks.py`'s
`deliver_webhook_task` (Celery, `autoretry_for`/`retry_backoff`, mirroring `apps.notifications.tasks.deliver_email`)
retries a transient failure (network error, non-2xx) but not a permanent one (unsafe URL, DNS failure, no secret
configured). Two trigger points, both broker-outage-safe (`transaction.on_commit`, the same pattern as order
fulfilment's `schedule()`):
  - `publish_product`/`unpublish_product` (`apps/storefronts/services.py`) ping the one storefront involved.
  - `apps/storefronts/signals.py` (connected in `StorefrontsConfig.ready()`) pings every storefront a plan is
    already published to when that plan's own `Product` row or any of its `ProductPrice` rows are saved or a
    price is removed - **`apps.products` needed no changes at all** for this (Rule 3): the signal is entirely
    owned by `apps.storefronts`, guarded by a cheap "does this product have any storefront links at all" check
    before doing anything, so an ordinary product/price save elsewhere in the project is unaffected.
  A staff member generates/rotates the signing secret from the storefront's own detail page (shown once, exactly
  like the API credential); the page also lists the 10 most recent delivery attempts.

**Per-storefront override screen.** `set_product_storefronts` (the "Available on" checkboxes) now only adds or
drops links - it no longer writes a shared featured/sort_order onto every linked storefront on every resave,
which would have clobbered a title/description/featured/sort_order set from the row below it. The product detail
page's "External storefronts" section now shows one editable row per storefront the plan is already published to
(`storefronts_staff:product_link_update`), each with its own title override, description override, featured flag
and sort order, posting independently - the full capability `StorefrontProduct` already had in slice 1 is now
reachable from the UI, not only from the service layer.

**Verified:** 23 new tests (`apps/storefronts/tests/test_webhooks.py` 19, plus 4 more in `test_web.py`) - the SSRF
guard (safe/unsafe/unresolvable), signing, one delivery attempt (success, non-2xx, unsafe URL, missing secret, all
without ever calling `requests.post` when they shouldn't), `notify_catalog_changed`'s no-op-until-configured
behaviour, both trigger points, both product-save and price-save signals (and that an unrelated, unpublished
product notifies nobody), secret rotation audited without the raw value ever appearing in the audit row, and the
new staff views' permission gates. Browser-checked (Chromium, 1440 wide, scratch database): the storefront detail
page's Webhook card (URL, secret status, Generate/Rotate button with its `data-confirm` prompt, recent-deliveries
table) and the product page's per-storefront override rows (filled in, edited, saved, flash message confirms which
storefront changed) - no console errors, no overflow. Full project suite green throughout.

## Deferred WordPress-side requirements (for whoever builds the plugin; not part of this slice)

- Store the `public_id`/`secret` pair in WordPress options, never in a cookie or anything a visitor's browser can
  read; send the `Authorization: Storefront ...` header from the server side (e.g. via WordPress's HTTP API),
  never from client-side JavaScript.
- Cache the catalog responses locally (there is no pagination - a fetch returns the whole published catalog) and
  refresh on a schedule (e.g. hourly) as a baseline, and additionally on receiving a webhook ping (see below).
- Verify every incoming webhook ping before trusting it: recompute `hmac_sha256(secret, f"{t}." + raw_body)` from
  the `t=...,v1=...` value in the `X-Storefront-Signature` header using the webhook signing secret (generated
  separately from the API secret, on the storefront's own detail page) and compare with a constant-time comparison
  - then simply refetch the catalog from the read API; the ping's own JSON body (`{"event": ..., "storefront":
  ...}`) carries no catalog data and must never be trusted as if it did (Rule 3 - WordPress never stores a second
  copy of pricing logic, and it never trusts a push payload as the source of truth either).
- Link "Order now" buttons straight to each plan's `order_url` - never attempt to build a cart or compute a price
  in WordPress; this project's own checkout is always the one source of truth for what a customer is actually
  charged.
- Treat `404` on a product-detail lookup as "not available to you" (could be unpublished, switched off, or simply
  renamed) rather than surfacing a technical error to a site visitor.

## API contract (for the future plugin)

```
GET /api/v1/storefront/products/
GET /api/v1/storefront/products/<slug-or-id>/
GET /api/v1/storefront/categories/
GET /api/v1/storefront/addons/
GET /api/v1/storefront/domains/pricing/

Header: Authorization: Storefront <public_id>:<secret>
```

Example product (list or detail - same shape):

```json
{
  "id": 10,
  "slug": "live-check-published-plan",
  "type": "shared_hosting",
  "name": "Live Check Published Plan",
  "description": "",
  "category": {"slug": "shared", "name": "Shared hosting", "group": "Hosting"},
  "pricing": {
    "monthly": {"billing_cycle": "monthly", "months": 1, "amount": "9.99", "setup_fee": "0.00", "currency": "USD"}
  },
  "features": ["Disk space: 10 GB", "Bandwidth: Unmetered"],
  "featured": true,
  "sort_order": 100,
  "order_url": "https://portal.example.com/products/live-check-published-plan/",
  "updated_at": "2026-10-02T18:29:02.825136Z"
}
```

No envelope, no pagination (`{"results": [...]}`) - list endpoints return a plain JSON array, since a storefront
is expected to fetch and cache the whole published catalog rather than page through it.

**Webhook ping** (slice 2) - a POST to the storefront's own `webhook_url`, signed in the `X-Storefront-Signature`
header (`t=<unix time>,v1=<hmac-sha256 hex of "{t}." + raw body>`, keyed by the webhook signing secret generated
on that storefront's detail page - separate from the API secret above):

```json
{"event": "product.updated", "storefront": "sf_AK41EUMVKo4pLGzu"}
```

`event` is one of `product.published`, `product.updated`, `product.unpublished` - informational only. The body
carries no catalog data; on receiving one (of any event, with a verified signature), the plugin should simply
refetch from the read API above.

## Deferred to a later slice

- Expanding the webhook trigger list beyond publish/unpublish and an already-published plan's own name,
  description, status or price (e.g. a category rename does not currently trigger a ping for the plans in it).
- A dead-letter view or manual "retry this delivery" action in the staff UI - today a failed delivery is visible
  in the storefront detail page's recent-deliveries table and retried automatically (up to 5 times, with backoff)
  by Celery, but there is no way to force a retry sooner, or to resend a specific past delivery, from the UI.
