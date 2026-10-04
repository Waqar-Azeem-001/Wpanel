# WordPress Plugin (Hosting Portal Connector) - Status and Django Alignment

**Source:** plugin handover note dated 2026-09-29 (Steps 1-10 done on the WordPress side), checked against the
Django side on 2026-10-04 (`docs/e13-storefront-integration.md`, `docs/storefront-connection-guide.md`).
The plugin source (PHP) is **not in this repo**.

**Rule:** Django is the single source of truth for products, prices, domains, addons, checkout. WordPress only
displays what the API returns and links "Order now" to the portal's `order_url`.

## 1. Plugin status (from the handover note)

| Step | Area | Status |
|---|---|---|
| 1 | Plugin foundation | Done |
| 2 | Admin settings and connection test | Done |
| 3 | Cache layer (transients) | Done |
| 4 | `[hosting_plans]` shortcode | Done |
| 5 | Billing cycle switching | Done |
| 6 | Admin health dashboard | Done |
| 7 | Categories and filtering (`[hosting_categories]`) | Done |
| 8 | Domain pricing (`[domain_prices]`) | Done (mock) |
| 9 | Hosting addons (`[hosting_addons]`) | Done (mock) |
| 10 | Advanced shortcode controls | Done |
| 11+ | Live connection and everything after | Not started |

Everything in Steps 1-10 runs on **mock data**. The plugin has never talked to the real Django API.

## 2. The note is outdated: Django side is already built

The note says Django work is pending. It is not (E13, slices 1 and 2, 2026-10-02/03).

| Note says pending | Django reality |
|---|---|
| Live API endpoints | Done: `/api/v1/storefront/...` |
| Publish controls (Step 12) | Done: product publish/unpublish plus per-storefront override screen |
| Webhook invalidation (Step 14) | Django side done: signed webhook, SSRF guard, Celery retry, delivery log |
| Scoped read-only credential | Done: per-storefront credential, secret shown once, rotate only |
| Multi-site (Step 21) | Base model done (`ExternalStorefront`) |

So Django is ready for plugin Steps 11, 12 and 14. The remaining work for those is on the plugin side.

## 3. Contract differences to fix in plugin Step 11

1. **Auth header.** Django expects `Authorization: Storefront <public_id>:<secret>`. The plugin has one "API Key"
   field. It needs two values (public ID and secret), sent server-side only.
2. **Endpoints.** `products/`, `domains/pricing/`, `addons/` match. Django also has `categories/` and
   `products/<slug-or-id>/` (404 means "not available to you", do not show a technical error).
3. **Response shape.** List endpoints return a **plain JSON array** (no `{"results": []}`, no pagination).
   Prices are nested: `pricing.monthly.amount`, `setup_fee`, `currency`. Plugin mock data must be mapped to this.
4. **Order URL.** Use the `order_url` returned per plan. Never build a URL or cart in WordPress.
5. **Webhook receiver.** The plugin needs an endpoint that verifies `X-Storefront-Signature`
   (`t=<unix>,v1=<hmac_sha256(secret, "{t}." + raw_body)>`) with a constant-time compare, then simply refetches
   the catalog. The ping body (`{"event":..., "storefront":...}`) carries no catalog data and must not be trusted.
6. **Cache.** No pagination, so fetch the whole catalog, cache it, refresh hourly as a baseline plus on webhook.

## 4. Gaps that are real on the Django side

| Plugin step | Gap |
|---|---|
| 12 (per-site addons/domains) | Addons and domain pricing are **global**, not per-storefront |
| 20 Addon compatibility | Does not exist anywhere in the project yet |
| 22 Currency/market | Only one store currency (`settings.STORE_CURRENCY`) |
| 18/19 Domain search and order | No storefront availability endpoint yet |
| 14 | Webhook triggers do not cover a category rename; no manual retry or resend in staff UI |

These need new Django work (an E-track slice) before the matching plugin steps can be built.

## 5. Suggested order

1. Plugin Step 11: align auth and response mapping, switch to Live, test against a running portal.
2. Plugin Step 14: webhook receiver plus signature check.
3. Decide whether the owner needs Steps 18-22 for launch; if yes, add the Django endpoints first (new slice).
4. Phase 18 (Testing and Readiness) should include the plugin as a real API client; Phase 19 launch follows.

## 6. Open decisions for the owner

- Plugin before or after Phase 18? (Recommendation: Phase 18 first, plugin Step 11 and 14 as an E15 slice.)
- Is a WordPress storefront a launch requirement? (Section 28 of the roadmap does not list it as a blocker.)
- Are per-site addons, domain search and multi-currency needed for launch?

## 7. Update 2026-10-04: plugin Steps 11-15 built

Plugin source: `E:\Waqar\Clients\WHE\WPanel_WP\hosting-portal-connector` (outside this repo, not under git; a backup of the
pre-change code was kept in the session scratchpad).

| Step | Result |
|---|---|
| 11 Live connection | Done. Sends `Authorization: Storefront <public_id>:<secret>` server-side; list responses wrapped; domain rows mapped (`register_price` -> `register.amount`); 401/403 give a clear message; a credential without `:` is refused locally. The single "API Key" field now holds `public_id:secret`. |
| 12 Publish controls | Nothing to build in the plugin: Django already filters to what is published to this storefront. |
| 13 Caches | Done. Separate transients for products, domain pricing and addons (plus the request-level cache). |
| 14 Webhook | Done. `POST /wp-json/hpc/v1/webhook` verifies `X-Storefront-Signature` (HMAC-SHA256 of `t.body`, 10 minute tolerance, constant-time compare), then clears the caches. New "Webhook Signing Secret" setting. |
| 15 Single plan | Done. `[hosting_plan slug="..."]` (slug or numeric id); unavailable plan shows a quiet message. Also `orderby="sort_order"` for `[hosting_plans]`. |
| 23 Diagnostics | Partly done: last request (endpoint, latency, result), last error and last webhook on the settings page. |
| 24 Hardening | Partly: key never leaves PHP, signed and time-limited webhook, replay window. Output escaping already in the views. |

**Verified:** all PHP files lint clean (PHP 8.2); `tests/shim_test.php` (WordPress functions stubbed) runs 20 checks
against response shapes copied from the Django serializers, including a webhook signature produced by Django's own
`sign()`: all pass. **Not verified:** inside a real WordPress site, and against a running portal (needs the portal's
public address and a real credential).

**Not built, because Django has no endpoint yet:** 18/19 domain search and order flow, 20 addon compatibility,
21 multi-site rules beyond what E13 gives, 22 currency/market. Step 17 uses the portal's `order_url`; the portal's
product page currently ignores a `billing_cycle` query parameter, so the cycle chosen in WordPress is not carried to
checkout yet (a small Django change if wanted). Step 16 (store pages) works today with `[hosting_plans category=...]`.
Step 25 (theme compatibility, accessibility) needs a real site to test.
