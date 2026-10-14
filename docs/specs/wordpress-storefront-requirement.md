You are working on an existing production-oriented Python/Django Hosting Management Portal. Do NOT create a new project, duplicate existing product/pricing logic, or rebuild completed modules.

The project already uses Django, Django REST Framework, PostgreSQL, Redis, Celery, server-side pricing, products, product prices, addons, domain/TLD pricing, carts, checkout, orders, invoices, hosting services, domains, authentication, RBAC, audit logs, notifications, provider adapters, and an API-first architecture.

The goal of this task is to prepare the Django project for integration with one or more external WordPress websites through a custom WordPress plugin.

The Django portal must remain the single source of truth. WordPress must never become responsible for authoritative product pricing, billing calculations, tax, discounts, provisioning rules, invoice calculations, or service state.

IMPORTANT PROJECT RULES

1. Inspect before implementing.
2. Search the repository for existing functionality before creating anything.
3. Reuse existing Product, ProductPrice, Addon, TLD/domain pricing, billing, API, audit, credential encryption, permissions, and notification infrastructure.
4. Do not create duplicate pricing calculators.
5. Do not rename existing URL names.
6. Do not break existing APIs or customer/staff workflows.
7. All sensitive credentials must use the project's existing encrypted credential mechanism.
8. All important administrative changes must be audited.
9. Existing tests must remain green.
10. Do not deploy. Work locally only unless explicitly instructed otherwise.

==================================================
TASK: EXTERNAL / WORDPRESS STOREFRONT INTEGRATION
==================================================

Implement the Django-side infrastructure required to allow WordPress websites to fetch selected hosting products, prices, addons, categories, domain pricing and order URLs from the Django Hosting Management Portal.

WordPress itself will be developed separately. This task is ONLY the Django side.

--------------------------------------------------
STEP 1 — INSPECT THE EXISTING PROJECT
--------------------------------------------------

Before writing code, inspect:

- apps.products
- existing Product model
- existing ProductPrice model
- existing Addon / AddonPrice implementation
- product categories/groups
- apps.domains and TLD pricing
- apps.orders
- existing storefront/catalog APIs
- existing `/api/v1/` URLs
- authentication/API permission classes
- provider/integration credential storage
- apps.core.crypto or equivalent encrypted credential implementation
- audit logging
- staff setup screens
- menu registry/navigation
- existing webhook infrastructure
- existing serializer/service patterns
- tests covering product visibility and pricing

Determine which parts already exist and can be reused.

Do not assume a new API endpoint or database model is necessary until the existing implementation has been inspected.

Produce a short inspection summary before implementation:

FOUND
PARTIAL
MISSING
REUSE
CHANGES REQUIRED

Then implement only the missing pieces.

==================================================
STEP 2 — CONNECTED EXTERNAL STOREFRONTS
==================================================

Add support for registering external storefronts such as WordPress websites.

Prefer a reusable generic concept such as:

ExternalStorefront
or
StorefrontIntegration

Do NOT hard-code the integration specifically around one domain or one WordPress installation.

A connected storefront should support, where appropriate after inspecting existing patterns:

- Name
- Site/domain URL
- Integration type, initially WordPress
- Active / disabled status
- Public/client identifier
- Secure credential or secret
- Created date
- Updated date
- Last successful API access or sync time if useful
- Optional notes
- Optional webhook URL if push invalidation is implemented

Examples:

Web Host Era UK
https://example.co.uk
Type: WordPress
Status: Active

Web Host Era KSA
https://example.sa
Type: WordPress
Status: Active

Use the existing encrypted credential infrastructure where secrets are stored.

Do not store plain API secrets.

Staff should be able to revoke or rotate integration credentials.

--------------------------------------------------
STEP 3 — PRODUCT EXTERNAL PUBLISHING CONTROL
--------------------------------------------------

Administrators must be able to decide whether a product is available to external WordPress storefronts.

Inspect the existing Product model before deciding whether this should be:

- a field on Product,
- a publishing/settings model,
- or a relationship between products and storefronts.

The architecture should support both:

1. Global external publishing
2. Per-storefront publishing

The business requirement is:

An administrator can decide:

"Should this hosting plan be shown on connected WordPress websites?"

For multiple storefronts, staff should eventually be able to configure:

Business Hosting

Publish externally:
Yes

Available on:
[x] Web Host Era UK
[x] Web Host Era KSA
[ ] Another Site

Do not duplicate the Product record for each website.

Reuse one Product and control publication separately.

Possible storefront-specific display metadata may include:

- external/public title override
- short public description
- featured flag
- sort order
- visible billing cycles

Only add these if they are actually needed and can be implemented without duplicating core product data.

The authoritative product name, price and product configuration must remain in the existing product system.

--------------------------------------------------
STEP 4 — STOREFRONT CATALOG API
--------------------------------------------------

Create or extend versioned DRF endpoints under the existing:

/api/v1/

architecture.

Do not create an unversioned API.

The external storefront API should expose only information required to display and sell products.

Possible endpoint structure:

GET /api/v1/storefront/products/
GET /api/v1/storefront/products/<slug-or-id>/
GET /api/v1/storefront/categories/
GET /api/v1/storefront/addons/
GET /api/v1/storefront/domains/pricing/

The final route names must follow the project's existing URL conventions.

Do not rename existing URLs.

If an existing public catalog API can safely support this requirement, extend it rather than creating another catalog implementation.

--------------------------------------------------
STEP 5 — PRODUCT API RESPONSE
--------------------------------------------------

The WordPress-facing product response should provide enough information to render pricing cards.

Where available, return:

- stable product ID
- slug
- name
- description
- category/group
- product type
- active/public status
- available billing cycles
- authoritative server-calculated display prices
- setup fees where relevant
- product features/resource limits
- available addons
- featured state where configured
- external sort order
- order/configuration URL
- currency
- relevant availability state

Example conceptual response:

{
    "id": 12,
    "slug": "business-hosting",
    "name": "Business Hosting",
    "description": "...",
    "category": {
        "slug": "shared-hosting",
        "name": "Shared Hosting"
    },
    "pricing": {
        "monthly": {
            "amount": "9.99",
            "currency": "USD"
        },
        "annual": {
            "amount": "99.99",
            "currency": "USD"
        }
    },
    "features": [
        "20 GB Storage",
        "10 Email Accounts"
    ],
    "order_url": "https://portal.example.com/store/..."
}

Do NOT create a second pricing engine just to construct this response.

Prices must come from the existing ProductPrice / pricing services.

If prices need formatting for display, format an existing authoritative amount rather than recalculating business logic inside serializers.

No business logic should be implemented inside DRF serializers or API views.

--------------------------------------------------
STEP 6 — DOMAIN PRICING API
--------------------------------------------------

Expose externally publishable domain/TLD pricing using the existing domain pricing model.

Where supported by the existing system, expose:

- TLD
- registration price
- renewal price
- transfer price
- available/public state
- currency
- optional WHOIS/privacy price if the existing system supports it

Do not create a second domain pricing table.

Reuse TldPricing or the currently implemented equivalent.

--------------------------------------------------
STEP 7 — ADDONS
--------------------------------------------------

Expose existing applicable addons to WordPress where required.

Return only relevant display data such as:

- ID
- name
- description
- billing cycle
- price
- compatible product information if that functionality already exists

Do not implement a new addon pricing system.

If addon compatibility is currently deferred in the project, do not silently invent new compatibility rules.

Document the limitation instead.

--------------------------------------------------
STEP 8 — API AUTHENTICATION
--------------------------------------------------

Do not give WordPress a normal staff/admin API token.

Implement or extend a dedicated integration authentication mechanism.

An external storefront should only receive permissions required for storefront functionality.

Initial read-only scopes should conceptually be limited to:

products:read
prices:read
addons:read
domains:read

If the project already has an appropriate scoped API credential mechanism, reuse it.

Otherwise implement the smallest secure mechanism consistent with the existing authentication architecture.

The WordPress integration must NOT receive access to:

- staff users
- complete client lists
- invoices
- transactions
- internal notes
- support administration
- system settings
- provider credentials
- audit logs
- server credentials

The API must enforce permissions server-side.

Never rely on WordPress to hide unauthorized information.

--------------------------------------------------
STEP 9 — ORDER URLs
--------------------------------------------------

Version 1 of this integration should use WordPress as the storefront/display layer only.

The preferred flow is:

WordPress product card
→ Order Now
→ Django product configuration/cart
→ Django checkout
→ Django invoice
→ Django order
→ existing provisioning workflow

Generate the order URL with Django's `reverse()`.

Never hard-code internal Django paths.

If guest cart functionality already exists in the project, preserve it.

A visitor arriving from WordPress should be able to continue through the existing cart/checkout system without creating a second WordPress-based billing implementation.

Do NOT implement full checkout inside WordPress in this task.

--------------------------------------------------
STEP 10 — CACHE / DATA VERSION SUPPORT
--------------------------------------------------

WordPress will cache the catalog rather than calling Django on every page load.

Provide useful response metadata for caching where practical, for example:

- updated_at
- catalog version
- Last-Modified
- ETag

Use existing framework capabilities where possible.

Do not implement unnecessary infrastructure if standard HTTP caching solves the problem.

--------------------------------------------------
STEP 11 — OPTIONAL WEBHOOK INVALIDATION
--------------------------------------------------

After the pull-based catalog API is working, inspect whether the project's existing webhook/event infrastructure can safely support cache invalidation notifications.

Desired future behaviour:

Staff changes a product/price
→ transaction commits
→ Django sends signed storefront refresh webhook
→ WordPress clears its local catalog cache
→ WordPress fetches the updated catalog on the next request

Possible events:

product.updated
product.published
product.unpublished
price.updated
addon.updated
domain_price.updated

Do not put outbound HTTP calls directly inside database model `save()` methods.

Use the existing service/event/background-job architecture.

Webhook delivery must be:

- signed
- retryable
- auditable
- timeout protected
- safe from duplicate delivery

If implementing webhooks would create too much scope for the first slice, document them as the next slice rather than implementing a fragile partial solution.

--------------------------------------------------
STEP 12 — STAFF UI
--------------------------------------------------

Add the required staff-facing controls using the existing application shell and shared UI components.

Possible pages:

Setup
→ External Storefronts

Storefront detail:
- website
- type
- status
- credential status
- rotate credential
- test connection if appropriate
- disable integration

Product edit:
External Storefronts

Publish externally:
[ ]

Available on:
[x] Web Host Era UK
[x] Web Host Era KSA

Featured:
[ ]

Sort order:
[ ]

Follow the existing navigation registry.

Do not hard-code sidebar/menu links.

Only staff with the appropriate existing product/system permissions should see or modify these settings.

Do not invent an entirely separate RBAC system.

--------------------------------------------------
STEP 13 — AUDIT
--------------------------------------------------

Audit at least the following actions:

- storefront created
- storefront enabled/disabled
- credential created
- credential rotated
- credential revoked
- product external publishing enabled/disabled
- product assigned/unassigned from storefront
- webhook configuration changed

Never write secret values into logs or audit metadata.

--------------------------------------------------
STEP 14 — SECURITY
--------------------------------------------------

Security requirements:

- secrets encrypted at rest
- secrets never returned again after initial generation unless the existing system explicitly supports secure retrieval
- authentication checked on every integration request
- disabled storefront credentials immediately stop working
- rate limiting
- no staff/session privileges inherited by integration credentials
- no sensitive model fields serialized accidentally
- URL validation for registered storefront domains
- outbound webhooks must protect against SSRF
- webhook signatures must use a strong secret
- constant-time signature comparison where appropriate
- no secrets in query strings
- no credentials logged
- HTTPS expected for production storefront URLs

Follow the project's existing security helpers and conventions.

--------------------------------------------------
STEP 15 — TESTS
--------------------------------------------------

Add automated tests for at least:

1. External-published product appears in storefront API.
2. Non-published product does not appear.
3. Disabled product does not appear unless existing business rules explicitly say otherwise.
4. A product published to Storefront A does not automatically appear to Storefront B.
5. Product price comes from the existing authoritative pricing source.
6. Changing the core ProductPrice changes the API output.
7. No duplicate price calculation exists.
8. TLD prices use the existing domain pricing records.
9. Invalid API credentials return the appropriate authentication response.
10. Revoked credentials stop working.
11. Disabled storefront credentials stop working.
12. Integration credential cannot access staff/private APIs.
13. Order URLs reverse successfully.
14. Existing customer/store/cart tests remain green.
15. Permissions for staff configuration screens.
16. Audit events for sensitive integration changes.
17. No credentials appear in logs.
18. API response contains no internal/provider secrets.

If webhook invalidation is implemented, test:

- signature generation
- retry behaviour
- duplicate delivery safety
- timeout handling
- disabled storefront behaviour

Run the existing full relevant test suite and link crawler before marking the task complete.

--------------------------------------------------
STEP 16 — DO NOT BREAK EXISTING ARCHITECTURE
--------------------------------------------------

The following must remain authoritative in Django:

Product
ProductPrice
Addon
TLD pricing
pricing calculations
discounts
tax
cart
checkout
invoice
order
provisioning
renewals
upgrades
permissions

WordPress is NOT another backend.

The architecture should remain:

Django/PostgreSQL
        |
Existing business/service layer
        |
Versioned DRF Storefront API
        |
External WordPress Plugin
        |
WordPress page / shortcode / Gutenberg block

For purchases:

WordPress
→ Django cart/configuration
→ Django checkout
→ Django invoice/order
→ existing provisioning

--------------------------------------------------
DELIVERABLES
--------------------------------------------------

At the end of the task provide:

1. Inspection report.
2. Files changed.
3. Models added/changed and why.
4. Migrations created.
5. API endpoints added/reused.
6. Authentication method.
7. Staff UI controls.
8. Audit coverage.
9. Automated tests added.
10. Test results.
11. Link crawler result.
12. Known limitations.
13. Deferred WordPress-side requirements.
14. Exact API contract the future WordPress plugin should consume.
15. Update the master roadmap/state tracker/deferred items/decision log where required.

Do not deploy.

Do not build the WordPress plugin in this task.

The objective is to leave the Django portal with a clean, secure, reusable external storefront API so that a WordPress plugin can later connect by entering the portal URL and integration credentials, fetch only the products that staff chose to publish, display authoritative prices, and send customers back to the existing Django checkout.