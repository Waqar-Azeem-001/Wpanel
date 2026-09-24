# Phase 01 Gap Report: Foundation & Architecture

**Date:** 2026-09-24
**Repository audited:** https://github.com/Waqar-Azeem-001/Wpanel (`main` @ `abea2fa`, "Initial commit")

## FOUND

The repository has one commit, which contains a one-line `README.md`. There was no application code, configuration, tests or deployment setup.

## PARTIAL

None.

## BROKEN

None.

## MISSING

All 18 items on the audit checklist (roadmap section 28) were missing: project structure, Django apps, models, authentication, users, roles, permissions, REST API layer, services, billing/orders, provider integrations, notifications/email, Celery/Redis, audit/logging, tests, deployment configuration, environment configuration and documentation.

## REUSE

There was no existing code to reuse. Phase 01 builds the shared foundation that every later phase must reuse:

| Concern | Single implementation | Later phases use it via |
|---|---|---|
| Users & auth | `apps.accounts` (`User`, `services`) | `apps.accounts.services` |
| Roles & permissions | `apps.accounts.roles` (Groups + `accounts.*` permissions) | `HasPortalPermission` + `required_permissions`, or `user.has_perm(perm("..."))` |
| API errors | `apps.core.exceptions` (`ServiceError`, handler) | raise `ServiceError` from services |
| Pagination / filtering / throttling | DRF settings + `apps.core.pagination` | default on every view |
| Audit logging | `apps.audit.services.record` | call `record()` for every sensitive action |
| Email & notifications | `apps.notifications.services` (`send_email`, `notify`) | templates in `templates/emails/` |
| Background jobs | Celery (`config/celery.py`), Redis broker | `@shared_task` in each app's `tasks.py` |
| Credential encryption | `apps.core.crypto` | provider models (WHM, registrar, payment) |
| Correlation IDs | `apps.core.middleware.RequestIDMiddleware` | automatic in logs, audit events and error bodies |

## CHANGES (implemented)

- **Project:** Django 5.2 LTS, with settings split into `base`, `dev`, `test` and `prod`. Configuration comes from environment variables and covers infrastructure only.
- **Accounts:** email-based custom user with statuses `active`, `suspended` and `closed`, plus a profile. Covers registration, login and logout (web sessions and API JWT), email verification (signed token tied to the current email address), password reset (single-use token) and password change (revokes all JWTs).
- **Roles:** Customer, Support Agent, Manager, Admin and Super Admin. Each user has one role, which controls their group, `is_staff` and `is_superuser`. Only a Super Admin can grant or revoke Admin or Super Admin, and nobody can change their own role or status.
- **Permissions:** view and manage permissions for users, clients, products, orders, billing, domains, hosting, support, reports, providers and settings, plus `assign_roles` and `view_audit_log`. `sync_roles` runs after every migrate.
- **API foundation:** routes live under `/api/v1/` with namespace versioning. Authentication is JWT (Bearer) plus session. Every endpoint requires authentication by default. Errors share one format, lists use page-number pagination (`page_size` ≤ 100), and filtering, search and ordering are enabled. Rate limits are anon 60/min, user 300/min and auth 10/min. The OpenAPI schema and Swagger docs are staff-only.
- **Endpoints:** `health/`, `auth/{register,login,refresh,logout,verify-email,resend-verification,password-reset,password-reset/confirm,password-change}/`, `me/`, `users/` (staff: list, `status`, `role`), `notifications/` (own, `mark-read`), `audit-events/` (read-only).
- **Web:** server-rendered pages for login, register, email verification, password reset, profile and password change. HTMX is loaded, and the layout is responsive with dark mode.
- **Audit:** an append-only `AuditEvent` records actor, target, metadata (secrets redacted), IP, user agent and request ID. It covers registration, login and failed login (for web, admin and API), logout, verification, password reset/change, profile changes, status and role changes, and email provider changes.
- **Email & notifications:** each email is saved as an `EmailMessage` and then delivered by a Celery task after the transaction commits. Delivery retries with backoff and never sends the same message twice. The email provider is a database record managed in the admin, with its password encrypted. In-app `Notification` records are included.
- **Infrastructure:** Celery with a Redis broker, a Redis cache, request-ID logging and a health check. There is a Dockerfile and a Compose file (PostgreSQL, Redis, migrate, web, worker, beat, Nginx), along with production security settings.

## TEST PLAN

70 automated tests (`pytest`) cover:

- **Authentication:** registration, duplicate and weak passwords, login success and generic failure, suspended login, refresh rotation, logout blacklisting and refresh-token ownership.
- **Email flows:** email verification (including a token for an old email address and tampered tokens), password reset (single use, no leak of unknown emails) and password change (revokes access and refresh tokens).
- **Authorization:** role matrix for every role, `sync_roles` idempotence, customer denied on staff endpoints, support agent vs manager, manager unable to touch admins or roles, admin vs Super Admin grants, self-action prevention, suspension killing live JWTs, and fail-closed permission class.
- **API standards:** error format (401, 400, 404), request IDs, no unversioned API, pagination, filters, search, and docs restricted to staff.
- **Audit, email and Celery:** audit context and redaction; email delivery through the Celery task (eager), idempotency, recorded failures, provider credential decryption, single active provider, production fallback refusal; notification ownership and mark-read.
- **Web:** pages render, login required, register, login/logout audit, open-redirect protection, verification link, reset flow, session kept after password change, profile update.
- **Infrastructure:** credential encryption round trip and wrong-key failure; `check_celery` broker round trip; `makemigrations --check` is clean.
- **CI** (`.github/workflows/ci.yml`): migration check, migrate on PostgreSQL 17, `check --deploy`, full suite on PostgreSQL, and a Celery worker round trip plus cache check on Redis 7.

## PHASE 01 EXIT CRITERIA

| Criterion | State |
|---|---|
| Existing implementation inspected, no duplicates | ✅ |
| Auth, authorization and API auth tested | ✅ 70 passing |
| Migrations created, `makemigrations --check` clean | ✅ |
| Dev-server smoke test (health, pages, register → verification email, login → JWT) | ✅ |
| `check --deploy` with production settings | ✅ (only HSTS preload warning; intentional until domain is final) |
| Celery/Redis verified against a **real** Redis broker and worker | ⏳ CI job added (`.github/workflows/ci.yml`); runs on first push |
| Migrations and tests run on **PostgreSQL** | ⏳ Same CI job; runs on first push |
| Browser check (desktop and mobile, no overflow, no JS errors) | ✅ Headless Chromium at 1366, 768 and 375 px wide: every account page and form flow plus the admin link. No horizontal overflow, no JS/console errors |
| Documentation updated (roadmap tracker, known work, decisions) | ✅ |
| Commit after verification | ⏳ Awaiting approval |

Phase 01 moves to **🟢 Complete** once the CI job passes on GitHub (PostgreSQL, Redis, Celery worker) after the first push.
