# WHPanel Next-Generation Hosting Platform Roadmap

**Purpose:** Claude development handoff / master implementation roadmap\
**Project:** WHPanel\
**Existing platform:** Django + Django REST Framework\
**Primary goal:** Preserve the completed WHPanel business platform while
progressively eliminating WHMCS and cPanel dependency.\
**Version:** 2.1 (12 October 2026) --- adds sections 20--25: three-status
rule, V track (real-world verification), N11--N13, P track (product
features) and the updated order. Sections 00--19 are unchanged except
where marked **[v2]**. v2.1 corrects v2: Milestone C and D acceptance
tests name the right phases (N11, N12, V2, V4--V6), the push/deploy rule
is explicit, and V0 and the P track record what the repository already
has.

------------------------------------------------------------------------

# 00 --- READ THIS FIRST

This roadmap is an **extension of the existing WHPanel project**, not a
rebuild.

## Final Product Goal

``` text
                         WHPanel
                            |
             +--------------+--------------+
             |              |              |
        Business Plane  Hosting Plane  Infrastructure Plane
```

Final dependency target:

``` text
WHMCS Dependency  -> ZERO
cPanel Dependency -> ZERO
```

cPanel/WHM is transitional. WHPanel must first hide it behind a
provider-neutral hosting-control layer, then introduce native
infrastructure, migrate existing accounts, and finally retire cPanel.

## Permanent Principles

1.  Do not rebuild completed WHPanel business functionality.
2.  Continue the existing Django + DRF architecture.
3.  WHPanel is the customer's primary interface.
4.  Customer hosting UX should be simple and modern, similar in
    philosophy to Hostinger/hPanel.
5.  cPanel is a temporary provider, not the permanent architecture.
6.  `Open cPanel` may temporarily exist as an advanced escape hatch.
7.  Customer-facing services/APIs must be provider-neutral.
8.  cPanel-specific code stays inside the cPanel adapter.
9.  Customer and staff use the same hosting-control engine with
    different permissions.
10. Super Admin can go directly from Client -\> Service -\> Manage
    Hosting without the customer's password.
11. Administrative hosting actions are audited.
12. Native servers eventually use a secure WHPanel Agent.
13. Native infrastructure must be production-verified before cPanel
    accounts are migrated.
14. cPanel is removed only after migration, rollback, backup/restore,
    monitoring and security validation.

------------------------------------------------------------------------

# 01 --- THREE PRODUCT PLANES

## Business Plane

Preserve the existing implementation for:

-   Clients and contacts
-   Products and pricing
-   Orders
-   Billing, invoices and transactions
-   Domains
-   Renewals/upgrades
-   Support
-   Affiliates
-   Reports
-   Notifications
-   Staff permissions
-   Audit logs

## Hosting Control Plane

Add:

-   Websites
-   Files
-   Databases
-   Email
-   Hosting domains
-   DNS
-   SSL
-   PHP/runtime settings
-   Cron jobs
-   Backups
-   Security
-   Usage/logs

## Infrastructure Plane

Eventually manage:

-   Servers/nodes
-   WHPanel Agents
-   IP addresses
-   Web/PHP runtime
-   Database services
-   DNS
-   Mail
-   Storage
-   Backups
-   Resource isolation
-   Monitoring
-   Maintenance and updates

------------------------------------------------------------------------

# 02 --- TRANSITION ARCHITECTURE

``` text
Customer / Staff
       |
       v
    WHPanel UI
       |
       v
Django / DRF
       |
       v
Hosting Control Services
       |
       v
Hosting Provider Interface
       |
       +----------------------+----------------------+
       |                                             |
 CPanelProvider                               NativeProvider
 Transitional                                Long-term
       |                                             |
 WHM / cPanel APIs                              WH Agent
```

The UI must not care which provider operates the hosting account.

``` text
Hosting Service
      |
      v
Server / Node
      |
      v
Provider
```

Example:

``` text
UK-CP-01      -> cPanel
UK-CP-02      -> cPanel
UK-NATIVE-01  -> WH Native
DE-NATIVE-01  -> WH Native
```

------------------------------------------------------------------------

# 03 --- PROVIDER-NEUTRAL API STANDARD

Never design the public/customer API around cPanel names.

Avoid:

``` text
/api/cpanel/create-email
/api/cpanel/create-database
/api/cpanel/change-php
```

Use:

``` text
/api/v1/hosting/services/{id}/files/
/api/v1/hosting/services/{id}/databases/
/api/v1/hosting/services/{id}/emails/
/api/v1/hosting/services/{id}/domains/
/api/v1/hosting/services/{id}/dns/
/api/v1/hosting/services/{id}/ssl/
/api/v1/hosting/services/{id}/php/
/api/v1/hosting/services/{id}/cron/
/api/v1/hosting/services/{id}/backups/
/api/v1/hosting/services/{id}/usage/
```

Internally:

``` text
Hosting Service
      |
      v
Provider-neutral service
      |
      +--> CPanelProvider
      |
      +--> NativeProvider
```

------------------------------------------------------------------------

# 04 --- CUSTOMER EXPERIENCE TARGET

Main navigation:

``` text
Home
Websites
Domains
Email
Billing
Support
Account
```

Example website card:

``` text
example.com
Business Hosting
Active

Storage       4.8 / 20 GB
Bandwidth     38 / 100 GB
SSL           Active
PHP           8.3

[ Manage ]
```

Manage screen:

``` text
example.com

Overview     Performance     Security

FILES
File Manager
Backups
FTP / SFTP

DATABASES
MySQL Databases
Database Users
phpMyAdmin

EMAIL
Email Accounts
Forwarders
Autoresponders

DOMAINS
Domains
Subdomains
Redirects
DNS

SECURITY
SSL
Malware Protection
IP Controls

ADVANCED
PHP
Cron Jobs
Error Logs
Access Logs
```

The customer should not normally need to know whether the underlying
account is cPanel-backed or native.

------------------------------------------------------------------------

# 05 --- STAFF / SUPER ADMIN EXPERIENCE

Required path:

``` text
Clients
   -> Ali Khan
      -> Services
         -> example.com
            -> Manage Hosting
```

Admin context:

``` text
ADMIN ACCESS

Managing: example.com
Client: Ali Khan
Service: Business Hosting
Node: UK-NODE-02

[ Client Profile ] [ Service Details ] [ Exit Admin Access ]
```

No customer password is required.

Every sensitive action records:

``` text
Actor
Client
Hosting Service
Action
Old Value
New Value
IP
Timestamp
Result
```

## Access Model

### Customer

Own services: files, databases, email, DNS, SSL, backups, PHP, cron,
usage.

### Technical Staff

Authorized hosting operations: files, databases, DNS, SSL, PHP, logs,
backups, diagnostics.

### Admin

Clients, hosting services, domains, provisioning, packages, usage,
suspensions and limited infrastructure information.

### Super Admin

Full hosting administration plus nodes, agents, IPs, infrastructure
services, backups, queues, monitoring, security, provisioning logs and
maintenance.

------------------------------------------------------------------------

# 06 --- H TRACK: HOSTING CONTROL PLANE

## H0 --- Hosting Control Architecture & Provider Abstraction

Before coding, inspect the existing repository under the project's Rule
1.

Inspect:

-   `apps.hosting`
-   `HostingProvider`
-   Server models
-   encrypted credentials
-   provisioning services
-   hosting permissions
-   APIs
-   WHM adapter
-   hosting UI
-   audit services
-   tests

Define provider-neutral capabilities:

``` text
Account
Files
Databases
Email
Domains
DNS
SSL
PHP
Cron
Backups
Usage
```

Providers:

``` text
HostingProvider
   +-- CPanelProvider
   +-- NativeProvider
```

**Exit criterion:** UI and core hosting services do not need to know
whether an account is cPanel or native.

------------------------------------------------------------------------

## H1 --- Websites & Hosting Dashboard

Build the approved modern white/blue SaaS-style hosting workspace.

Include:

-   website/service list
-   hosting status
-   plan
-   primary domain
-   disk usage
-   bandwidth
-   SSL state
-   PHP version
-   quick actions
-   Manage Hosting

------------------------------------------------------------------------

## H2 --- File Manager

WHPanel-native UI for:

-   browse
-   upload/download
-   create file/folder
-   rename
-   copy/move
-   delete
-   text editing
-   permissions
-   archive/extract
-   search

Security:

-   strict hosting-root isolation
-   path traversal protection
-   authorization per operation
-   upload validation
-   size limits
-   audit sensitive actions
-   no arbitrary privileged shell execution

------------------------------------------------------------------------

## H3 --- Database Management

Support:

-   list/create/delete databases
-   database users
-   password changes
-   user/database assignment
-   privileges
-   usage
-   phpMyAdmin or equivalent secure access

Plan limits remain server-side truth.

------------------------------------------------------------------------

## H4 --- Email Management

Support:

-   mailboxes
-   quotas
-   password changes
-   forwarders
-   autoresponders
-   routing
-   spam controls
-   usage
-   webmail access

The same UI contract must later work with native mail.

------------------------------------------------------------------------

## H5 --- Domain & DNS Management

Support:

-   attached domains
-   subdomains
-   aliases
-   redirects
-   DNS zones

Common DNS records:

``` text
A
AAAA
CNAME
MX
TXT
SRV
CAA
```

Sensitive changes require confirmation and audit.

------------------------------------------------------------------------

## H6 --- SSL Management

Support:

-   certificate status
-   issuer
-   expiry
-   auto-renew
-   HTTPS redirect
-   renewal/reissue where applicable

Native infrastructure later automates certificate lifecycle.

------------------------------------------------------------------------

## H7 --- PHP & Runtime Management

Customer-friendly controls:

-   PHP version
-   memory limit
-   upload limit
-   execution time
-   allowed extensions/settings
-   error-display controls where permitted

Do not expose unrestricted raw server configuration.

------------------------------------------------------------------------

## H8 --- Cron & Advanced Tools

Support:

-   create/edit/delete cron jobs
-   enable/disable
-   schedule presets
-   command validation
-   error logs
-   access logs
-   FTP/SFTP accounts
-   redirects
-   IP controls

Cron must not become an unrestricted privileged command interface.

------------------------------------------------------------------------

## H9 --- Backup & Restore

Backup types:

-   website/files
-   database
-   email
-   full account

Requirements:

-   scheduled backups
-   manual backups where permitted
-   retention
-   off-server storage for native hosting
-   restore workflow
-   restore audit
-   integrity checks
-   tested restore

A successful backup job alone is not proof of recoverability.

------------------------------------------------------------------------

## H10 --- Security Center

Customer-facing controls/status:

-   SSL
-   malware findings
-   suspicious files
-   blocked IPs
-   SSH state
-   directory protection
-   security events
-   SSH keys where offered
-   file-integrity monitoring where offered

------------------------------------------------------------------------

## H11 --- Usage, Logs & Performance

Display:

``` text
Disk        7.2 / 20 GB
Bandwidth   38 / 100 GB
Databases   3 / 10
Email       8 / 50
Websites    2 / 5
```

Also include historical usage, warnings and plan limits. Limits are
enforced server-side.

------------------------------------------------------------------------

## H12 --- Client -\> Hosting Administration

Implement:

``` text
Client -> Services -> Manage Hosting
```

Requirements:

-   no customer password
-   visible Admin Access context
-   role-based actions
-   customer/service identity always visible
-   return links to client/service
-   complete audit trail
-   same hosting engine as customer area

------------------------------------------------------------------------

## H13 --- Infrastructure Dashboard

Authorized UI should eventually show:

-   node count/health
-   hostname
-   region
-   provider
-   account count
-   CPU
-   RAM
-   disk
-   load
-   network
-   web service
-   database service
-   mail
-   DNS
-   agent
-   backups

For cPanel nodes, native-only fields can be unavailable rather than
simulated.

------------------------------------------------------------------------

## H14 --- Complete Transitional cPanel Adapter

Expand the current WHM/cPanel adapter to satisfy provider-neutral
contracts for:

-   account operations
-   files
-   databases
-   email
-   domains
-   DNS
-   SSL
-   PHP
-   cron
-   usage
-   backups

No cPanel-specific logic should leak into generic hosting services or
customer UI.

------------------------------------------------------------------------

## H15 --- Temporary cPanel Escape Hatch

Temporary advanced option:

``` text
Advanced Tools
[ Open cPanel ]
```

Requirements:

-   secure temporary/session login where supported
-   authorization before session creation
-   never expose WHM token
-   do not display cPanel password
-   audit access requests
-   clearly treat as transitional architecture

Remove this when cPanel is retired.

------------------------------------------------------------------------

# 07 --- N TRACK: NATIVE INFRASTRUCTURE

## N0 --- Native Architecture Research & Security Design

Do not silently choose infrastructure technologies.

Explicitly evaluate:

1.  web server
2.  PHP process/runtime model
3.  tenant isolation
4.  Linux account/container model
5.  database topology
6.  mail topology/software
7.  DNS architecture
8.  SSL/ACME
9.  SFTP/FTP
10. backup storage/retention
11. resource enforcement
12. firewall/network model
13. monitoring
14. OS/server updates
15. agent language/runtime
16. node authentication
17. secret rotation
18. disaster recovery
19. cPanel migration compatibility
20. supported Linux distributions

Evaluate security, isolation, operational complexity, performance,
compatibility, maintainability and recovery.

Produce architecture decision records before production native hosting.

------------------------------------------------------------------------

## N1 --- WH Agent

Native nodes use a secure WHPanel Agent.

``` text
WHPanel
   |
   v
WH Agent
   |
   +-- Website Manager
   +-- PHP Manager
   +-- Database Manager
   +-- SSL Manager
   +-- DNS Manager
   +-- Mail Manager
   +-- Backup Manager
   +-- Metrics Agent
```

**Critical rule:** Django must never use the agent as an arbitrary
remote shell.

Use allowlisted structured commands such as:

``` text
CREATE_WEBSITE
DELETE_WEBSITE
CREATE_DATABASE
CHANGE_PHP_VERSION
ISSUE_SSL
CREATE_MAILBOX
CREATE_BACKUP
RESTORE_BACKUP
```

Requirements:

-   strong node identity
-   encrypted/authenticated transport
-   replay protection
-   authorization
-   idempotency
-   correlation IDs
-   timeouts
-   structured responses
-   audit
-   version compatibility
-   safe upgrades

------------------------------------------------------------------------

## N2 --- Native Node Enrollment

Flow:

``` text
Fresh Linux Server
       |
Install WH Agent
       |
One-Time Enrollment Token
       |
Mutual Authentication
       |
WHPanel
```

Store:

-   hostname
-   region
-   IPs
-   CPU
-   RAM
-   storage
-   agent version
-   capabilities
-   health
-   capacity

------------------------------------------------------------------------

## N3 --- Native Website Provisioning

``` text
Paid Order
   |
WHPanel
   |
Select Native Node
   |
Provisioning Job
   |
WH Agent
   |
Create Isolated Hosting Environment
   |
   +-- Web Runtime
   +-- PHP
   +-- Storage
   +-- SSL
   +-- DNS
   |
ACTIVE
```

**Milestone:** WHPanel can provision and manage a website on a native
node without cPanel.

------------------------------------------------------------------------

## N4 --- Native Database Platform

Implement native lifecycle for:

-   databases
-   users
-   credentials
-   privileges
-   quotas/usage
-   backups
-   restores

H3 UI must work unchanged through `NativeProvider`.

------------------------------------------------------------------------

## N5 --- Native DNS & SSL

Automate:

``` text
Domain -> DNS -> Validation -> Certificate -> HTTPS -> Auto Renewal
```

H5/H6 customer workflows remain unchanged.

------------------------------------------------------------------------

## N6 --- Native Files & SFTP

H2 must transparently support:

``` text
File Manager
   +--> CPanelProvider
   +--> NativeProvider -> WH Agent
```

Implement SFTP/FTP according to N0 architecture decisions.

------------------------------------------------------------------------

## N7 --- Native Mail Platform

Mail is a high-complexity subsystem.

Required capabilities:

-   SMTP
-   IMAP
-   mailboxes
-   webmail
-   forwarders
-   aliases
-   spam filtering
-   SPF
-   DKIM
-   DMARC
-   quotas
-   queues
-   reputation monitoring
-   abuse controls

Evaluate whether mail should be separated from web-hosting nodes.

------------------------------------------------------------------------

## N8 --- Native Backup Platform

``` text
Native Node
    |
Encrypted Backup
    |
Remote Backup Storage
```

Back up:

-   websites
-   databases
-   email where hosted
-   recovery configuration

Require encryption, retention, integrity checks, restore tests, access
controls and disaster-recovery procedures.

------------------------------------------------------------------------

## N9 --- Resource Isolation & Limits
                                                                  
Production shared hosting must enforce:

-   CPU
-   RAM                 
-   disk
-   processes
-   I/O
-   bandwidth
-   database usage
-   email usage
-   inodes
-   runtime/process isolation

One compromised/noisy tenant must not materially affect unrelated
tenants.

This is a production-security milestone.
                                                            
------------------------------------------------------------------------

## N10 --- Monitoring & Controlled Self-Healing

Monitor:

-   node/agent health
-   CPU/RAM/disk/load
-   network
-   web service
-   database
-   DNS
-   mail
-   backups
-   certificates
-   queues

States:

``` text
Healthy
Warning
Critical
Offline
```

Only bounded, proven-safe operations may self-heal automatically.

------------------------------------------------------------------------

# 08 --- M TRACK: MIGRATION & CPANEL RETIREMENT

## M0 --- Dual Provider Period

Operate both providers:

``` text
WHPanel
   +-- UK-CP-01       cPanel
   +-- UK-CP-02       cPanel
   +-- UK-NATIVE-01   Native
   +-- DE-NATIVE-01   Native
```

The customer UI remains identical.

This validates provider abstraction in real usage.

------------------------------------------------------------------------

## M1 --- Native-First Provisioning

Once native infrastructure is production-ready:

``` text
New Order -> Native Infrastructure
```

Existing cPanel accounts remain operational temporarily.

Target:

``` text
New cPanel Accounts = 0
```

------------------------------------------------------------------------

## M2 --- cPanel Migration Engine

``` text
cPanel Account
      |
Discovery
      |
Pre-flight Check
      |
      +-- Files
      +-- Databases
      +-- Email
      +-- DNS
      +-- Cron
      +-- SSL
      +-- Configuration
      |
Native Node
      |
Validation
      |
DNS Cutover
      |
Monitoring
      |
Complete
```

Every migration needs a rollback strategy.

------------------------------------------------------------------------

## M3 --- Migration Dashboard

Example:

``` text
cPanel Migration

Total Accounts        843
Migrated              620
Pending               180
Failed                 12
Manual Review          31
```

Per account track:

-   discovery
-   compatibility
-   backup
-   transfer
-   validation
-   cutover
-   rollback window
-   completion
-   failures/manual review

------------------------------------------------------------------------

## M4 --- cPanel Retirement

Do not retire cPanel until:

-   required customer features work natively
-   native provisioning is verified
-   isolation is verified
-   backups are verified
-   restores are verified
-   monitoring is verified
-   security audit passes
-   migrations are validated
-   rollback procedures are tested
-   no required production account depends on cPanel
-   operations team can support the native stack

Then:

``` text
CPanelProvider  -> Removed
Open cPanel     -> Removed
WHM Credentials -> Removed
cPanel Servers  -> Migrated / Decommissioned
```

Final:

``` text
cPanel Dependency = ZERO
```

------------------------------------------------------------------------

# 09 --- WHMCS INDEPENDENCE

The existing WHPanel business modules already replace much of WHMCS's
role.

Future product terminology should gradually move from:

``` text
WHMCS Parity
```

to:

``` text
WHPanel Product Standard
```

Do not rebuild working functionality merely to rename the design target.

Final identity:

``` text
WHPanel
   = Business Management
   + Hosting Management
   + Infrastructure Management
```

No future architecture should introduce a WHMCS runtime/API dependency.

``` text
WHMCS Dependency = ZERO
```

------------------------------------------------------------------------

# 10 --- MAJOR MILESTONES

## Milestone A --- WHPanel Experience Complete

Normal customers perform routine hosting management entirely in WHPanel:

-   websites
-   files
-   databases
-   email
-   domains/DNS
-   SSL
-   PHP
-   cron
-   backups
-   security
-   usage

cPanel may still exist underneath. `Open cPanel` is only a temporary
advanced fallback.

## Milestone B --- Native Website Hosting

A fresh supported Linux node can host a WHPanel-provisioned website
without cPanel, including the minimum required isolated runtime, files,
database, DNS, SSL, monitoring and backup.

## Milestone C --- Native Platform Production Ready

Native infrastructure has:

-   tenant isolation
-   enforceable limits
-   monitoring
-   backup and tested restore
-   operational tooling
-   security validation
-   reliable provisioning
-   required mail strategy
-   disaster recovery

New accounts can safely default to `NativeProvider`.

## Milestone D --- cPanel Retirement

All required accounts/features are migrated and verified.

``` text
WHMCS = 0 dependency
cPanel = 0 dependency
```

------------------------------------------------------------------------

## [v2] Milestone acceptance tests

A milestone is reached only when its acceptance test has run on real
infrastructure and the evidence is recorded (date, node, commit, result).

  -------------------------------------------------------------------------
  Milestone   Acceptance test (real, not simulated)
  ----------- -------------------------------------------------------------
  A           On a live WHM server: every H-phase action works from
              WHPanel for one test account; "Open cPanel" opens a
              session without exposing a token (V3).

  B           Fresh AlmaLinux node: installer -> enrol -> order -> website
              live over HTTPS with PHP, one database, DNS, backup and
              monitoring green. Repeat on a second fresh node (V2).

  C           V2 and V4--V6 passed: isolation probe + load test, restore
              from real remote storage, mail delivers to Gmail/Outlook
              inbox with SPF/DKIM/DMARC pass, external security review
              closed. **N11 is Proven**: a security fix reaches a node
              through the signed update path without SSH.

  D           All production accounts migrated, one rollback rehearsed
              for real, M4 checklist green with named sign-offs.
              **N12 is Proven**, so accounts with several domains were
              migrated, not left behind.
  -------------------------------------------------------------------------

------------------------------------------------------------------------

# 11 --- IMPLEMENTATION ORDER

``` text
Existing WHPanel
      |
H0  Provider Architecture
      |
H1  Hosting Dashboard
      |
H2  Files
      |
H3  Databases
      |
H4  Email
      |
H5  Domains / DNS
      |
H6  SSL
      |
H7  PHP
      |
H8  Cron / Advanced
      |
H9  Backups
      |
H10 Security
      |
H11 Usage
      |
H12 Admin Hosting Access
      |
H13 Infrastructure UI
      |
H14 Complete cPanel Adapter
      |
H15 Temporary cPanel Escape Hatch
      |
      +---- MILESTONE A
      |
N0  Native Architecture Research
      |
N1  WH Agent
      |
N2  Node Enrollment
      |
N3  Native Website Provisioning
      |
      +---- MILESTONE B
      |
N4  Native Database
      |
N5  Native DNS / SSL
      |
N6  Native Files / SFTP
      |
N7  Native Email
      |
N8  Native Backups
      |
N9  Isolation / Limits
      |
N10 Monitoring
      |
      +---- MILESTONE C
      |
M0  Dual Provider
      |
M1  Native First
      |
M2  Migration Engine
      |
M3  Account Migration
      |
M4  cPanel Retirement
      |
      +---- MILESTONE D
```

------------------------------------------------------------------------

# 12 --- CLAUDE DEVELOPMENT PROTOCOL

Claude must follow the existing project's inspection-first development
rules.

## Start of Every Task

1.  Read the existing master roadmap and architecture decisions.
2.  Read this roadmap.
3.  Identify exactly one current phase/slice.
4.  Inspect the repository before coding.
5.  Search existing models, services, adapters, APIs, URLs, templates
    and tests.
6.  Classify relevant functionality as complete, partial, broken or
    missing.
7.  Reuse existing implementation.
8.  Never recreate completed business functionality.

## During Implementation

-   preserve stable URL names/API contracts unless a documented
    migration requires change
-   keep provider code inside adapters
-   keep business logic out of templates
-   never expose WHM/cPanel/native root credentials to browsers
-   never trust client permissions, paths, limits or resource values
-   enforce object ownership server-side
-   enforce staff permissions server-side
-   audit sensitive operations
-   use background jobs for long-running operations
-   use idempotency for provisioning/external operations where
    appropriate
-   future agent accepts structured allowlisted operations, not
    arbitrary shell
-   preserve existing link-integrity standards
-   add tests with each capability

## End of Every Task

1.  Run relevant tests.
2.  Run authorization tests.
3.  Run link-integrity checks if UI/routes changed.
4.  Browser-test changed UI.
5.  Record implementation.
6.  Record remaining work.
7.  Update roadmap/state/deferred items.
8.  Do not mark complete until verified.
9.  Do not deploy without explicit owner approval.
10. **[v2]** Report every phase with the three-status rule (section 20):
    Built / Tested (simulated) / Proven (real). Never collapse them.
11. **[v2]** Commit after each slice. Pushing to the project's private
    remote repository is allowed once the owner has approved it for the
    work in hand; until then report "push pending" with the number of
    unpushed commits. **Deploying** to any server always needs explicit
    owner approval (item 9). Local-only work is a project risk, so a
    long-pending push must be raised, not left.
12. **[v2]** If a phase needs a real server, a live cPanel, a vendor or
    an owner decision, stop at the prepared state and list exactly what
    is needed, instead of simulating further.

------------------------------------------------------------------------

# 13 --- SECURITY NON-NEGOTIABLES

Moving away from cPanel means WHPanel assumes responsibilities
previously handled by a mature control-panel platform.

Mandatory:

-   least privilege
-   tenant isolation
-   encrypted secrets
-   secure node identity
-   authenticated agent communication
-   no arbitrary remote shell API
-   allowlisted structured agent operations
-   path isolation
-   strict authorization
-   rate limiting
-   session/API/CSRF protections as appropriate
-   audit logging
-   idempotent provisioning
-   safe retries
-   backup before destructive operations where appropriate
-   tested restore
-   monitoring
-   secure updates
-   rollback procedures
-   no secrets in logs
-   no WHM/root credentials in browser responses
-   no customer-password requirement for staff hosting administration

Native shared hosting is not production-ready until security and
isolation are proven.

------------------------------------------------------------------------

# 14 --- OPEN ARCHITECTURE DECISIONS

These belong to N0 and must not be silently decided during unrelated
implementation:

1.  Web server technology
2.  PHP runtime/process model
3.  Tenant isolation model
4.  Linux user/container strategy
5.  Database topology
6.  Mail architecture
7.  DNS architecture
8.  SSL/ACME implementation
9.  SFTP/FTP strategy
10. Backup storage and retention
11. Resource-limit technology
12. Firewall/network model
13. Monitoring stack
14. Server update strategy
15. WH Agent implementation language/runtime
16. Node/control-plane authentication
17. Secret distribution/rotation
18. Disaster recovery
19. cPanel migration compatibility
20. Supported Linux distributions/versions

------------------------------------------------------------------------

# 15 --- CURRENT STRATEGIC STATE

Until native hosting is ready:

``` text
Customer Experience -> WHPanel
Hosting Backend      -> cPanel where required
Advanced Fallback    -> Temporary Open cPanel
```

During migration:

``` text
WHPanel
   +-- CPanelProvider
   +-- NativeProvider
```

Final state:

``` text
Customer
   |
WHPanel
   |
NativeProvider
   |
WH Agent
   |
Native Infrastructure
```

The end product is an independent hosting platform, not a permanent
redesigned frontend on top of cPanel.

------------------------------------------------------------------------

# 16 --- FINAL PROJECT PRINCIPLE

> **WHPanel owns the customer experience, business logic and ultimately
> the hosting infrastructure control plane.**

> **cPanel is a migration dependency, not the final product.**

> **WHMCS is not part of the final runtime architecture.**

> **Provider abstraction must exist from the first hosting-control
> feature so the customer interface survives the cPanel-to-native
> migration unchanged.**

> **Native infrastructure is production-ready only after its security,
> isolation, backup, restore and operational model are explicitly
> designed and verified.**

``` text
                  WHPanel
                     |
          +----------+----------+
          |          |          |
       Business   Hosting   Infrastructure
        Plane      Plane        Plane
          |          |          |
          +----------+----------+
                     |
             WH Native Platform

WHMCS: 0 dependency
cPanel: 0 dependency
```

---

# 17 — UI/UX MASTER SPECIFICATION

This section is mandatory for all future WHPanel customer, staff, admin and infrastructure screens. The objective is a modern hosting-management experience with the usability standard of products such as hPanel, while retaining WHPanel's own visual identity and implementation.

## 17.1 Product Design Direction

```text
Style: Modern SaaS Hosting Platform
Character: Clean, premium, calm, fast and operational
Inspiration: Modern hosting dashboards and cloud consoles
Identity: WHPanel's own design; not a cPanel or WHMCS visual clone
Primary UX principle: Common tasks should be obvious without technical hosting knowledge
```

The interface must avoid the dense legacy-control-panel appearance. Advanced controls remain available, but the default customer experience should emphasize websites and outcomes rather than server terminology.

### Visual principles

- Generous whitespace and clear information hierarchy.
- White/light surfaces with restrained brand-blue emphasis by default.
- Dark mode may be supported through the existing theme system, but every screen must be designed intentionally for both themes if enabled.
- Rounded cards and controls with restrained shadows; avoid excessive decoration.
- One icon system throughout the product.
- Status always includes text, not colour alone.
- Primary actions are visually obvious; destructive actions are never primary-coloured.
- Technical IDs, IPs, paths and log values use a monospace treatment where useful.
- Avoid showing infrastructure jargon to customers unless it is needed for the task.

## 17.2 Design Tokens

Reuse the existing semantic-token architecture. Do not introduce page-specific literal colours.

Required semantic groups:

```text
--bg
--surface
--elevated
--text
--muted
--border
--primary
--primary-hover
--success
--warning
--danger
--info
--success-bg
--warning-bg
--danger-bg
--info-bg
```

Typography should use the project's existing modern sans-serif family. Maintain a predictable scale for small metadata, body copy, section headings, page titles and dashboard figures.

Spacing should use a consistent 4px/8px-derived scale. Cards, tables, forms, drawers and modals must use shared radius, border and shadow tokens.

## 17.3 Shared Application Shell

The hosting-control expansion should use a modern application shell while respecting the existing navigation registry and stable URL rules.

Target desktop concept:

```text
┌──────────────┬──────────────────────────────────────────────────────────┐
│              │ Search / Command                         🔔  Account ▼ │
│   WHPanel    ├──────────────────────────────────────────────────────────┤
│              │ Breadcrumbs                                               │
│ Dashboard    │ Page title                              Primary Action    │
│ Websites     │ Page description / context                                │
│ Domains      │                                                            │
│ Email        │ Main workspace                                             │
│ Billing      │                                                            │
│ Support      │                                                            │
│              │                                                            │
│──────────────│                                                            │
│ Settings     │                                                            │
└──────────────┴──────────────────────────────────────────────────────────┘
```

The exact navigation form must be reconciled with the current D8 header architecture before implementation. Do not create a second navigation system. If the existing header remains the product standard, apply this visual/interaction intent to that shell rather than reintroducing a sidebar without an explicit architecture decision.

### Global shell requirements

- Brand/logo area.
- Global or contextual search where authorized.
- Notification center.
- Account menu.
- Breadcrumbs below top-level screens.
- Clear page title and optional description.
- One obvious primary page action.
- Responsive navigation for mobile.
- Loading feedback for HTMX/async operations.
- No page-level horizontal overflow.

## 17.4 Customer Information Architecture

Primary customer navigation should converge toward:

```text
Home
Websites
Domains
Email
Billing
Support
Account
```

Existing routes and capabilities are reused. Navigation changes must be performed through the registry and must preserve old URLs/redirect requirements.

The customer's mental model is:

```text
I own a website/service
      ↓
I select it
      ↓
I manage everything related to it
```

not:

```text
I need to understand WHM/cPanel/server modules before I can manage my site
```

## 17.5 Customer Dashboard

The dashboard should answer four questions immediately:

1. Are my services healthy?
2. Is anything requiring attention?
3. What do I own?
4. What is the fastest route to the task I want?

Suggested structure:

```text
Welcome back, Client
Manage your websites, domains and account from one place.

[ Websites 4 ] [ Domains 6 ] [ Invoices 1 ] [ Tickets 2 ]

Needs your attention
┌──────────────────────────────────────────────────────────────┐
│ Invoice #1024 is due in 3 days                    [Pay/View] │
│ SSL for legacy.example.com requires attention     [Review]   │
└──────────────────────────────────────────────────────────────┘

Your websites                                      [View all]
┌───────────────────────┐  ┌───────────────────────┐
│ example.com           │  │ store.example.com     │
│ Business Hosting      │  │ Starter Hosting       │
│ ● Active              │  │ ● Active              │
│ Disk 24%  SSL Active  │  │ Disk 18%  SSL Active  │
│          [Manage]     │  │          [Manage]     │
└───────────────────────┘  └───────────────────────┘

Quick actions
[Create/Open Ticket] [Register Domain] [View Invoices] [Backups]
```

Do not overwhelm the customer dashboard with internal system-health data.

## 17.6 Websites Screen

Websites become the primary entry point into hosting management.

Each website/service card should show only useful summary information:

```text
example.com                                      ● Active
Business Hosting

Disk        4.8 / 20 GB
Bandwidth   38 / 100 GB
SSL         Active
PHP         8.3

[ Manage Website ]                  ⋯
```

The overflow menu may contain low-frequency actions such as service details, upgrade, cancellation request or other permitted actions.

Filters should support status and search when a client owns many services.

## 17.7 Manage Website Workspace

This is the central customer hosting-control screen.

Header:

```text
← Websites

example.com                                      ● Active
Business Hosting · UK Node

[Visit Website] [Open Admin*] [More ▾]
```

`Open Admin` refers to an application/site admin when a supported integration exists; do not confuse it with WHPanel staff administration.

Recommended information hierarchy:

```text
Overview   Performance   Security

USAGE / HEALTH                    QUICK ACTIONS
┌──────────────────────┐          ┌────────────────────────┐
│ Disk       24%       │          │ File Manager           │
│ Bandwidth  38%       │          │ Databases              │
│ SSL        Active    │          │ Email Accounts         │
│ PHP        8.3       │          │ Backups                │
└──────────────────────┘          └────────────────────────┘

Manage Hosting
┌─────────────┐ ┌─────────────┐ ┌─────────────┐
│ Files       │ │ Databases   │ │ Email       │
└─────────────┘ └─────────────┘ └─────────────┘
┌─────────────┐ ┌─────────────┐ ┌─────────────┐
│ Domains     │ │ DNS         │ │ SSL         │
└─────────────┘ └─────────────┘ └─────────────┘
┌─────────────┐ ┌─────────────┐ ┌─────────────┐
│ Backups     │ │ Security    │ │ PHP         │
└─────────────┘ └─────────────┘ └─────────────┘
┌─────────────┐ ┌─────────────┐ ┌─────────────┐
│ Cron Jobs   │ │ Logs        │ │ FTP / SFTP  │
└─────────────┘ └─────────────┘ └─────────────┘

Advanced Tools
[ Open cPanel ]   <- only while the transitional provider requires it
```

The `Open cPanel` control must not dominate the page. It is an escape hatch, not the main workflow.

## 17.8 File Manager UI

Desktop concept:

```text
File Manager                                      [Upload] [New ▾]

Path: / public_html / assets

┌──────────────────────┬───────────────────────────────────────────────┐
│ Folders              │ Name          Size       Modified      ⋯     │
│                      │                                               │
│ public_html          │ css/          —          Today         ⋯     │
│ logs                 │ index.php     12 KB      Today         ⋯     │
│ backups              │ .htaccess     2 KB       Yesterday     ⋯     │
│                      │                                               │
└──────────────────────┴───────────────────────────────────────────────┘
```

Requirements:

- breadcrumbs for current path
- searchable file list
- upload progress
- drag/drop where safe
- contextual row actions
- editor in modal/full workspace
- confirmation for destructive actions
- mobile-friendly list/card fallback
- errors displayed beside the attempted operation

## 17.9 Database UI

Main screen:

```text
Databases                                      [Create Database]

Database            Users       Size        Actions
site_prod           2           180 MB      Manage
wordpress_store     1           95 MB       Manage
```

Database detail should separate:

- database information
- assigned users
- privileges
- usage
- access tool
- destructive zone

Passwords are never displayed after creation/reset.

## 17.10 Email UI

```text
Email Accounts                                   [Create Account]

sales@example.com        1.2 / 5 GB     Active       [Manage]
support@example.com      0.8 / 5 GB     Active       [Manage]
```

Manage mailbox:

- quota
- reset password
- webmail
- forwarding
- autoresponder
- spam/security settings
- usage

Do not expose mail-server implementation details unless the user needs them for client configuration.

## 17.11 Domains & DNS UI

Domain overview cards/rows should show:

- domain
- status
- expiry
- auto-renew
- DNS state
- SSL state

DNS editor should use a clear structured table:

```text
Type   Name   Value / Target                  TTL       Actions
A      @      203.0.113.10                   Auto      Edit
MX     @      mail.example.com               Auto      Edit
TXT    @      v=spf1 ...                     Auto      Edit
```

Create/edit DNS should use a form tailored to the selected record type rather than one confusing generic form.

## 17.12 SSL UI

```text
SSL Certificate                                  ● Active

Domain          example.com
Issuer          Let's Encrypt
Valid until     14 Dec 2026
Auto renewal    Enabled
HTTPS redirect  Enabled

[Renew/Reissue if permitted] [Certificate Details]
```

Use clear warnings before expiry and actionable failure messages.

## 17.13 Backups UI

```text
Backups                                         [Create Backup]

Automatic Backups
Daily · 7 restore points                         ● Healthy

Restore Points
Today 02:10        Full Account        Ready     [Restore]
Yesterday 02:11    Full Account        Ready     [Restore]
```

Restore is a sensitive workflow:

1. choose restore point
2. show what will be replaced
3. confirmation
4. background job/progress
5. result
6. audit entry

## 17.14 Security Center UI

The customer should receive a security summary rather than raw infrastructure output.

```text
Security Score / Status

SSL                         Protected
Malware Scan                No threats found
SSH                         Disabled
Suspicious Activity         None detected
Backups                     Healthy

Recent Security Events
...
```

Warnings must link to a concrete remediation action when one exists.

## 17.15 Usage & Performance UI

Use cards and restrained charts for meaningful trends:

```text
Disk       7.2 / 20 GB
Bandwidth  38 / 100 GB
Inodes     42,120 / 250,000
Databases  3 / 10
Email      8 / 50
```

Warnings at defined thresholds should be consistent. Never use decorative charts when a number/progress bar communicates the state more clearly.

## 17.16 Client Billing UI

Maintain the existing financial truth and workflows but modernize presentation.

Invoices list:

```text
Invoices

#1024   05 Oct 2026   Due 12 Oct   £120.00   Unpaid    [View]
#1018   01 Sep 2026   Paid         £120.00   Paid      [View]
```

Invoice view should emphasize:

- amount/status
- due date
- payment instructions/actions
- invoice lines
- transaction history
- PDF/download/print

Do not mix infrastructure controls into billing pages.

## 17.17 Support UI

Ticket list should resemble a modern inbox rather than a legacy database table where possible.

```text
#4821  Website unavailable        Open        12 min ago
#4812  DNS question               Answered    Yesterday
```

Ticket detail:

- conversation thread
- clear staff/customer distinction
- attachments
- related service/domain context
- reply composer
- ticket status

## 17.18 Staff Dashboard

Staff dashboard is operational rather than customer-facing.

Suggested structure:

```text
Operations Overview

[ Pending Orders 12 ] [ Open Tickets 24 ] [ Unpaid £X ] [ Failures 3 ]

Needs Attention
- 3 provisioning failures
- 8 domains expire within 7 days
- 4 services awaiting manual action

Orders / Support / Billing panels

Infrastructure Summary
Healthy Nodes  8 / 9
Agent Issues   1
Backup Issues  0
```

Only show widgets the staff member is authorized to open.

## 17.19 Client 360° Staff View

This is one of the most important administrative screens.

```text
Ali Khan                                         ● Active
Client #1042 · Acme Ltd

[Add Order] [Create Invoice] [Open Ticket] [More ▾]

Summary  Services  Domains  Billing  Tickets  Emails  Activity

CLIENT SUMMARY                 ACCOUNT HEALTH
Contact details                Active services  4
Currency                       Unpaid invoices  1
Joined                         Open tickets     2

Hosting Services
example.com     Business Hosting     Active      [Manage Hosting]
store.com       Cloud Hosting        Active      [Manage Hosting]
```

The staff member must be able to reach hosting administration in one obvious click from the relevant service.

## 17.20 Staff Manage Hosting / Admin Access

When staff opens a customer's hosting service:

```text
ADMIN ACCESS
Managing hosting for Ali Khan / example.com

[Client Profile] [Service Details]                         [Exit]
```

This context remains visible while performing hosting operations.

The underlying hosting workspace should reuse the customer components wherever possible, with additional authorized admin actions. Do not build a completely separate hosting panel for staff.

## 17.21 Super Admin Infrastructure UI

Main infrastructure screen:

```text
Infrastructure                                    [Add/Enroll Node]

[ Nodes 9 ] [ Healthy 8 ] [ Warning 1 ] [ Accounts 843 ]

Node             Region   Provider    CPU   RAM   Disk   Health
UK-CP-01         UK       cPanel      32%   54%   68%    Healthy
UK-NATIVE-01     UK       Native      22%   41%   37%    Healthy
DE-NATIVE-01     DE       Native      81%   72%   65%    Warning
```

Node detail:

```text
UK-NATIVE-01                                  ● Healthy

Overview  Accounts  Services  Metrics  Backups  Events  Settings

CPU       22%
RAM       41%
Disk      37%
Load      1.24
Agent     Connected

Services
Web       Healthy
Database  Healthy
DNS       Healthy
Mail      Healthy / External / N/A
Backups   Healthy
```

Native-only operations must not be shown as fake controls for cPanel nodes.

## 17.22 Provisioning & Job UI

Long-running hosting operations must have visible states:

```text
Queued -> Running -> Completed
                  -> Failed -> Retry / Manual Review
```

Staff should be able to inspect:

- operation
- service/client
- provider/node
- start/end
- attempts
- safe error summary
- correlation ID
- retry availability

Never expose secrets in errors/log output.

## 17.23 Migration UI

Migration dashboard:

```text
cPanel Migration

[ Total 843 ] [ Migrated 620 ] [ Pending 180 ] [ Failed 12 ]

Account       Source       Target          Stage         Status
example.com   UK-CP-01     UK-NATIVE-01    Validation    Running
store.com     UK-CP-02     UK-NATIVE-02    Pre-flight    Ready
```

Account migration detail should show a timeline:

```text
✓ Discovery
✓ Compatibility Check
✓ Backup
✓ Files Transfer
✓ Database Transfer
✓ Email Transfer
● Validation
○ DNS Cutover
○ Monitoring
○ Complete
```

Rollback availability must be visible during the applicable window.

## 17.24 Responsive Design

Required target widths remain consistent with the existing project QA standards, including phone, tablet and desktop widths.

### Desktop

- full navigation
- multi-column cards where useful
- data tables
- side-by-side context panels

### Tablet

- reduced columns
- cards reflow
- tables use responsive strategy
- no essential action hidden behind hover

### Mobile

- navigation becomes drawer/menu according to the approved shell
- one-column content
- sticky/accessible primary actions where useful
- tables become stacked rows/cards when horizontal scrolling would harm usability
- modals may become full-height sheets
- touch targets remain comfortably sized

Never remove functionality merely because the user is on mobile.

## 17.25 Shared Component Library

Create/reuse shared components rather than page-specific markup:

- app navigation
- page header
- breadcrumbs
- stat/KPI card
- website/service card
- action card
- status badge
- alert
- progress/usage meter
- data table
- stacked mobile row
- tabs
- filter bar
- search field
- pagination
- form field
- select
- toggle
- password input
- modal
- confirmation dialog
- drawer/sheet
- toast
- empty state
- error state
- skeleton/loading state
- activity timeline
- job/progress indicator
- destructive-zone panel

## 17.26 Interaction States

Every interactive feature must define:

```text
Default
Hover
Focus
Disabled
Loading
Success
Validation Error
Server Error
Empty
Permission Denied
```

Never leave a button apparently active after the user has submitted a long-running operation without feedback.

HTMX/background actions should provide immediate acknowledgment and then expose job state where appropriate.

## 17.27 Empty States

Empty screens should explain what the area is and provide one useful action.

Good:

```text
No email accounts yet
Create an address such as info@example.com for this domain.

[Create Email Account]
```

Avoid blank tables or generic `No data` when a more useful explanation is possible.

## 17.28 Error UX

Errors should tell the user:

1. what failed
2. whether anything changed
3. what they can do next

Customer errors must not expose provider responses, server paths, stack traces, tokens or internal hostnames unnecessarily.

Staff may receive a safe diagnostic reference/correlation ID and can open the corresponding authorized operation log.

## 17.29 Confirmation & Destructive Actions

Confirmation is required for operations such as:

- delete database
- delete mailbox
- delete files/folders where recovery is uncertain
- restore backup
- destructive DNS replacement
- terminate hosting
- destructive infrastructure operation
- migration cutover/rollback where applicable

Confirmation dialogs must name the affected resource. High-impact actions should not rely on a generic `Are you sure?` message.

## 17.30 Loading & Perceived Performance

The interface must feel responsive even when provider APIs are slow.

Use:

- skeletons for initial async panels
- immediate button loading state
- optimistic UI only when rollback is safe
- background jobs for slow provider operations
- progress/status pages for provisioning, backup, restore and migration
- independently loaded dashboard widgets

A slow cPanel/native provider must not freeze the whole dashboard.

## 17.31 Accessibility

Minimum standard:

- WCAG 2.1 AA contrast
- visible keyboard focus
- keyboard-accessible navigation and dialogs
- labelled controls
- semantic headings
- status conveyed by text as well as colour
- accessible validation messages
- icons with accessible labels when they carry meaning
- no hover-only essential actions

## 17.32 Customer vs Staff vs Super Admin Visual Boundaries

Use one design system, but context must be unmistakable.

### Customer

Simple, outcome-focused, minimal infrastructure terminology.

### Staff/Admin

Operational data, client context, workflows and additional actions.

### Super Admin

Infrastructure, nodes, provider configuration, agents, queues, security and system operations.

Never expose Super Admin controls merely by hiding them with CSS/JavaScript. Authorization remains server-side.

## 17.33 cPanel Transition UX

During the transition:

```text
Normal task -> WHPanel native UI
Rare unsupported task -> Advanced Tools -> Open cPanel
```

Do not design:

```text
Manage Hosting -> giant Open cPanel button
```

The UI must actively reduce cPanel dependence by making WHPanel's own controls the easiest path.

When `NativeProvider` eventually replaces cPanel, customer navigation and normal workflows should remain substantially unchanged.

## 17.34 UI Implementation Rules for Claude

Before implementing any screen:

1. Inspect current template and route.
2. Inspect existing shared components.
3. Inspect the navigation registry.
4. Inspect current D8 shell before changing navigation.
5. Reuse the existing semantic design tokens.
6. Determine customer/staff/Super Admin permissions.
7. Identify loading, empty, success and error states.
8. Define phone/tablet/desktop behavior.
9. Build with shared components.
10. Run existing link crawler and template lint.
11. Browser-test relevant widths and themes.
12. Check console errors and keyboard interaction.

Do not create a parallel CSS framework, second navigation registry, duplicated component library or a separate staff hosting engine.

## 17.35 UI Completion Gate

A hosting UI phase is not complete until:

- [ ] correct permission model is enforced
- [ ] normal customer flow does not require cPanel
- [ ] customer/staff context is clear
- [ ] loading state exists
- [ ] empty state exists
- [ ] validation/server errors are designed
- [ ] destructive actions have meaningful confirmation
- [ ] responsive behavior verified
- [ ] keyboard/focus behavior verified
- [ ] no page-level overflow
- [ ] no browser console errors
- [ ] link crawler passes
- [ ] template lint passes
- [ ] no provider secret reaches the browser
- [ ] cPanel-specific terminology is absent from provider-neutral screens except the explicit transitional escape hatch

---

# 18 — UPDATED UI DELIVERY ORDER

The UI work runs alongside the hosting-control phases rather than being postponed until the backend is complete.

```text
UI0  Hosting Design System Audit / Extension
UI1  Application Shell Reconciliation with Current D8 Navigation
UI2  Customer Dashboard
UI3  Websites / Hosting Services
UI4  Manage Website Workspace
UI5  Files
UI6  Databases
UI7  Email
UI8  Domains / DNS
UI9  SSL
UI10 Backups
UI11 Security / Usage / Performance
UI12 Billing / Support Visual Alignment
UI13 Staff Dashboard
UI14 Client 360° + Manage Hosting
UI15 Super Admin Infrastructure
UI16 Jobs / Provisioning
UI17 Migration Dashboard
UI18 Final Responsive / Accessibility / Visual QA
```

Backend/control-plane work and UI should be paired where practical:

```text
H2 File capability       <-> UI5 File Manager
H3 Database capability   <-> UI6 Database UI
H4 Email capability      <-> UI7 Email UI
H5 DNS capability        <-> UI8 DNS UI
H6 SSL capability        <-> UI9 SSL UI
H9 Backup capability     <-> UI10 Backup UI
H12 Admin access         <-> UI14 Client 360° / Admin Access
H13 Infrastructure       <-> UI15 Infrastructure UI
M2/M3 Migration          <-> UI17 Migration Dashboard
```

This prevents a technically complete control plane from becoming another legacy-looking server panel.

---

# 19 — UPDATED PRODUCT EXPERIENCE PRINCIPLE

> **WHPanel should feel like one coherent modern hosting product, not WHMCS plus cPanel placed behind a new logo.**

The customer experience should remain stable while the underlying provider changes:

```text
TODAY
Customer -> WHPanel UI -> CPanelProvider -> cPanel

TRANSITION
Customer -> WHPanel UI -> Provider Layer -> cPanel / Native

FINAL
Customer -> WHPanel UI -> NativeProvider -> WH Agent -> Native Infrastructure
```

The UI is therefore part of the provider-independence architecture. If the customer interface is designed around cPanel terminology or cPanel page structure, cPanel dependency has not actually been removed.


------------------------------------------------------------------------

# 20 --- [v2] THREE-STATUS RULE

Every phase, report and status page uses three separate answers:

``` text
Built     code exists and is reviewed
Tested    automated tests pass (simulated node, demo provider, fake WHM)
Proven    ran successfully on real infrastructure, with recorded evidence
```

"Done" without qualification means **Proven**. A phase that is Built
and Tested but not Proven is reported as "code complete", never
"complete".

No phase after Milestone B may be called Proven until Milestone B is
Proven, because every later native phase depends on a real node.

Current state (12 October 2026): H0--H15, N0--N10, M0--M4 and native cron
are Built and Tested. Nothing native is Proven. Milestone A is code
complete, waiting on the live WHM check. Local tests ran on SQLite;
the first PostgreSQL run of the agent and migration apps happens in CI
once the repository is pushed (V0).

------------------------------------------------------------------------

# 21 --- [v2] V TRACK: REAL-WORLD VERIFICATION

The V track turns simulated work into proven work. It has priority over
new features.

## V0 --- Repository Safety & CI

Already in place (check before building): a remote `origin`, and
`.github/workflows/ci.yml` running the migration check, the production
deploy check, the full suite on PostgreSQL and Redis, and a Celery round
trip.

To do:

-   Push the unpushed commits to the private remote (owner approval,
    protocol item 11); protect `main`.
-   Extend the existing CI: install Go, run `go vet ./...` and
    `go test ./...` in `agent/`, so the Python<->Go golden parity tests
    run in CI instead of being skipped when Go is missing.
-   Secret scan in CI (and pre-commit); confirm nothing sensitive is in
    history.
-   First green CI on PostgreSQL for the agent and migration apps (they
    were only run on SQLite).
-   Tagged releases for the agent binary with checksums and signatures.

**Exit:** a red CI blocks merge; the repository exists off the
developer's machine; CI is green on PostgreSQL.

## V1 --- Environments & Release Process

-   Separate dev / staging / production settings and databases.
-   Staging WHPanel that talks to a staging node and a test WHM account.
-   Deployment runbook, database-migration rollback notes, feature flags
    for every native capability.
-   Error tracking and structured logs for WHPanel itself (no secrets).
-   Automated, encrypted, off-site backup of the WHPanel database and
    media, with a quarterly restore drill.

## V2 --- First Real Node (Milestone B)

-   Run the installer on a fresh AlmaLinux VPS; it must be idempotent.
-   Agent self-check command: lists required services, versions and
    failures.
-   Enrol, provision one website, verify HTTPS + PHP + database + DNS +
    backup + monitoring.
-   Record every fault found, fix it, repeat on a second fresh node.

**Exit:** Milestone B acceptance test passes twice.

## V3 --- Live cPanel Adapter (Milestone A)

-   One dedicated test account on a live WHM server.
-   Run every H-phase action and "Open cPanel" through WHPanel.
-   Record WHM/cPanel versions tested.

## V4 --- Isolation, Limits & Load

-   Run the cross-tenant probe on a real node with two tenants.
-   Load test: one noisy tenant must not materially affect another.
-   Verify CPU, RAM, I/O, process, inode and disk limits are enforced.

## V5 --- Backup, Restore & Disaster Recovery

-   Choose the storage vendor and object-lock policy (owner decision).
-   Real backup -> real remote storage -> restore to a different node.
-   Time the full DR runbook once, end to end.

## V6 --- Mail, Security Review & Migration Rehearsal

-   Mail node with TLS, reverse DNS, SPF/DKIM/DMARC; inbox placement test.
-   External security review / penetration test of WHPanel and the agent.
-   Import a real `pkgacct` archive, migrate it, then roll it back.

------------------------------------------------------------------------

# 22 --- [v2] NATIVE GAPS (N11--N13)

## N11 --- Agent Lifecycle & Updates

Resolves ADR 21 (currently Proposed).

-   Signed release manifest; agent verifies signature and checksum.
-   Version pinning per node; staged rollout (1 node -> 10% -> all).
-   Health check after update; automatic rollback to the previous binary.
-   Minimum supported agent version enforced by WHPanel (the 426 check
    already exists).
-   Super Admin UI: agent versions per node, rollout state, rollback.
-   The release signing key is never on a production server or on
    WHPanel, and differs from the key that signs commands.

**Exit:** a security fix reaches every node without SSH access.

## N12 --- Multiple Domains per Native Account

Closes the gap between H5 and the native stack (today: one domain per
account; the migration stops for a person when an account has addon,
sub or parked domains).

-   Addon domains, subdomains, parked/alias domains, redirects.
-   Per-domain document root, vhost, DNS zone and certificate.
-   Plan limits for domain counts, enforced server-side.
-   Required before cPanel accounts with several domains can migrate.

## N13 --- Wildcard SSL & Usage History

-   ACME DNS-01 through the native DNS service for wildcard certificates.
-   Usage history (disk, bandwidth, inodes, CPU) stored with retention, as
    required by H11; threshold alerts at defined levels (for example 80%
    and 95%).
-   Choose the metrics store in an ADR before implementation.

------------------------------------------------------------------------

# 23 --- [v2] P TRACK: PRODUCT FEATURES

These features make WHPanel competitive. They start only after the V
track phase they depend on is Proven, unless marked "can start now".
Each follows the same protocol, provider abstraction and UI completion
gate. **Inspect first (protocol start-of-task):** some already exist in
part.

## Security & Trust

-   **P1 Two-factor authentication.** TOTP and recovery codes already
    exist (`apps/accounts/mfa.py`). Remaining: make it mandatory for
    staff, then passkeys. *Can start now.*
-   **P2 Login & activity alerts** --- new device, password change, admin
    access to the customer's hosting. *Can start now.*
-   **P3 Malware scanning & web application firewall** on native nodes,
    surfaced in the Security Center (H10).

## Website Tools

-   **P4 One-click application installer**, WordPress first:
    install, update, admin login link, security hardening.
-   **P5 Staging sites** --- clone to staging, test, push to live with a
    backup taken first.
-   **P6 Git deployment** --- connect a repository, deploy on push, deploy
    log, rollback to the previous release.
-   **P7 Additional runtimes** --- Node.js and Python apps, behind the
    same isolation model. Requires an ADR.

## Customer & Business

-   **P8 Notification channels** --- WhatsApp and SMS in addition to
    email, for invoices, renewals, SSL expiry, usage limits and downtime.
    Customer chooses channels per event. *Can start now.*
-   **P9 Local payment methods** --- JazzCash, Easypaisa and
    bank-transfer proof upload with staff verification. Inspect the
    existing billing payment methods first and reuse them; do not
    rebuild. *Can start now.*
-   **P10 Public status page** fed by N10 monitoring, with incident
    notes.
-   **P11 Customer API & API tokens** --- scoped, revocable tokens over
    the existing provider-neutral `/api/v1/hosting/` endpoints, rate
    limited and audited.
-   **P12 Reseller accounts** --- resellers manage their own clients
    under their brand, with quotas set by WHPanel.
-   **P13 Urdu interface** for customer screens through Django i18n
    (the templates already use translation tags; there is no locale
    catalogue yet; right-to-left layout must be tested).

## Operations

-   **P14 Abuse handling** --- reports, evidence, suspend/unsuspend
    workflow with audit and customer notification.
-   **P15 Client data export & deletion** on request, respecting billing
    record retention.

------------------------------------------------------------------------

# 24 --- [v2] UPDATED IMPLEMENTATION ORDER

``` text
Already code complete: H0-H15, N0-N10, M0-M4, native cron, UI0-UI17

V0  Repository safety & CI                  <- next (push needs owner approval)
V1  Environments & release process
V2  First real node                          +---- MILESTONE B (Proven)
V3  Live cPanel adapter                      +---- MILESTONE A (Proven)
N11 Agent lifecycle & updates
N12 Multiple domains per native account
N13 Wildcard SSL & usage history
UI18 Final responsive / accessibility / visual QA
V4  Isolation, limits & load
V5  Backup, restore & DR
V6  Mail, security review, migration rehearsal
                                             +---- MILESTONE C (Proven)
M1  Native-first switched on (owner decision)
M2/M3 Real account migrations
M4  cPanel retirement                        +---- MILESTONE D

P track: P1, P2, P8, P9 can run in parallel now.
         Others after Milestone C unless the owner reprioritises.
```

------------------------------------------------------------------------

# 25 --- [v2] OPEN OWNER DECISIONS

Claude must not decide these; list them when they block work.

1.  Backup storage vendor and object-lock policy.
2.  Mail: self-hosted mail nodes or a hosted mail service.
3.  Agent update design (ADR 21 -> N11).
4.  Who performs the security review, and who runs the runbooks.
5.  When to enable native-first (`NEW_ACCOUNTS_NATIVE_ONLY`).
6.  Importing real prices from WHMCS before go-live.
7.  Metrics store for usage history (N13).
8.  Which P-track features come first.
9.  Approval to push the unpushed commits to the private remote.
