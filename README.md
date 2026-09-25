# Wpanel

This is a hosting business management portal, similar to WHMCS. It is built with Django and Django REST Framework, uses PostgreSQL for data, and runs background jobs through Redis and Celery.

- **Roadmap and rules:** [Hosting_Management_Portal_Master_Development_Roadmap.md](Hosting_Management_Portal_Master_Development_Roadmap.md)
- **Phase reports:** [Phase 01](docs/phase-01-gap-report.md), [Phase 02](docs/phase-02-gap-report.md), [Phase 03](docs/phase-03-gap-report.md), [Phase 04](docs/phase-04-gap-report.md), [Phase 05](docs/phase-05-gap-report.md), [Phase 06](docs/phase-06-gap-report.md), [Phase 07](docs/phase-07-gap-report.md), [Phase 08](docs/phase-08-gap-report.md), [Phase 09](docs/phase-09-gap-report.md), [Phase 10](docs/phase-10-gap-report.md), [Phase 11](docs/phase-11-gap-report.md), [Phase 12](docs/phase-12-gap-report.md), [Phase 13](docs/phase-13-gap-report.md), [Phase 14](docs/phase-14-gap-report.md), [D0 UI & navigation audit](docs/d0-ui-navigation-audit.md) ([route inventory](docs/route-inventory.md)), [D1 design system](docs/d1-design-system.md), [D2 link integrity](docs/d2-link-integrity.md), [D3 client area](docs/d3-client-area.md), [D3b modern look](docs/d3b-modern-look.md)

## Local development (no Docker)

Without `DATABASE_URL`/`REDIS_URL`, dev settings use SQLite and a local-memory cache, and they run Celery tasks inline. Emails print to the console.

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows (source .venv/bin/activate on Linux/macOS)
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py createsuperuser  # becomes Super Admin
python manage.py runserver
pytest
```

- Web portal: http://localhost:8000/account/login/
- Staff client management: http://localhost:8000/staff/clients/
- Public hosting plans: http://localhost:8000/products/
- Staff product/addon/server management: http://localhost:8000/staff/products/
- Domain search: http://localhost:8000/domains/
- Staff domain/TLD management: http://localhost:8000/staff/domains/
- Customer hosting: http://localhost:8000/account/hosting/
- Staff hosting management: http://localhost:8000/staff/hosting/
- Cart and checkout: http://localhost:8000/cart/
- My orders: http://localhost:8000/account/orders/
- Staff orders (All / Pending / Active / Fraud / Cancelled, Add Order): http://localhost:8000/staff/orders/
- Staff billing (invoices, payments, quotes, billable items, settings, payment methods, tax, coupons): http://localhost:8000/staff/billing/
- My invoices and quotes: http://localhost:8000/account/billing/invoices/
- Payment webhook (gateway calls this; signature-verified): `POST /api/v1/webhooks/payments/<provider_id>/`
- Staff renewals (due for renewal, upgrades, changes needing attention): http://localhost:8000/staff/renewals/
- Support: customer tickets http://localhost:8000/account/support/, staff overview http://localhost:8000/staff/support/, public help centre http://localhost:8000/help/
- Django admin: http://localhost:8000/admin/
- API: http://localhost:8000/api/v1/
- API docs (staff login required): http://localhost:8000/api/v1/docs/

## Docker

```bash
cp .env.example .env    # set DJANGO_SECRET_KEY, CREDENTIALS_ENCRYPTION_KEY, passwords
docker compose up --build
docker compose run --rm web python manage.py createsuperuser
```

## Layout

```text
config/              settings (base/dev/test/prod), urls, api_urls (/api/v1/), celery
apps/core/           shared: error format, pagination, permissions, request IDs, crypto, health
apps/accounts/       users, roles & permissions, auth services, web + API views
apps/audit/          append-only audit log (audit.services.record)
apps/notifications/  email log + delivery task, email provider, in-app notifications
apps/clients/        client accounts, contacts (owner/billing/technical), staff + customer pages/API
apps/products/        products, addons, pricing, servers (with WHM connection fields since Phase 05)
apps/domains/          domains, DNS, TLD pricing, registrar adapter (Manual only - no real registration yet)
apps/hosting/          hosting accounts, WHM adapter (Manual + a real, unverified WhmApiAdapter)
apps/billing/          payment methods, tax, coupons; invoices, quotes, transactions, payment gateway adapters (calculations.py is the only billing arithmetic)
apps/orders/           cart, pricing engine, checkout, orders (pricing.py is the only place a price is computed)
apps/support/          tickets, departments, predefined replies, attachments (private storage), knowledgebase
apps/renewals/         hosting terms, renewal and upgrade invoices, proration (proration.py holds the rules), nightly renewal-invoice job
templates/           web pages and email templates
deploy/nginx/        reverse proxy config
```

## Conventions

- Business logic lives in `apps/<app>/services.py`. API views, web views and the admin all call it, so none of them holds business rules of its own.
- Services raise `apps.core.exceptions.ServiceError`, and the API turns it into the standard error body.
- A staff API view declares `permission_classes = [HasPortalPermission]` along with `required_permissions`. If a view declares nothing, access is denied.
- Every sensitive action calls `apps.audit.services.record(...)`.
- Emails go through `apps.notifications.services.send_email` or `notify`. Never call `send_mail` directly.
- Money is `Decimal`, and every billing figure comes from `apps/billing/calculations.py`. An invoice's paid amount and status are derived from its succeeded transactions; `python manage.py verify_billing` re-checks every stored figure.
- Renewals and upgrades are invoices: `apps/renewals` creates them, freezes the figures on a `ServiceChange`, and applies the change only when the invoice is paid. Run `python manage.py generate_renewal_invoices` (or let Celery beat do it nightly).
- A paid order is fulfilled in the background (`apps/orders/fulfilment.py`) as `apps.core.system.SYSTEM` - the actor for work no user is present for - by calling the ordinary domain and hosting services. Every order status change goes through `apps/orders/lifecycle.transition`. `ORDER_AUTO_FULFIL` switches it (on by default). Customers cannot create hosting or domains outside checkout.
- Ticket attachments are stored under `PRIVATE_MEDIA_ROOT` (never `MEDIA_ROOT`, which nginx serves) and downloaded only through an authenticated view; uploads are validated by extension *and* content before anything is written. Internal notes are hidden from customers by `apps/support/services.messages_for`.
- The built-in test payment gateway moves no money and only works when `ALLOW_TEST_PAYMENT_GATEWAY` is true (dev/test). Add a payment provider in Django admin (credentials and webhook secret are encrypted, write-only).
- Provider credentials (email, WHM, registrar, payment) live in the database, encrypted with `apps.core.crypto`. Never put them in environment variables.
