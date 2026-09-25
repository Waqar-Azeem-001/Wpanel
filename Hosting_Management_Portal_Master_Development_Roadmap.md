# Hosting Management Portal

## Master Development Roadmap & Engineering Specification

**Project:** Independent WHMCS-style Hosting Business Management
Platform\
**Primary Goal:** Build a production-ready platform for running a real
hosting business.\
**Current Focus:** Hosting management portal only.\
**Future Client Strategy:** Web and mobile clients must consume the same
API/business layer.\
**Control Panel Scope:** cPanel/WHM is an external hosting
provider/integration; this project does not build a server control
panel.

------------------------------------------------------------------------

# 00 --- READ THIS FIRST

This document is the **single development roadmap and source of truth**
for the project.

Any developer or AI agent working on this project must follow these
rules.

## Rule 1 --- Inspect Before Implementing

Before changing code:

1.  Inspect the repository structure.
2.  Search for the requested feature.
3.  Inspect related models, services, views, URLs, templates and APIs.
4.  Check existing tests.
5.  Determine whether the feature is:
    -   already complete,
    -   partially complete,
    -   broken,
    -   or genuinely missing.
6.  Only then decide what code needs to change.

**Never assume a feature is missing because it was requested.**

------------------------------------------------------------------------

## Rule 2 --- Never Repeat Completed Work

A completed feature is considered part of the permanent project
baseline.

Once a feature has been:

-   implemented,
-   tested,
-   verified,
-   documented,

it must **not be rebuilt or reimplemented** in a later session.

A later session may touch it only when:

-   a new requirement explicitly changes its behavior,
-   a regression exists,
-   a security issue exists,
-   a provider/API dependency requires a change,
-   or another feature genuinely requires an integration change.

------------------------------------------------------------------------

## Rule 3 --- One Implementation Only

Do not create duplicate:

-   models,
-   database tables,
-   API endpoints,
-   services,
-   permission systems,
-   notification systems,
-   billing calculations,
-   provider clients,
-   background-job systems,
-   UI components.

Before creating anything, search for an existing implementation and
reuse it where appropriate.

------------------------------------------------------------------------

## Rule 4 --- API-First Architecture

The platform must be designed so the same business logic can serve:

``` text
                    WEB APP
                       |
                       |
                 REST API / BFF
                       |
                       v
              BUSINESS / SERVICE LAYER
                       |
        +--------------+--------------+
        |              |              |
        v              v              v
   PostgreSQL        Redis       External APIs
                                   |
                         +---------+---------+
                         |         |         |
                       Domain     WHM      Payment
```

### Important

The project does **not** need FastAPI simply because a mobile app may
exist later.

The initial recommended backend is:

-   **Django**
-   **Django REST Framework**

DRF provides the API layer while Django provides:

-   ORM
-   authentication
-   permissions
-   admin
-   transactions
-   business application structure
-   mature ecosystem

The business/service layer must not depend on the web page that called
it.

Later clients can use the same API:

``` text
                 SAME API
                    |
          +---------+---------+
          |                   |
        Web                Mobile
          |                   |
     Django/HTMX        Flutter/React Native
```

------------------------------------------------------------------------

# 01 --- TECHNOLOGY STACK

## Backend

### Python

Primary programming language.

### Django

Primary application framework.

Use Django for:

-   authentication
-   users
-   permissions
-   business logic orchestration
-   ORM
-   database transactions
-   administration
-   server-rendered web application
-   application configuration

### Django REST Framework

Primary API framework.

Use DRF for:

-   customer API
-   mobile API
-   provider webhooks
-   external integrations
-   asynchronous operation status
-   API authentication
-   API permissions

Do not build duplicate business logic inside serializers or API views.

Preferred structure:

``` text
API View
   ↓
Serializer / Validation
   ↓
Service Layer
   ↓
Domain / Business Logic
   ↓
Repository / ORM
   ↓
PostgreSQL
```

------------------------------------------------------------------------

# 02 --- DATA & INFRASTRUCTURE

## PostgreSQL

Primary relational database.

Expected major entities:

-   Users
-   Clients
-   Products
-   Product Prices
-   Addons
-   Servers
-   Providers
-   Orders
-   Order Items
-   Services
-   Domains
-   Invoices
-   Invoice Items
-   Transactions
-   Quotes
-   Disputes
-   Tickets
-   Affiliates
-   Commissions
-   Notifications
-   Audit Events

------------------------------------------------------------------------

## Redis

Use Redis for:

-   Celery broker
-   caching
-   short-lived state
-   rate limiting where appropriate
-   job coordination where required

Do not use Redis as the permanent source of truth for business records.

------------------------------------------------------------------------

## Celery

Use Celery for:

-   provisioning
-   domain registration
-   domain renewal
-   service renewal jobs
-   invoice reminders
-   expiry checks
-   email delivery
-   notifications
-   provider synchronization
-   report generation
-   retryable external API operations

------------------------------------------------------------------------

# 03 --- WEB TECHNOLOGY

## Django Templates

Primary web UI technology.

Use server-rendered pages for:

-   customer portal
-   admin portal
-   support
-   billing
-   reports

## HTMX

Use HTMX for interactions where server-driven updates are simpler than a
full SPA.

Examples:

-   filters
-   search
-   inline updates
-   modals
-   pagination
-   status changes
-   notifications

## JavaScript

Use JavaScript only where client-side interaction genuinely improves UX.

Do not move business rules into JavaScript.

------------------------------------------------------------------------

# 04 --- SERVER / DEPLOYMENT STACK

Recommended production environment:

``` text
Internet
   |
   v
Nginx
   |
   v
Gunicorn
   |
   v
Django
   |
   +-------- PostgreSQL
   |
   +-------- Redis
                |
                v
             Celery
                |
                v
          External Providers
```

Use:

-   Linux
-   Docker
-   Docker Compose
-   Nginx
-   Gunicorn
-   PostgreSQL
-   Redis
-   Celery Worker
-   Celery Beat

------------------------------------------------------------------------

# 05 --- EXTERNAL INTEGRATION ARCHITECTURE

The application must use provider abstractions.

## Domain

``` text
Domain Service
      ↓
DomainProvider
      ↓
Registrar Adapter
      ↓
Registrar API
```

## Hosting

``` text
Hosting Service
      ↓
HostingProvider
      ↓
WHM Adapter
      ↓
WHM / cPanel API
```

## Payments

``` text
Payment Service
      ↓
PaymentProvider
      ↓
Gateway Adapter
      ↓
Payment Gateway
```

## Email

``` text
Business Event
      ↓
Notification Service
      ↓
Email Service
      ↓
Configured Email Provider
```

Provider-specific code must stay inside the integration/adapter layer.

------------------------------------------------------------------------

# 06 --- CORE BUSINESS MODEL

Keep these concepts separate.

``` text
CLIENT
  |
  +---- ORDER
  |       |
  |       +---- ORDER ITEM
  |
  +---- INVOICE
  |       |
  |       +---- INVOICE ITEM
  |
  +---- TRANSACTION
  |
  +---- SERVICE
  |       |
  |       +---- HOSTING SERVICE
  |       +---- DOMAIN SERVICE
  |
  +---- SUPPORT TICKET
  |
  +---- AFFILIATE
```

### Definitions

**Order**\
Commercial request/purchase.

**Invoice**\
Financial document.

**Transaction**\
Payment event.

**Service**\
Active purchased/provisioned service.

**Domain**\
Domain registration lifecycle.

Never merge these concepts simply to reduce model count.

------------------------------------------------------------------------

# 07 --- PHASE STATUS SYSTEM

Every phase must have one of these states:

-   ⬜ Not Started
-   🟡 In Progress
-   🔵 Verification
-   🟢 Complete
-   🔴 Blocked

A phase can become **🟢 Complete** only after implementation, tests,
verification and documentation are finished.

------------------------------------------------------------------------

# PHASE 01 --- FOUNDATION & ARCHITECTURE

## Objective

Build the reusable technical foundation before business modules are
added.

## Requirements

### Accounts

-   User registration
-   Login
-   Logout
-   Password reset
-   Email verification
-   Profile
-   Account status

### Roles

Initial roles:

-   Customer
-   Support Agent
-   Manager
-   Admin
-   Super Admin

### Permissions

Permissions must cover:

-   clients
-   products
-   orders
-   billing
-   domains
-   hosting
-   support
-   reports
-   providers
-   system settings

### API Foundation

Create the API architecture so future mobile clients can use it.

Required:

-   API versioning strategy
-   authentication
-   permission classes
-   consistent error format
-   validation
-   pagination
-   filtering
-   rate limiting strategy
-   API documentation strategy

### Infrastructure

-   PostgreSQL
-   Redis
-   Celery
-   Email
-   Notifications
-   Audit logging
-   Error logging
-   Environment configuration

## Phase Completion

Before marking complete:

-   inspect existing implementation
-   avoid duplicate systems
-   test authentication
-   test authorization
-   test API authentication
-   test Celery/Redis
-   test migrations
-   update project documentation

------------------------------------------------------------------------

# PHASE 02 --- CLIENT MANAGEMENT

## Objective

Complete the customer relationship layer.

## Features

-   View clients
-   Search clients
-   Filter clients
-   Add client
-   Edit client
-   Manage users
-   Client status
-   Client profile
-   Orders
-   Services
-   Domains
-   Invoices
-   Transactions
-   Support tickets
-   Activity

## Client Profile

``` text
Client
 |
 +-- Users
 +-- Orders
 +-- Services
 +-- Domains
 +-- Invoices
 +-- Transactions
 +-- Tickets
 +-- Quotes
 +-- Cancellations
 +-- Affiliate
 +-- Activity
```

## API

Customer-facing and admin-facing client endpoints must use the same
service layer.

------------------------------------------------------------------------

# PHASE 03 --- PRODUCTS, PRICING & ADDONS

## Objective

Create the commercial catalogue.

## Products

-   Shared Hosting
-   WordPress Hosting
-   Reseller Hosting
-   VPS
-   Dedicated Server
-   Domain products

## Product Fields

-   name
-   description
-   status
-   pricing
-   setup fee
-   billing cycles
-   resource limits
-   server mapping
-   WHM package mapping
-   provisioning rules
-   auto-renewal

## Billing Durations

-   Monthly
-   Quarterly
-   Semi-Annual
-   Annual
-   Biennial
-   Custom duration

## Addons

-   SSL
-   Dedicated IP
-   Backup
-   Malware protection
-   Migration
-   Extra storage
-   Extra email
-   Premium support

## Rules

Prices must be calculated server-side.

------------------------------------------------------------------------

# PHASE 04 --- DOMAIN MANAGEMENT

## Objective

Make domains a complete first-class service.

## Customer Features

-   availability search
-   registration
-   renewal
-   transfer
-   domain details
-   nameservers
-   DNS
-   auto-renew
-   expiry

## Registrar Interface

Required abstraction:

``` text
DomainProvider
 |
 +-- Registrar Adapter
 |
 +-- Future Registrar Adapter
```

## Provider Operations

``` text
check_availability()
register_domain()
renew_domain()
transfer_domain()
get_domain()
update_nameservers()
get_dns()
update_dns()
lock_domain()
unlock_domain()
```

## Reliability

Registration/renewal operations must be idempotent.

------------------------------------------------------------------------

# PHASE 05 --- HOSTING / WHM PROVISIONING

## Objective

Connect hosting products to real cPanel/WHM servers.

## Server Management

-   server records
-   server status
-   credentials
-   package mapping
-   provisioning settings

## WHM Operations

-   create account
-   suspend
-   unsuspend
-   terminate
-   change package
-   status
-   usage where supported

## Provisioning Flow

``` text
PAID ORDER
    ↓
Provisioning Job
    ↓
HostingProvider
    ↓
WHM API
    ↓
+------------------+
|                  |
Success          Failure
|                  |
↓                  ↓
Active          Retry/Review
```

## Must Have

-   idempotency
-   retries
-   failure states
-   manual retry
-   audit
-   safe credentials
-   provider timeout handling

------------------------------------------------------------------------

# PHASE 06 --- CART & CHECKOUT

## Objective

Create the purchase experience.

## Flow

``` text
Product
   ↓
Domain
   ↓
Addons
   ↓
Duration
   ↓
Cart
   ↓
Checkout
   ↓
Payment
   ↓
Invoice
   ↓
Order
```

## Features

-   hosting selection
-   domain selection
-   addons
-   duration
-   custom duration
-   customer information
-   discounts
-   tax
-   payment method
-   final total

## Security

Never trust browser values for:

-   price
-   total
-   duration
-   discount
-   credit
-   tax
-   expiry
-   provisioning state

------------------------------------------------------------------------

# PHASE 07 --- BILLING & INVOICES

## Objective

Build the financial system.

## Features

-   transactions
-   invoices
-   invoice items
-   billable items
-   quotes
-   discounts
-   tax
-   due dates
-   payment status
-   PDF invoices

## Invoice States

-   Draft
-   Unpaid
-   Paid
-   Partially Paid
-   Overdue
-   Cancelled
-   Refunded where supported

## Audit

Financial calculations must be reproducible from permanent database
records.

------------------------------------------------------------------------

# PHASE 08 --- RENEWALS, UPGRADES & PRORATION

## Objective

Correctly handle existing service changes.

## Renewal

``` text
Current Service
      ↓
Renewal Price
      ↓
Invoice
      ↓
Payment
      ↓
Expiry Extended
```

## Upgrade

``` text
Current Plan
      ↓
Days Remaining
      ↓
Unused Value
      ↓
New Plan Price
      ↓
Credit
      ↓
Net Payable
      ↓
Invoice
      ↓
Payment
      ↓
New Plan + New Expiry
```

## Rules

-   remaining value calculated server-side
-   credit cannot exceed valid paid value
-   tampered credit ignored
-   tampered expiry ignored
-   expired plans receive no invalid remaining credit
-   same-plan renewal follows explicit renewal rules
-   invoice shows calculation

------------------------------------------------------------------------

# PHASE 09 --- ORDER MANAGEMENT & LIFECYCLE

## Objective

Create complete operational order handling.

## Screens

-   All Orders
-   Pending
-   Active
-   Fraud
-   Cancelled
-   Add Order

## Lifecycle

``` text
Draft
  ↓
Pending Payment
  ↓
Paid
  ↓
Processing
  ↓
Provisioning
  ↓
Active
```

Exceptional states:

-   Fraud
-   Failed
-   Cancelled
-   Suspended
-   Terminated

Every state transition must be auditable.

------------------------------------------------------------------------

# PHASE 10 --- CUSTOMER SERVICE & SUPPORT

## Objective

Build support operations.

## Features

-   Support overview
-   Tickets
-   New ticket
-   Departments
-   Assignment
-   Priority
-   Status
-   Internal notes
-   Predefined replies
-   Attachments
-   Related service
-   Related domain

## Ticket States

``` text
Open
 ↓
Agent Reply
 ↓
Customer Reply
 ↓
Pending
 ↓
Resolved
 ↓
Closed
```

## Knowledgebase

-   Hosting
-   Domains
-   cPanel
-   DNS
-   Email
-   WordPress
-   Billing
-   Getting Started

------------------------------------------------------------------------

# PHASE 11 --- NOTIFICATIONS & EMAIL

## Objective

Centralize communication.

## Events

-   registration
-   verification
-   order
-   payment
-   invoice
-   overdue
-   hosting activation
-   domain registration
-   renewal
-   suspension
-   cancellation
-   ticket update
-   quote update
-   dispute/payment update

## Email Tracking

Where architecture supports it:

-   sent
-   failed
-   opened
-   open rate

Open tracking is a signal, not proof of reading.

------------------------------------------------------------------------

# PHASE 12 --- CANCELLATION & SERVICE LIFECYCLE

## Objective

Manage service termination safely.

## Features

-   cancellation request
-   reason
-   immediate/end-of-term choice
-   admin review
-   approval
-   suspension
-   termination
-   domain handling
-   refund/final billing handling

## Lifecycle

``` text
Active
  ↓
Renewal Due
  ↓
Overdue
  ↓
Grace
  ↓
Suspended
  ↓
Terminated
```

Timing must be configurable.

------------------------------------------------------------------------

# PHASE 13 --- AFFILIATE MANAGEMENT

## Objective

Support customer referrals.

## Features

-   affiliate accounts
-   referral links
-   attribution
-   commission rules
-   commission records
-   pending
-   approved
-   paid
-   rejected
-   payout history
-   reports

Commission types:

-   percentage
-   fixed amount

------------------------------------------------------------------------

# PHASE 14 --- REPORTS

## Objective

Provide business visibility.

## Sales

-   daily performance
-   orders
-   new customers

## Financial

-   daily income
-   monthly income
-   annual income
-   unpaid invoices
-   transactions
-   refunds/disputes

## Services

-   active hosting
-   active domains
-   expiring services
-   suspended services
-   cancelled services

## Support

-   open tickets
-   resolved tickets
-   department statistics

## Exports

-   PDF
-   CSV
-   XLSX

------------------------------------------------------------------------

# PHASE 15 --- ADMIN OPERATIONS

## Objective

Centralize business operations.

## Dashboard

-   revenue
-   customers
-   orders
-   pending orders
-   active services
-   expiring domains
-   unpaid invoices
-   tickets
-   provisioning failures
-   system status
-   activity
-   notifications
-   alerts

## Administration

-   providers
-   servers
-   products
-   domain pricing
-   email
-   notifications
-   audit logs
-   maintenance
-   settings

------------------------------------------------------------------------

# PHASE 16 --- SECURITY HARDENING

## Objective

Prepare for real customer and financial data.

## Required

-   RBAC
-   MFA
-   secure sessions
-   CSRF protection
-   rate limiting
-   secure passwords
-   credential protection
-   server-side billing validation
-   webhook signature verification
-   API authentication
-   sensitive-action confirmation

## Audit

Record:

-   authentication changes
-   security changes
-   user changes
-   product changes
-   price changes
-   invoice changes
-   payment verification
-   provisioning
-   suspension
-   termination
-   domain changes
-   admin actions

------------------------------------------------------------------------

# PHASE 17 --- RELIABILITY & FAILURE HANDLING

## Objective

Prevent external failures from corrupting business data.

## Scenarios

-   registrar unavailable
-   WHM unavailable
-   payment callback delayed
-   email failure
-   provisioning failure
-   duplicate webhook
-   network timeout
-   worker failure

## Standard Pattern

``` text
Request
  ↓
Validation
  ↓
Create Job
  ↓
Execute
  ↓
+------------------------+
|                        |
Success                Failure
|                        |
↓                        ↓
Complete              Retry?
                         |
              +----------+----------+
              |                     |
          Temporary             Permanent
              |                     |
              ↓                     ↓
            Retry              Manual Review
```

------------------------------------------------------------------------

# PHASE 18 --- TESTING & PRODUCTION READINESS

## Automated Testing

-   unit tests
-   integration tests
-   authorization tests
-   API tests
-   billing tests
-   domain tests
-   provisioning tests
-   renewal tests
-   upgrade tests
-   webhook tests
-   notification tests

## Browser Testing

Test:

-   desktop
-   tablet
-   mobile
-   customer portal
-   admin portal
-   checkout
-   billing
-   support

Verify:

-   no overflow
-   no broken navigation
-   no JS errors
-   forms work
-   permissions work

## Infrastructure

Verify:

-   migrations
-   HTTPS
-   DNS
-   email
-   payment gateway
-   registrar
-   WHM
-   Redis
-   Celery
-   scheduled jobs
-   monitoring
-   error tracking

------------------------------------------------------------------------

# PHASE 19 --- PRODUCTION LAUNCH

## Deployment Pipeline

``` text
Development
    ↓
Implementation
    ↓
Tests
    ↓
Review
    ↓
Commit
    ↓
Push
    ↓
CI
    ↓
Staging
    ↓
Smoke Test
    ↓
Production Deploy
    ↓
Production Smoke Test
```

## Launch Checklist

-   domain
-   HTTPS
-   production database
-   email
-   payment gateway
-   registrar API
-   WHM API
-   workers
-   scheduled jobs
-   monitoring
-   error tracking
-   customer signup
-   test order
-   invoice
-   payment
-   provisioning
-   renewal

------------------------------------------------------------------------

# 20 --- MVP BOUNDARY

The first production MVP must contain:

-   Customer accounts
-   Hosting products
-   Domain products
-   Addons
-   Cart
-   Checkout
-   Invoices
-   Transactions
-   Payment gateway
-   Domain API
-   WHM/cPanel API
-   Hosting services
-   Domain management
-   Renewals
-   Upgrades/proration
-   Cancellation
-   Customer dashboard
-   Admin dashboard
-   Support tickets
-   Notifications/email
-   Basic reports
-   RBAC
-   Audit logs
-   REST API foundation

Advanced features can be added after the MVP is stable.

------------------------------------------------------------------------

# 21 --- API DESIGN STANDARD

## API Versioning

Start with:

``` text
/api/v1/
```

Do not create unversioned public APIs.

## Resource Examples

``` text
/api/v1/auth/
/api/v1/clients/
/api/v1/products/
/api/v1/orders/
/api/v1/services/
/api/v1/domains/
/api/v1/invoices/
/api/v1/transactions/
/api/v1/tickets/
/api/v1/notifications/
```

Exact endpoints must be determined from the actual implementation rather
than blindly creating every route up front.

## API Rules

-   authenticated by default
-   explicit public endpoints only where necessary
-   object-level permissions
-   pagination
-   filtering
-   consistent errors
-   stable response contracts
-   server-side validation
-   audit sensitive operations

------------------------------------------------------------------------

# 22 --- MOBILE READINESS

The mobile application is not required for the initial MVP, but the
backend must not block it.

The API must support:

-   authentication
-   customer profile
-   products
-   cart
-   checkout
-   invoices
-   payments
-   services
-   domains
-   support tickets
-   notifications

The mobile app should contain presentation logic only.

Business rules remain in the backend.

``` text
                 BUSINESS RULES
                       |
                  Django Services
                       |
                    REST API
                 /             \
                /               \
             WEB              MOBILE
```

Recommended future mobile technologies:

-   Flutter
-   React Native

The choice can be made when mobile development actually begins.

------------------------------------------------------------------------

# 23 --- ENGINEERING QUALITY RULES

## Server-Side Truth

Never trust client-provided:

-   prices
-   totals
-   discounts
-   credits
-   expiry
-   permissions
-   service status

## Idempotency

Operations that may be retried must be safe to retry:

-   payment webhooks
-   provisioning
-   domain registration
-   renewal
-   termination
-   invoice payment
-   notifications

## Provider Isolation

No provider-specific API calls inside templates.

Avoid provider-specific logic directly inside views when a
service/adapter layer is appropriate.

## Transactions

Financial and state-changing operations should use database transactions
where atomicity is required.

## Observability

Important background operations should have:

-   status
-   timestamps
-   error information
-   retry count
-   correlation/reference ID where appropriate

Do not log:

-   passwords
-   API keys
-   payment secrets
-   tokens
-   sensitive customer data unnecessarily

------------------------------------------------------------------------

# 24 --- PHASE COMPLETION CHECKLIST

Before marking any phase 🟢 Complete:

### Code

-   [ ] Existing implementation inspected
-   [ ] No duplicate implementation
-   [ ] Architecture rules followed
-   [ ] API/business logic separated
-   [ ] Permissions implemented
-   [ ] Error handling implemented

### Tests

-   [ ] New behavior tested
-   [ ] Authorization tested
-   [ ] Regression tests passed
-   [ ] Relevant full suite passed

### UI

If UI changed:

-   [ ] Desktop checked
-   [ ] Mobile checked
-   [ ] No horizontal overflow
-   [ ] No JS errors
-   [ ] Navigation checked

### Database

-   [ ] Migrations created if required
-   [ ] Migration check clean
-   [ ] No unnecessary schema changes

### Documentation

-   [ ] Roadmap updated
-   [ ] Known limitations recorded
-   [ ] Architecture decision recorded if applicable

### Git

-   [ ] Changes reviewed
-   [ ] Commit created only after verification

### Deployment

-   [ ] Push only when approved
-   [ ] CI checked
-   [ ] Deploy checked
-   [ ] Production smoke test performed

------------------------------------------------------------------------

# 25 --- PROJECT STATE TRACKER

  Phase                          Status   Tests   Browser   Commit   Deploy
  ------------------------------ -------- ------- --------- -------- --------
  01 Foundation & Architecture   🟢       70 ✅   ✅        960bd54  ---
  02 Client Management           🟢       98 ✅   ✅        3d257c4  ---
  03 Products & Addons           🟢       150 ✅  ✅        3697eac  ---
  04 Domain Management           🟢       227 ✅  ✅        e614249  ---
  05 WHM Provisioning            🟢       296 ✅  ✅        9c0c517  ---
  06 Cart & Checkout             🟢       458 ✅  ✅        23168d7  ---
  07 Billing & Invoices          🟢       622 ✅  ✅        ac37961  ---
  08 Renewals & Upgrades         🟡       729 ✅  ✅        ---      ---
  09 Orders & Lifecycle          ⬜       ---     ---       ---      ---
  10 Support                     ⬜       ---     ---       ---      ---
  11 Notifications & Email       ⬜       ---     ---       ---      ---
  12 Cancellation                ⬜       ---     ---       ---      ---
  13 Affiliates                  ⬜       ---     ---       ---      ---
  14 Reports                     ⬜       ---     ---       ---      ---
  15 Admin Operations            ⬜       ---     ---       ---      ---
  16 Security                    ⬜       ---     ---       ---      ---
  17 Reliability                 ⬜       ---     ---       ---      ---
  18 Testing & Readiness         ⬜       ---     ---       ---      ---
  19 Production Launch           ⬜       ---     ---       ---      ---

------------------------------------------------------------------------

# 26 --- KNOWN WORK / DEFERRED ITEMS

Use this section whenever something is discovered but intentionally
postponed.

  -------------------------------------------------------------------------------------------
  Item                               Status    Reason                          Target
  ---------------------------------- --------- ------------------------------- ----------------
  Email open/failure tracking,       Deferred  Phase 01 records sent/failed    Phase 11
  more provider kinds, templates               with SMTP only

  MFA                                Deferred  Roadmap places MFA in security  Phase 16
                                               hardening

  Account lockout beyond rate        Deferred  Auth endpoints throttled         Phase 16
  limiting                                     (10/min); failures audited

  Staff admin portal (non-Django-    Deferred  Django admin used by Super      Phase 15
  admin UI for roles/status/audit)             Admin; API covers staff actions

  Error tracking service (e.g.       Deferred  Structured logging with request  Phase 18
  Sentry) and monitoring                       IDs in place

  TLS termination / certificates     Deferred  Compose Nginx serves HTTP only   Phase 19

  Docker Compose stack run end to    Open      No Docker on dev machine; CI    Phase 19
  end                                          covers PostgreSQL/Redis/Celery

  Require verified email before      Deferred  Verification tracked; purchase   Phase 06
  purchase                                     gating belongs to checkout

  Client profile record sections     Deferred  Orders/services/domains/etc.    Phases 04-13
  (orders, invoices, tickets...)               models do not exist yet

  Customer self-service sub-user     Deferred  Staff manage contacts; needs     Phase 15/16
  invitations                                  customer-side permission design

  Closing a client suspends users/   Deferred  Service lifecycle rules          Phase 12
  services                                     belong to cancellation phase

  Server credentials / WHM adapter   Open      Server model intentionally      Phase 05
                                                minimal in Phase 03

  Multi-currency product pricing     Deferred  Single store currency for MVP    Post-MVP

  Addon-to-product-type              Deferred  Any addon attaches to any        Phase 06
  compatibility restriction                    product for now

  Staff pricing table row-actions    Deferred  Table scrolls in its own         Phase 18
  cramped at 375px width                       container; no page overflow

  Real registrar adapter needed      Open      Manual adapter is local-only    Before launch
  before launch                                simulation, no real network calls

  Multi-part TLDs (.co.uk, .com.au)  Deferred  Needs a public-suffix list       Post-MVP
  not supported

  Domain registration/transfer not   Open      Staff complete manually until   Phase 07/08
  yet wired to billing                         invoices/payment exist

  Automated renewal invoicing        Closed    Phase 08: nightly job creates    Phase 08
  (expiry job)                                 renewal invoices; reminders/dunning
                                               remain Phase 11

  Outbound transfer auth-code        Deferred  Registrar-specific; not in the   Real registrar
  retrieval not implemented                    roadmap's explicit op list      adapter

  WHM API adapter needed live      Open      Written to WHM's public docs,    Before launch
  verification before launch                  never run against a real server

  Hosting provisioning/lifecycle     Open      Staff act manually until        Phase 07/08
  actions not yet wired to billing              invoices/payment exist

  No customer self-service for       Deferred  No billing/cancellation         Phase 08/12
  suspend/terminate/change-package             workflow to hang these off yet

  No automatic multi-server           Deferred  Staff pick manually when a       Post-MVP
  capacity-based selection                     product maps to >1 server

  Hosting usage sync is manual,       Deferred  Needs Celery beat + a verified   Phase 17/18
  not scheduled                                 live WHM adapter first

  Payment, invoices, transactions     Closed    Delivered in Phase 07: checkout   Phase 07
  for orders (order ends at                     issues the invoice; paying it
  "pending payment")                            marks the order paid

  Order fulfilment: provisioning      Open      Needs a system actor - domain/    Phase 07/09
  domains/hosting from a paid order             hosting services authorise a user

  Customer "request without paying"   Open      Bypass checkout; keep until       Phase 07/09
  flows (domain register/transfer,              fulfilment is wired, then retire
  hosting request) still exist

  Guest (anonymous) carts             Deferred  Cart requires a signed-in user    Post-MVP

  No real payment gateway adapter     Open      Only the simulated test gateway   Before launch
  (Stripe is unavailable in                     ships (dev/test only, guarded by
  Pakistan; choice is a business                ALLOW_TEST_PAYMENT_GATEWAY). A
  decision)                                     real one = one adapter class

  Invoices/quotes visible to any      Deferred  Restrict financial documents to   Phase 15/16
  contact of the client                         owner/billing contacts

  Overdue reminders / dunning         Deferred  Overdue is derived and            Phase 11
                                                filterable; no scheduled emails

  Renewals, upgrade proration         Closed    Delivered in Phase 08               Phase 08

  Credit balances (carrying forfeited Deferred  Credit beyond the new price is    Post-MVP
  upgrade credit forward); refunds              forfeited and shown as such; a
  do not reverse a renewal/upgrade              refund never undoes a service change

  Hosting terms are recorded by staff Open      Nothing starts a term when an order Phase 09
  (no automatic start on a paid order)          is paid yet; fulfilment must call
                                                the same term logic

  Downgrades / billing-cycle changes  Deferred  Only upgrades are offered online;   Post-MVP
  online; renewal of EXPIRED domains            staff handle the rest
  and unsuspend-on-payment

  PDF text limited to Western         Deferred  Built-in PDF fonts; other         Post-MVP
  European characters                           scripts print as "?"

  Staff not notified when a customer  Deferred  Visible on the Payments page      Phase 11/15
  reports an offline payment

  Abandoned online payment attempts   Deferred  Stay pending until the gateway    Phase 17
  never expire                                  answers or the invoice is
                                                cancelled

  Coupons apply to first payment      Deferred  No per-product limits or          Phase 08
  only                                          recurring discounts yet

  Tax: one rule per country,          Deferred  Tax-exempt clients done in        Post-MVP
  order-level, exclusive                        Phase 07; still no tax-inclusive
                                                pricing, state rules or per-
                                                invoice rate override

  Unpaid orders hold domain names     Deferred  No automatic order expiry yet     Phase 08/17
  until cancelled

  Terms-of-service acceptance at      Deferred  No ToS page/versioning yet        Phase 15
  checkout

  Web cart needs exactly one client;  Deferred  API takes client_id; staff "Add   Phase 09/15
  no client chooser                             Order" screen is Phase 09
  -------------------------------------------------------------------------------------------

**Rule:** If an item is already recorded here, do not rediscover or
re-plan it unless its status changes.

------------------------------------------------------------------------

# 27 --- ARCHITECTURE DECISION LOG

Record permanent technical decisions here.

  -----------------------------------------------------------------------------------
  Decision                            Reason                  Status
  ----------------------------------- ----------------------- -----------------------
  Django is primary backend           Strong                  Active
                                      relational/business     
                                      application framework   

  Django REST Framework is API layer  Mobile/API readiness    Active
                                      without unnecessary     
                                      second backend          
                                      framework               

  PostgreSQL is primary database      Transactional           Active
                                      billing/service data    

  Redis + Celery for async work       Provisioning,           Active
                                      notifications,          
                                      scheduled operations    

  Provider adapters                   Avoid provider lock-in  Active

  Order/Invoice/Transaction/Service   Different business      Active
  remain separate                     lifecycles              

  API-first business architecture     Web and future mobile   Active
                                      clients share business  
                                      logic                   

  cPanel/WHM is external integration  Hosting portal manages  Active
                                      business; WHM manages   
                                      server hosting accounts 

  Single role per user; roles are     One permission system;  Active
  Django Groups synced from           easy to audit           
  apps.accounts.roles                                         

  Portal permissions live on          Permission checks do    Active
  accounts.PortalAccess (view_/       not depend on future    
  manage_<area>)                      module models           

  JWT (simplejwt) for API/mobile,     Mobile readiness;       Active
  sessions for web; JWT revoked on    immediate revocation    
  password change/suspension                                  

  Email provider is a DB record       "No provider            Active
  with encrypted credentials          credentials in ENV"     

  Provider credentials encrypted      Reusable for WHM,       Active
  with Fernet (apps.core.crypto),     registrar, payment      
  key from CREDENTIALS_ENCRYPTION_KEY                         

  Emails recorded first, delivered    Retry-safe, auditable   Active
  by Celery after commit              delivery                

  Product/Addon share one            Avoids two visibility    Active
  CatalogStatus and one PriceEntry   vocabularies and two     
  shape (ProductPrice/AddonPrice)    pricing calculators      

  Public catalog API/pages are one   Same visibility rule     Active
  view with staff-vs-public shape,   (staff see all, others   
  not two separate implementations   see active) as Phase 02  

  Server model created now, minimal  Phase 05 extends the     Active
  (no credentials); domain-as-       same model rather than   
  product excluded from Product      creating a second one;   
  entirely                           domains get their own    
                                     TLD-keyed pricing model  

  Business records belong to Client;  Agencies/companies have Active
  users reach clients through         several users; one user 
  ClientContact (owner/billing/       can manage several      
  technical)                          accounts                

  Staff accounts cannot be client     Keeps staff and         Active
  contacts                            customer access apart   

  Self-registration creates a Client  Every customer can      Active
  owned by the new user               order immediately       

  Domain registration/transfer are    Works without Cart/      Active
  request-then-complete (contact      Checkout or Billing      
  requests, staff completes)          existing yet             

  Registrar adapter interface with a  Registrar-specific code  Active
  Manual (local-only, no real         confined to one layer;   
  registration) adapter shipped       real registrar swaps in  
                                     without touching services 

  Self-service domain actions open    None are financial;      Active
  to any client contact (owner/       technical contacts exist 
  billing/technical), not just        specifically for this    
  owner/billing                                                

  Domain-specific TLD pricing model   Register/renew/transfer  Active
  (TldPricing), separate from         pricing keyed by TLD     
  apps.products.Product               does not fit the         
                                     billing-cycle price shape 

  Server model extended (not          Explicit Phase 03         Active
  duplicated) with WHM connection     instruction; one place    
  fields for Phase 05                 for server records        

  Real WHM API adapter written        WHM is one stable,        Active
  against public docs; domain          documented protocol      
  registrars got Manual-only          unlike registrars         

  cPanel account passwords are        "Safe credentials" taken  Active
  generated per-provisioning, emailed literally: nothing to     
  once, never stored in our DB        leak from our own DB      

  Hosting self-service limited to     Other actions are either  Active
  view + request; no customer         operationally sensitive   
  suspend/terminate/change-package    or imply a billing change 

  A cart stores selections, never     Roadmap section 23:       Active
  prices; one pricing engine          never trust browser       
  (orders.pricing) computes every     prices/totals; one place  
  figure, orders snapshot it          for billing calculations  

  Phase 06 ends at an Order awaiting  Follows the roadmap's own Active
  payment; payments/invoices are      phase definitions (07 =   
  Phase 07, lifecycle is Phase 09     invoices/transactions)    

  apps.billing holds configuration    Phase 07 extends it, no   Active
  (payment methods, tax, coupons);    second billing app;       
  apps.orders holds carts and orders  transactional vs rules    

  Payment methods (customer-facing)   Gateways are live-         Active
  are separate from payment gateways  configured providers      
  (attached in Phase 07)              (final roadmap principle) 

  Order created once with the full    Phases 07/09 add          Active
  lifecycle vocabulary; Phase 06      transitions to this model,
  uses pending_payment/cancelled only never a second Order      

  Checkout recomputes under row       Correct money, no double   Active
  locks, is atomic and double-submit  redemption of a coupon,   
  safe; unpaid orders reserve domain  no name sold twice        
  names until paid or cancelled                                 

  One billing arithmetic              Roadmap rule: no duplicate  Active
  (billing.calculations): cart,       billing calculations;
  orders, invoices, quotes; discount  documents must sum exactly
  and tax allocated to lines in cents 

  Invoice paid/refunded amounts and   Financial figures must be   Active
  status are derived from succeeded   reproducible from permanent
  transactions under the invoice row  records (verify_billing)
  lock; "overdue" is derived, never   
  stored                              

  Issued invoices are immutable       Legal/financial integrity;  Active
  (pay, refund, cancel only); numbers gap-free numbering allocated
  are gap-free, taken at issue        at issue under a row lock

  Payments succeed only via a signed  Never trust the browser     Active
  gateway webhook (idempotent by      redirect; redeliveries and
  event id, amount/currency checked); retries must be safe
  invoice order: invoice -> tx ->     
  order lock order                    

  PaymentProvider is DB-configured    Provider credentials never  Active
  and admin-only; test gateway is     in ENV; a no-money gateway
  behind ALLOW_TEST_PAYMENT_GATEWAY   must never run in production

  Renewals/upgrades are invoices      One financial record; a     Active
  (apps.renewals): figures frozen on  browser never supplies a
  a ServiceChange at invoicing time,  price, credit or expiry;
  applied once when paid; a failed    the payment is never lost
  apply leaves the payment and a      to a service-side failure
  retryable change

  Upgrade credit = paid x days left / Roadmap rules: credit       Active
  term days, capped at what was paid, never exceeds valid paid
  none once expired; shown on the     value; expired plans get
  invoice as a discount line          none; invoice shows it
  -----------------------------------------------------------------------------------

------------------------------------------------------------------------

# 28 --- CURRENT STARTING TASK

## Start: Phase 01 --- Foundation & Architecture

Do **not** immediately start coding.

First perform a complete audit of the current project.

### Audit Checklist

1.  Project/repository structure
2.  Existing Django apps
3.  Existing models
4.  Existing authentication
5.  Existing users
6.  Existing roles
7.  Existing permissions
8.  Existing REST/API layer
9.  Existing serializers/viewsets/services
10. Existing billing/order functionality
11. Existing provider integrations
12. Existing notification/email system
13. Existing Celery/Redis setup
14. Existing audit/logging
15. Existing tests
16. Existing deployment configuration
17. Existing environment configuration
18. Existing documentation

### Required First Deliverable

Produce a **Phase 01 Gap Report** with:

``` text
FOUND
-----
What already exists and works.

PARTIAL
-------
What exists but is incomplete.

BROKEN
------
What exists but fails.

MISSING
-------
What genuinely needs implementation.

REUSE
-----
Existing components that should be reused.

CHANGES
-------
Exact Phase 01 changes required.

TEST PLAN
---------
Tests required to verify the changes.

PHASE 01 EXIT CRITERIA
----------------------
What must be true before Phase 01 is marked complete.
```

### Critical Instruction

**Do not implement anything before completing this audit.**

After the audit:

1.  Implement only genuine gaps.
2.  Reuse existing systems.
3.  Add tests.
4.  Verify UI/API where applicable.
5.  Update the Phase Status.
6.  Update Known Work if something is deferred.
7.  Update Architecture Decision Log if a permanent decision is made.
8.  Commit only after verification.
9.  Do not deploy unless deployment is explicitly requested/approved.

------------------------------------------------------------------------

# 29 --- FINAL PROJECT PRINCIPLE

> **Build once. Verify once. Document once. Reuse thereafter.**
>
> **Providers are live configurable connections. Do not hardcode provider credentials or require provider-specific ENV variables.**
>
> The project must continuously move forward from its current state.
>
> No future session should restart a completed phase, recreate an
> existing feature, or repeat an already-verified implementation without
> a documented reason.
