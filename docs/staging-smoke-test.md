# Staging Smoke Test (Phase 19)

Run on staging after deploy, then again (shorter) on production. Tick each line; any failure blocks launch.
Do it once as each of: Super Admin, a customer. Use a throwaway customer email you can read.

## 1. Platform
- [ ] `GET /api/v1/health/` returns 200
- [ ] Dashboard "System health": every row OK (Database, Cache, Background jobs, Email provider, Domain registrar,
      Servers, Public address, Error tracking, Scheduled jobs). "Hosting usage sync" is OK only after enabling it.
- [ ] 404 and 500 pages show the branded page (not a stack trace); HTTP redirects to HTTPS
- [ ] Run the link crawler suite against staging data; zero broken links

## 2. Customer journey
- [ ] Visit the store, add a hosting plan and a domain to the cart as a guest
- [ ] Check out; account is created; welcome/verification email arrives and its link opens the **staging/production domain**
- [ ] Order appears in staff Orders as Pending Payment; invoice PDF opens
- [ ] Customer sees the invoice and the payment instructions of the manual method

## 3. Staff journey
- [ ] Record the payment against the invoice; invoice becomes Paid; payment email arrives
- [ ] Accept the order; hosting is provisioned on WHM (account exists on the server, welcome email sent)
- [ ] Domain registers (or manual task is created, per the domain setting)
- [ ] Customer area shows the service, domain, and usage figures

## 4. Lifecycle
- [ ] Generate a renewal invoice for the test service; reminder email arrives
- [ ] Upgrade the plan; proration invoice is correct
- [ ] Suspend and unsuspend from staff; WHM state matches
- [ ] Customer submits a cancellation request; staff processes it; backup-before-terminate works
- [ ] Open a support ticket as the customer, reply as staff; email links open the right page

## 5. Operations
- [ ] Take a database backup and restore it into a scratch database (`docs/operations-runbook.md`)
- [ ] Stop the worker: dashboard shows Background jobs as a Problem; start it again
- [ ] Trigger a test error and confirm it reaches the error tracker (if a DSN is set)
- [ ] Enable `HOSTING_USAGE_SYNC_ENABLED=true`, restart beat, confirm the job appears and usage fills in
- [ ] Maintenance mode on and off from Setup > Brand
