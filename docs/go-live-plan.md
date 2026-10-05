# Go-Live Plan (all at once)

> **PARKED (owner, 2026-10-05): not going live yet. Development continues locally.** This plan is kept ready for when
> the owner decides to launch; nothing in it is being executed now.

**Decision (owner, 2026-10-05):** the product works locally; it goes live as one launch, not phase by phase.
**In the launch:** business platform, customer and staff areas, checkout, billing, support, domains, renewals,
affiliates, reports, WHM provisioning, storefront API, WordPress plugin.
**After launch:** the next-generation roadmap (H, N, M tracks), multi-currency, addon compatibility.
Nothing is deployed until the owner approves. Status of each line is kept here; tick as it is done.

## Stage 0 - Owner inputs (blocks everything else): `docs/owner-inputs.md`
- [ ] 1 Domain and staging subdomain  - [ ] 2 VPS with Docker and the TLS route  - [ ] 3 WHM API token
- [ ] 4 Email provider and sending domain  - [ ] 5 Registrar API  - [ ] 6 Payment method details
- [ ] 7 Backup destination  - [ ] 8 First Super Admin email

## Stage 1 - Preparation (can be done now unless marked)
- [x] Full local test suite green (see result in the session notes / CI)
- [x] Production env template: `.env.production.example`
- [x] TLS-ready Nginx example: `deploy/nginx/whpanel-tls.conf.example` (used only if the Nginx container holds the certificate)
- [x] Error tracking, runbook, staging smoke-test list, optional usage sync (built, off)
- [ ] Push to the remote and get CI green (needs owner approval to push)
- [ ] Verify the WHM adapter against the real server: create, suspend, unsuspend, status, usage, backup, terminate (needs input 3)
- [ ] Sending-domain DNS: SPF, DKIM, DMARC (needs inputs 1 and 4)

## Stage 2 - Staging rehearsal
- [ ] Deploy to staging, migrate, create the Super Admin
- [ ] Configure email, registrar, WHM, payment methods in the admin
- [ ] Run `docs/staging-smoke-test.md` in full (signup to renewal, cancellation, support)
- [ ] Link crawler on staging data; every email link opens on the staging domain
- [ ] Backup, then restore into a scratch database (`docs/operations-runbook.md`)
- [ ] Connect the WordPress plugin to the staging portal and test the shortcodes
- [ ] Dashboard "System health" panel all OK
- **Go/No-Go:** any failed item blocks production; fix and re-run that item.

## Stage 3 - Production day
- [ ] Backup point and previous image tag noted (rollback ready)
- [ ] Deploy, migrate, Super Admin, provider settings
- [ ] DNS cut-over, TLS check, HTTPS redirect, error pages
- [ ] Short production smoke test with one real test order
- [ ] Uptime monitor on `/api/v1/health/`, error tracking, backup alert on
- [ ] Turn on `HOSTING_USAGE_SYNC_ENABLED` once WHM is verified

## Stage 4 - First week
- [ ] Daily check of the health panel, failed orders and failed emails
- [ ] Invite 2-3 real customers first, then open wide
- [ ] Restore test with production data within the week
- [ ] One staff member owns recording manual payments

## Rollback
Maintenance mode on, previous image, and restore the pre-deploy database backup if data changed.

## Risks to watch
1. WHM adapter has never run against a real server (403 last time). Test it first.
2. Email deliverability: without SPF, DKIM and DMARC, invoices go to spam.
3. A backup that was never restored is not a backup.
4. Payments are manual: someone must record each one.
