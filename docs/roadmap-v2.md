# Roadmap V2: from "built and tested" to "selling and operating"

Status of this page: **a proposal for the owner to correct.** Nothing here is started. Written 2026-10-25, after the launch-hardening round.

How to read the sizes: **S** = under 2 days of work, **M** = about a week, **L** = 2 to 3 weeks. They are my work only; waiting for an outside party (a bank, a registrar, a messaging provider) is shown separately as *Needs*. "Done when" says how we will both know it is finished, in terms that can be checked on the live site.

## 1. Where we really are

**Built and tested** (tests run on PostgreSQL in CI; 3,370 tests): the catalogue, cart and checkout, orders, invoices, payments (bank transfer with proof upload, JazzCash, manual), renewals and lifecycle, affiliates, support, notifications, reports (now with a monthly summary), the WHM/cPanel control layer and the customer website tools, the AI Agent, the help centre, the public site and the public status page, and the security layer (emailed sign-in code, one device per account, staff idle logout, origin locked to Cloudflare, rate limits, concurrency guards on every money and provisioning path).

**Proven on the live server:** deploys (script with backup and warm-up, 32 seconds), nightly encrypted full backups, restore into an empty database from a real backup (rehearsed), health panel that shows red when something is not really running, cache invalidation across workers, the transfer-code flow up to the registrar call.

**Not proven yet, and the reason we cannot sell with confidence:**

1. **Domains are still "manual" on live.** A customer can pay for a domain and the panel shows it registered, but nothing is registered at OnlineNIC until a person does it. The OnlineNIC integration (register, renew, transfer in, transfer code) is built and tested with mocks; it waits for the server's IP address to be allowed at OnlineNIC (the live read-only check answered "IP not in the list").
2. **No copy of anything leaves the server.** Code and tooling for Backblaze B2 are done; the bucket, keys and your GPG public key are not.
3. **The restricted deploy and notify users exist but have no keys**, and the GitHub "production" environment (your approval before a deploy) is not set.
4. **No real customer order has gone through the whole chain** on live: sign-up, order, payment, WHM account created, welcome email, ticket, renewal.
5. **Nobody is watching from outside** (an uptime monitor, error tracking) and the staff two-factor rule is off by choice.

## 2. V2 in one sentence

*Take the first 100 paying customers without losing a payment, a domain or their data, and make the product worth more than the plain cPanel they could buy anywhere.*

Four tracks, run in this order. A is the gate: nothing in B to D matters if A is not finished.

| Track | Goal | Roughly |
|---|---|---|
| **A. Open for business** | Every launch blocker closed and a real order proven end to end | 1 to 2 weeks |
| **B. Revenue and retention** | More of what is billed is paid, without staff effort | weeks 2 to 8 |
| **C. Customer self-service** | Fewer tickets, more reasons to choose us | weeks 4 to 12 |
| **D. Operate safely as we grow** | We find problems before customers do, and can recover | continuous |

## 3. Track A: open for business (the gate)

| # | Item | Size | Needs | Done when |
|---|---|---|---|---|
| A1 | **Switch the live registrar to OnlineNIC** and sell one real domain | S | OnlineNIC allows the server IP `62.238.96.50`; money in the OnlineNIC balance; two name servers (`ns1/ns2.webhostera.pk`, already in the settings) | A real cheap domain bought through the cart is found at OnlineNIC (`infoDomain`), shows the right expiry on the panel, the customer got the "domain active" email. The health panel's "Domain registrar" line is green because it asked OnlineNIC |
| A2 | **Off-site backups on** (B2 bucket with Object Lock, write-only key, GPG key) | S | You: bucket, keys, public GPG key (steps in [offsite-b2-setup.md](offsite-b2-setup.md)) | `offsite_check` passes; the next 01:30 backup is in the bucket; a deploy stores an encrypted dump; the first drill from B2 is written in the drill log |
| A3 | **Restricted deploy users with real keys, and GitHub "production" approval** | S | You: two public keys; reviewer and secrets in GitHub ([deploy-setup.md](deploy-setup.md)) | A push waits for your approval, then deploys through the restricted key; the old root keys in `/root/.ssh/authorized_keys` (`github-actions-whpanel-deploy`, `whpanel-ci-deploy`) are removed |
| A4 | **One real order, end to end, with evidence** | M | A test customer account (yours or a friend's); one shared-hosting plan at its real price | Written log with times: sign-up, order, bank-transfer proof uploaded, staff confirms, WHM account created, welcome email arrives (not in spam), the customer logs in to the website tools, opens a ticket, receives a renewal reminder (date moved forward on a test account), cancels and is refunded. Every step green, or a ticket filed |
| A5 | **Watching from outside** | S | You: an account at an uptime monitor, a Sentry project | An alert on your phone when the site is down (tested by stopping nginx for a minute); a test exception shows in Sentry |
| A6 | **Hardening you decided to postpone**, re-decided with the facts in front of you | S | Your decision | Staff two-factor on or off *as a recorded decision*; WHM firewall (3306, 2086, 2082, 2087 closed to the world) and a restricted WHM token; Cloudflare WAF on; branch protection on `main`; OnlineNIC password and API key rotated (they were pasted into a chat); a copy of the whole `.env` outside the server |
| A7 | **Email that arrives** | S | DNS access | DMARC from `p=none` to `p=quarantine` after two weeks of clean reports; sign-in code and invoice emails land in the Gmail inbox with SPF, DKIM and DMARC all "pass" (looked at in "Show original") |
| A8 | **Real prices and plans** | S | You: the final price list | The plan list on the public pages equals your website's list, checked line by line; the TLD price table is synced and each price is above OnlineNIC's cost |

## 4. Track B: revenue and retention

| # | Item | Size | Needs | Why / done when |
|---|---|---|---|---|
| B1 | **Card and wallet payments with automatic confirmation** (JazzCash and Easypaisa are the obvious two, a card gateway if you want international customers) | L | A merchant account; sandbox keys | Today every payment is confirmed by a person. Done when a paid invoice becomes "paid" within a minute by itself, the webhook is signed and replay-safe (the machinery and tests exist), and a daily reconciliation report lists any gateway payment the panel does not know |
| B2 | **Reminder flow that works** | M | none | Invoices due, overdue, service expiring, domain expiring: each one sent through email and, when B3 is done, WhatsApp, with the pay button inside. Done when a test account's reminders arrive on the right days and the report "renewals due" matches what was sent |
| B3 | **WhatsApp and SMS notices** (payment received, order active, expiring soon, ticket replied) | L | A provider (Meta Cloud API or a local aggregator) and approved message templates | Most customers answer WhatsApp faster than email. Done when the same event sends the same short message, opt-in is recorded, and a customer can switch it off |
| B4 | **Auto-renew with a saved method** | M | B1 | Domains and plans renew themselves when the customer allows it; failures are retried on a schedule and explained to the customer |
| B5 | **Abandoned-cart and abandoned-checkout emails** | S | none | One polite reminder after 2 hours and one after 2 days; stops when the customer orders |
| B6 | **Affiliate payouts you can run in ten minutes** | S | none | Monthly payout list, mark paid, customer sees it; tests for double payout exist |
| B7 | **Money report you trust**: monthly income, refunds, churn, renewals due, by product | S | none | The Reports page has the monthly summary; add renewals due and churn rate; the figures equal the ledger (a test already checks the ledger) |

## 5. Track C: customer self-service (fewer tickets, a reason to choose us)

| # | Item | Size | Needs | Why / done when |
|---|---|---|---|---|
| C1 | **"Move my website here"** (assisted, then automatic) | L | A source cPanel to test against | The biggest sales objection. Phase 1: a form (old host, login, domain) that opens a staff task with the checklist and a status the customer can see. Phase 2: WHM's account-transfer API where the source allows it. Done when a real site moves in under an hour of staff time, with the DNS switch guided |
| C2 | **Hosting password reset and cPanel password change** | S | none | One of the commonest tickets. Done when a customer resets it after the emailed code, and an audit row exists |
| C3 | **Data export and deletion** (the customer's own data) | M | none | A privacy promise you can keep. Done when a customer gets a download of their records and a deletion request that staff approve, with what must be kept for tax shown |
| C4 | **Urdu interface** for the customer area and the public site | L | A translator who knows hosting words | Done when a customer switches language and every customer page (not the staff pages) is in Urdu, right-to-left, with the invoice PDF in both |
| C5 | **Customer API tokens** (resellers and agencies) | M | none | Scoped, hashed at rest, shown once, revocable, rate-limited, every call audited. Done when a reseller creates a hosting account through the API with a token and cannot touch another client's data (tests assert it) |
| C6 | **Website tools still missing:** Git deployment, a CDN switch (Cloudflare for the customer's site), automatic repair of changed WordPress core files | L | Some need WHM features switched on (listed in the overview) | Each shipped when it works on one real customer site and has a help article |
| C7 | **The same sign-in-device rule for the API**, and customer alerts for a password change or staff access to their hosting | S | none | Already in the "still open" list of the overview; the emailed alert for the transfer code is the pattern |

## 6. Track D: operate safely as we grow (never "done")

| # | Item | Size | Done when |
|---|---|---|---|
| D1 | **Public pages from Cloudflare's edge** ([plan](plan-public-page-cache.md)) | M | Anonymous visitors in Karachi get a public page in about 50 ms (today 250 to 420 ms). The test that the cached HTML is identical for everyone passes; a catalogue change purges the edge |
| D2 | **A staging copy of WHPanel** | M | A second small stack on a different address, loaded with a copy of live data with the personal details scrambled; every deploy goes there first and the smoke test runs |
| D3 | **Load test from many addresses** (Locust or k6) | S | We know how many customers one 2-CPU server carries with the WHM calls included; the limit and the next step (more workers, a bigger server, the other project moved away) are written down |
| D4 | **Quarterly restore drill and yearly "server lost" rehearsal** | S each | Dates in the drill log in [backup-recovery.md](backup-recovery.md) |
| D5 | **Security review before spending on ads**: an outside pen test of the login, the checkout and the API, and a read of the audit trail by someone who did not write it | M | The report's findings are all fixed or accepted in writing |
| D6 | **Secrets and keys hygiene** | S | Key rotation dates written; the OnlineNIC, WHM, SMTP, Cloudflare and GitHub tokens each have an owner and an expiry; no shared passwords |
| D7 | **Dependency and image updates monthly** | S/month | `pip-audit` clean; the base image rebuilt; the Postgres minor version current |
| D8 | **Support runbooks** for the ten most likely incidents (site down, payment not showing, domain not registering, email not arriving, hosting suspended by mistake...) | M | Each is one page, found from the staff area, tried once as a fire drill |

## 7. What I would do first, in order

1. **A1, A2, A3, A5** as soon as you give me the four things only you can: the OnlineNIC allow-list entry, the B2 bucket and keys with your GPG public key, the two SSH public keys, and the monitor and Sentry accounts. About three working days of mine, spread over the waiting.
2. **A4**, the real order, with you watching. It will find things; that is its purpose.
3. **A6, A7, A8** the same week.
4. Then **B1** (automatic payment confirmation) and **C2** (password reset), because they remove the most staff work per day of effort.
5. **D1** after B1, so the cached pages and the payment pages are not changed at the same time.

## 8. Decisions I need from you

1. **Launch date**, and whether it moves to after A4 passes. (My advice: yes.)
2. **Payment partners:** which wallet or bank, and whether foreign cards matter in the first six months.
3. **WhatsApp provider:** Meta's own API (a business account, approval) or a local provider that is faster to start.
4. **Staff two-factor:** on from launch day, or after a month. (My advice: on from the day real money arrives.)
5. **Urdu:** before launch, or in the first quarter after it.
6. **Who answers tickets, and in which hours.** The Agent's "open a ticket when nobody is online" switch depends on it.
7. **Moving the other project** that shares this server (its CPU and memory use are the first capacity limit).
8. **What you want the public to see in the status page**: only up and down as now, or an incident log you write by hand.

## 9. What I will not do without asking

Deploy, change live settings or money-related data, send email to customers, or touch the other project on the server. Every step above that changes the live site is shown as a diff or a plan first, and every report separates *built*, *tested* and *proven on the live server*.
