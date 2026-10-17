# WHpanel

The customer and staff portal of a hosting business, in the spirit of WHMCS and Hostinger's hPanel. Customers order and manage hosting, domains, email, websites and WordPress without opening cPanel; staff run billing, orders, support and the servers. It is built with Django and Django REST Framework, uses PostgreSQL for data, and runs background jobs through Redis and Celery. Hosting is done on cPanel/WHM servers through a provider layer.

**Start here:** [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) is the summary of the whole project (what is built, tested, proven on the live server, and still open). [ROADMAP.md](ROADMAP.md) is the short plan for what comes next.

## Documentation

- **Overview and plan:** [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md), [ROADMAP.md](ROADMAP.md), [Hostinger-style plan](docs/hostinger-parity-plan.md), [original specification](Hosting_Management_Portal_Master_Development_Roadmap.md)
- **Running it:** [operations runbook](docs/operations-runbook.md), [incident runbook](docs/incident-runbook.md), [go-live plan](docs/go-live-plan.md), [launch checklist](docs/phase-19-launch-checklist.md), [owner inputs](docs/owner-inputs.md), [staging smoke test](docs/staging-smoke-test.md), [server integration guide](docs/server-integration-guide.md), [server plans sync](docs/server-plans-sync.md)
- **Hosting control:** [architecture](docs/h0-hosting-control-architecture.md), [hosting API](docs/hosting-api.md), [cPanel adapter](docs/h14-cpanel-adapter.md), [Open cPanel fallback](docs/h15-cpanel-escape-hatch.md), [WordPress](docs/p4-wordpress.md), [Website care](docs/p5-website-care.md), [website workspace](docs/ui-website-workspace.md)
- **Storefront and WordPress plugin:** [storefront connection guide](docs/storefront-connection-guide.md), [external storefront integration](docs/e13-storefront-integration.md), [WordPress plugin status](docs/wordpress-plugin-status.md)
- **Phase reports:** [Phase 01](docs/phase-01-gap-report.md), [02](docs/phase-02-gap-report.md), [03](docs/phase-03-gap-report.md), [04](docs/phase-04-gap-report.md), [05](docs/phase-05-gap-report.md), [06](docs/phase-06-gap-report.md), [07](docs/phase-07-gap-report.md), [08](docs/phase-08-gap-report.md), [09](docs/phase-09-gap-report.md), [10](docs/phase-10-gap-report.md), [11](docs/phase-11-gap-report.md), [12](docs/phase-12-gap-report.md), [13](docs/phase-13-gap-report.md), [14](docs/phase-14-gap-report.md), [15 follow-up](docs/phase-15-follow-up-gaps.md), [16](docs/phase-16-gap-report.md), [17](docs/phase-17-gap-report.md), [18](docs/phase-18-gap-report.md)
- **Design and owner tracks:** [D0 navigation audit](docs/d0-ui-navigation-audit.md) ([route inventory](docs/route-inventory.md)), [D1 design system](docs/d1-design-system.md), [D2 link integrity](docs/d2-link-integrity.md), [D3 client area](docs/d3-client-area.md), [D3b modern look](docs/d3b-modern-look.md), [D4 staff area](docs/d4-staff-area.md), [D5 visual QA](docs/d5-visual-qa.md), [D6 shell and users](docs/d6-shell-and-users.md), [D7 visual system](docs/d7-visual-system.md), [D8 header layout](docs/d8-header-layout.md), [E1 owner tools](docs/e1-owner-tools.md), [E2 cart and checkout](docs/e2-cart-checkout.md), [E3 store design](docs/e3-store-cart-checkout-design.md), [E4](docs/e4-owner-round-2.md), [E5](docs/e5-owner-round-3.md), [E6](docs/e6-owner-round-4.md), [E8 alerts and policies](docs/e8-alerts-and-policies.md), [E9 looks](docs/e9-looks.md), [E10 email design](docs/e10-email-design.md), [E12 dashboard and bulk delete](docs/e12-dashboard-and-bulk-delete.md), [E14 nine owner requests](docs/e14-nine-owner-requests.md)
- **Hosting tools, one note each:** [H1 websites](docs/h1-websites-dashboard.md), [H2 files](docs/h2-file-manager.md), [H3 databases](docs/h3-databases.md), [H4 email](docs/h4-email.md), [H5 domains and DNS](docs/h5-domains-dns.md), [H6/H7 SSL and PHP](docs/h6-h7-ssl-php.md), [H8 cron](docs/h8-cron.md), [H9 backups](docs/h9-backups.md), [H10 security](docs/h10-security.md), [H11 usage](docs/h11-usage.md), [H12 admin access](docs/h12-admin-access.md), [H13 infrastructure](docs/h13-infrastructure.md)

## What is in it

- **Business:** catalogue, cart and checkout, orders, invoices and payments, renewals and upgrades, lifecycle (suspend, terminate, cancel), affiliates, reports, notifications, storefront API, a WordPress plugin for external storefronts.
- **Customer area:** websites (WordPress or custom, with one-click WP Admin, preview address, Website care, staging, copy website, for every website of a plan), file manager, FTP, databases, email with webmail, domains with a DNS zone editor and zone management, SSL, PHP, cron, backups, security tools, resource use, SSH, billing, support.
- **AI Agent:** a chat on every customer page that checks the customer's own account, explains the steps in the panel first, and prepares changes the customer approves (with a security code for sensitive ones). Works from a staff-edited knowledge base without any outside service; Claude is an optional second mode. Live chat with a person, callbacks and analytics included.
- **Help centre:** 110 articles in 9 topics with search, structured data, a sitemap and `robots.txt`.
- **Staff area:** clients, users with a per-person permission table (allow or hide any permission), products, servers, orders, billing, support, live chats, AI Agent settings, reports, audit log, branding, home page and pages editors.

## Local development (no Docker)

Without `DATABASE_URL`/`REDIS_URL`, dev settings use SQLite and a local-memory cache, and they run Celery tasks inline. Emails print to the console.

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows (source .venv/bin/activate on Linux/macOS)
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py seed_help        # the starter help centre articles (safe to run again)
python manage.py createsuperuser  # becomes Super Admin
python manage.py runserver
pytest                            # about 3,250 tests
```

Optional environment (names only; values go in `.env`, never in the repository): `ANTHROPIC_API_KEY` (only for the AI Agent's Claude mode), `PREVIEW_DOMAIN` and `PREVIEW_PROXY_KEY` (the preview address of new websites), `HOSTING_CPANEL_CAPABILITIES` (which hosting tools are switched on), `SITE_URL` (used for links in emails and in the sitemap).

### Addresses

- Web portal sign-in: http://localhost:8000/account/login/
- Public site: http://localhost:8000/ (front page), `/pricing/`, `/hosting/`, `/products/`, `/domains/`, `/contact/`
- Help centre: http://localhost:8000/help/ (also `/sitemap.xml` and `/robots.txt`)
- Customer area: `/account/` (websites at `/account/hosting/`, orders, billing, support, domains, **AI Agent** at `/account/assistant/`)
- Staff console: `/staff/` (clients, products, domains, hosting, orders, billing, renewals, support, **live chats** at `/staff/chats/`, **AI Agent settings** at `/staff/assistant/`, settings)
- Cart and checkout: http://localhost:8000/cart/
- Payment webhook (the gateway calls this; signature-verified): `POST /api/v1/webhooks/payments/<provider_id>/`
- Django admin: http://localhost:8000/admin/
- API: http://localhost:8000/api/v1/ (docs at `/api/v1/docs/`, staff login required)

## Docker

```bash
cp .env.example .env    # set DJANGO_SECRET_KEY, CREDENTIALS_ENCRYPTION_KEY, passwords
docker compose up --build
docker compose run --rm web python manage.py createsuperuser
docker compose run --rm web python manage.py seed_help
```

## Layout

```text
config/               settings (base/dev/test/prod), urls, api_urls (/api/v1/), celery
apps/core/            shared: error format, crypto, health, the menu registry (navigation.py), the test harness and crawler
apps/accounts/        users, roles and permissions (with per-person hiding), sign-in, two-factor
apps/audit/           append-only audit log (audit.services.record)
apps/notifications/   the message catalogue, email log and delivery, in-app notifications
apps/clients/         client accounts and contacts (owner, billing, technical)
apps/products/        products, add-ons, pricing, servers, public menus
apps/domains/         domains, TLD pricing, registrar adapter
apps/hosting/         hosting accounts, control layer, cPanel and demo adapters, every customer hosting tool
apps/billing/         payment methods, tax, coupons, invoices, quotes, transactions, gateways (calculations.py is the only billing arithmetic)
apps/orders/          cart, pricing engine, checkout, orders (pricing.py is the only place a price is computed)
apps/renewals/        renewal and upgrade invoices, proration, nightly renewal job
apps/lifecycle/       suspension, termination, cancellation
apps/affiliates/      referrals, commissions, payouts
apps/reports/         reports
apps/support/         tickets, departments, saved replies, attachments, the help centre (kb.py, kb_content/)
apps/assistant/       the AI Agent: agent.py, brain.py (knowledge mode), tools.py, actions.py, live.py, analytics.py
apps/branding/        brand, looks, home content, owner-written pages
apps/storefronts/     API for external storefronts
apps/console/         staff console: users, dashboard, setup pages
templates/ static/    pages, styles and scripts
hosting-portal-connector/   the WordPress plugin for storefronts
deploy/nginx/         reverse proxy configuration
docs/                 one note per stage, guides and the runbook
```

## Conventions

- Business logic lives in `apps/<app>/services.py`. API views, web views and the admin all call it, so none of them holds business rules of its own.
- Services raise `apps.core.exceptions.ServiceError`, and the API turns it into the standard error body.
- A staff API view declares `permission_classes = [HasPortalPermission]` along with `required_permissions`. If a view declares nothing, access is denied. Permission checks are `user.has_perm(perm("view_x"))`; an administrator can hide a permission from one person without changing the role.
- Every sensitive action calls `apps.audit.services.record(...)`.
- Emails go through `apps.notifications.services.send_email`, `dispatch` or `notify`. Never call `send_mail` directly. A new kind of message is one entry in `notifications/events.py` plus its template files in `templates/emails/`.
- Money is `Decimal`, and every billing figure comes from `apps/billing/calculations.py`. An invoice's paid amount and status are derived from its succeeded transactions; `python manage.py verify_billing` re-checks every stored figure.
- Renewals and upgrades are invoices: `apps/renewals` creates them, freezes the figures on a `ServiceChange`, and applies the change only when the invoice is paid.
- A paid order is fulfilled in the background (`apps/orders/fulfilment.py`) as `apps.core.system.SYSTEM`, by calling the ordinary domain and hosting services. Every order status change goes through `apps/orders/lifecycle.transition`.
- Hosting actions go through `apps/hosting/control.perform`: an allow-list of operations per capability, checked against the person's rights, audited, cached for reads. A new operation must exist on the demo adapter too.
- The AI Agent never touches a server. It proposes; the panel validates, asks the customer (Approve, or a security code on a separate page for sensitive changes) and runs it with the ordinary services. Tools never take a client number from the model. Passwords and codes are never asked for in the chat.
- Ticket attachments are stored under `PRIVATE_MEDIA_ROOT` (never `MEDIA_ROOT`, which nginx serves) and downloaded only through an authenticated view. Internal notes are hidden from customers.
- The built-in test payment gateway moves no money and only works when `ALLOW_TEST_PAYMENT_GATEWAY` is true (dev/test).
- Provider credentials (email, WHM, registrar, payment) live in the database, encrypted with `apps.core.crypto`. Never put them in environment variables. The one exception is the optional `ANTHROPIC_API_KEY`, which is read from the server environment only.
- Do not type a web address by hand in code or templates; build it from `SITE_URL` or a route name. A test enforces it.
- Help articles are written in a small safe markup (see `apps/support/kb.py`); a link to another article is `[[Exact title]]`. Starter articles live in `apps/support/kb_content/` and are added with `python manage.py seed_help`.
- Every report says what is **built**, what is **tested**, and what is **proven** on a real server.
