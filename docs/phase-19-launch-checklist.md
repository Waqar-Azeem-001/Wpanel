# Phase 19 - Production Launch: Checklist and Owner Inputs

**Started:** 2026-10-04. Nothing is deployed. Pipeline (roadmap): tests -> crawler -> review -> commit -> push -> CI
-> **staging** -> smoke test (+ crawler on staging) -> production deploy -> production smoke test.

Legend: DONE = built and tested locally. NEEDS OWNER = cannot proceed without a decision, access or server.

## A. Inputs needed from the owner (blocks everything below)

| # | Input | Why |
|---|---|---|
| 1 | Production **domain** and the staging subdomain | `SITE_URL`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, email links |
| 2 | A **server** (VPS) for staging and production, with Docker | Run `docker-compose.yml` |
| 3 | **TLS** approach: host Nginx + Certbot, or a load balancer | `deploy/nginx/whpanel.conf` only listens on port 80 and says TLS is terminated in front of it until Phase 19 |
| 4 | **WHM** access that works (API token with the needed privileges) | Last live test returned 403; adapter never verified; usage sync waits on it |
| 5 | **Email provider** account (SMTP/API) | Prod has no fallback: with no active provider, emails fail loudly |
| 6 | **Domain registrar** API account | Domain registration/renewal |
| 7 | **Payment methods** to offer (manual: bank details, JazzCash etc.) | Payments are manual by design |
| 8 | **Backup destination** and who is alerted | See `docs/operations-runbook.md` |
| 9 | Sentry (or similar) **DSN**, optional | Error tracking is off until set |

## B. Checklist (roadmap Phase 19 launch list)

| Item | Status | Notes |
|---|---|---|
| Domain | NEEDS OWNER (1) | |
| HTTPS/TLS | NEEDS OWNER (3) | Config work follows the choice |
| Production database | Ready to provision | PostgreSQL 17 in compose; migrations clean; tested in CI |
| Production secrets | Ready | Generate `DJANGO_SECRET_KEY`, `CREDENTIALS_ENCRYPTION_KEY`; `.env.example` lists all variables |
| Email | NEEDS OWNER (5) | Configure in the staff UI, send a test |
| Payment methods | NEEDS OWNER (7) | Created in the staff UI |
| Registrar API | NEEDS OWNER (6) | |
| WHM API | NEEDS OWNER (4) | Launch blocker |
| Workers (Celery worker + beat) | DONE | In compose; CI round trip passes; dashboard "Background jobs" check |
| Scheduled jobs | DONE | Hosting usage sync is built (`sync-hosting-usage`, daily 03:30) but OFF: set `HOSTING_USAGE_SYNC_ENABLED=true` after the WHM adapter is verified on a real server |
| Monitoring | Runbook written | Owner picks an uptime monitor for `/api/v1/health/` |
| Error tracking | DONE (optional) | Set `SENTRY_DSN` |
| Error pages | DONE | 403, 404, 500 templates; maintenance page |
| Backups | Runbook written | Test restore on staging (NEEDS OWNER 8) |
| Customer signup, test order, invoice, payment, provisioning, renewal | Script ready: `docs/staging-smoke-test.md` | Run on staging; needs WHM and email first |
| Every email link opens the right page on the production domain | To run on staging | Link tests pass locally; re-run crawler against staging |
| Dashboard "System health" panel all green | To check on staging | Shows public address, error tracking, jobs, usage sync |

## C. Done without the owner (2026-10-04)

- Staging smoke-test checklist: `docs/staging-smoke-test.md`.
- Scheduled hosting usage sync, off by default (tests added).
- Still pending until the owner decides TLS: the Nginx 443 config.

## C0. Original plan (for reference)

1. Prepare the Nginx TLS config for the Certbot route (port 443, redirect, HSTS stays in Django) once decision 3 is made.
2. Write a staging smoke-test script/checklist (signup -> order -> invoice -> payment -> provisioning -> renewal).
3. Build the scheduled hosting usage sync against the WHM docs, kept off by default until the adapter is verified live.

## D. Order on the day

1. Get inputs A. 2. Stand up staging. 3. Run migrations, create the Super Admin. 4. Configure email, registrar,
WHM, payment methods in the UI. 5. Smoke test and crawler on staging. 6. Restore test from a backup. 7. Owner approves.
8. Production deploy. 9. Production smoke test. 10. Mark Phase 19 complete in the roadmap.
