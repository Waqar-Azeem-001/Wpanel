# Wpanel

This is a hosting business management portal, similar to WHMCS. It is built with Django and Django REST Framework, uses PostgreSQL for data, and runs background jobs through Redis and Celery.

- **Roadmap and rules:** [Hosting_Management_Portal_Master_Development_Roadmap.md](Hosting_Management_Portal_Master_Development_Roadmap.md)
- **Phase reports:** [Phase 01](docs/phase-01-gap-report.md), [Phase 02](docs/phase-02-gap-report.md), [Phase 03](docs/phase-03-gap-report.md)

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
apps/products/        products, addons, pricing, servers (minimal - Phase 05 adds WHM credentials)
templates/           web pages and email templates
deploy/nginx/        reverse proxy config
```

## Conventions

- Business logic lives in `apps/<app>/services.py`. API views, web views and the admin all call it, so none of them holds business rules of its own.
- Services raise `apps.core.exceptions.ServiceError`, and the API turns it into the standard error body.
- A staff API view declares `permission_classes = [HasPortalPermission]` along with `required_permissions`. If a view declares nothing, access is denied.
- Every sensitive action calls `apps.audit.services.record(...)`.
- Emails go through `apps.notifications.services.send_email` or `notify`. Never call `send_mail` directly.
- Provider credentials (email, WHM, registrar, payment) live in the database, encrypted with `apps.core.crypto`. Never put them in environment variables.
