# Phase 18 - Testing & Production Readiness: Rule 1 Gap Report

**Date:** 2026-10-04. Inspection only; no code changed. Everything stays local.

## Verified today

| Check | Result |
|---|---|
| Full pytest suite (local) | Ran to 100%, **0 failures**, 15 skipped |
| `makemigrations --check --dry-run` | No changes detected |
| `check --deploy` (prod settings, local env) | 1 error: `SITE_URL` is localhost (CI sets it to https, so CI is fine); 1 warning: `SECURE_HSTS_PRELOAD` not set |

## Already in place (do not rebuild)

- CI (`.github/workflows/ci.yml`): migration check, migrate on PostgreSQL 17, deploy check, full suite, Celery worker
  round trip via Redis, Redis cache check.
- `/api/v1/health/` endpoint, automation status console, nginx config and maintenance page (`deploy/nginx`).
- Link crawler, template lint, email/PDF link tests (D2), browser sweeps (D5-D8).

## Gaps found

| # | Gap | Type | Action |
|---|---|---|---|
| 1 | Scheduled hosting usage sync not built | Launch blocker (known, Phase 17) | Needs a verified live WHM adapter first |
| 2 | WHM API adapter never run against a real server (backup call got a 403 on the owner's server) | Launch blocker (known) | Owner must supply working WHM access/permissions |
| 3 | No error tracking (Sentry or similar) configured | Missing | Add optional DSN-driven setup, off when unset |
| 4 | No backup/restore runbook or script for the database and private media | Missing | Write `docs/` runbook; confirm with owner where backups go |
| 5 | `SECURE_HSTS_PRELOAD` warning in deploy check | Minor | Decide with owner; only enable when the domain is ready for preload |
| 6 | Monitoring/uptime: only the health endpoint exists, nothing watches it | Missing | Runbook entry: point an external uptime monitor at `/api/v1/health/` |
| 7 | CI does not run the link crawler or a browser pass explicitly (crawler is a pytest test; browser sweeps are manual) | Verify | Confirm crawler runs inside the suite; browser pass stays a manual checklist |
| 8 | Real HTTPS, DNS, email delivery, registrar, production Redis/Celery cannot be verified locally | Environment | Verify at staging (Phase 19) |

## Proposed order (session-limited)

1. Gap 3 (error tracking, small, optional-by-config).
2. Gap 4 and 6 (one runbook document: backups, restore, uptime monitoring, HSTS note).
3. Gap 7 (confirm crawler in suite).
4. Gaps 1, 2, 8 stay open and are carried to Phase 19 as launch blockers; they need owner/server access.

Phase 18 can be marked complete once 3, 4, 6, 7 are done and 1, 2, 5, 8 are recorded as open launch items.

## Progress (2026-10-04, later the same day)

- Gap 3 closed: `config/error_tracking.py` + `prod.py` hook, optional via `SENTRY_DSN`, `sentry-sdk` added to requirements, 2 tests.
- Gaps 4, 5, 6 closed as documentation: `docs/operations-runbook.md` (backup, restore, uptime monitoring, HSTS note).
  Owner still must choose where backups are stored.
- Gap 7 confirmed: the crawler (`apps/core/test_crawler.py`, `test_link_integrity.py`) is part of the pytest suite that
  CI runs. The browser pass remains a manual checklist.
- Still open, carried to Phase 19: gaps 1 (usage sync), 2 (WHM live verification), 8 (HTTPS, DNS, email, registrar on staging).
