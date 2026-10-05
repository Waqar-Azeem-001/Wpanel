# H0: Hosting Control Architecture and Provider Abstraction

**Source:** `WHPANEL_NEXT_GENERATION_ROADMAP_FOR_CLAUDE.md`, section H0. **Date:** 2026-10-05. Local only.
**Exit criterion (roadmap):** the UI and core hosting services do not need to know whether an account is cPanel or native.

## 1. Rule 1 inspection (what already existed)

| Roadmap asks to inspect | Found | Status |
|---|---|---|
| `apps.hosting` | `HostingAccount` (status, domain, username, package, usage fields, backup ref), 20 service functions, staff and customer views, API viewset | Complete for the **account lifecycle** |
| `HostingProvider` | Already exists under another name: `HostingAdapter` (`apps/hosting/adapters/base.py`), chosen per server by `registry.build_adapter(server)` from `Server.kind` (`manual`, `whm_api`) | Complete; **it is the provider interface**. Not renamed (Rule 3, and stable names) |
| Server models | `products.Server`: hostname, IP, status, `kind`, WHM port/username/encrypted token, SSL flags, `max_accounts` | Complete. No region, capacity or health fields (those are H13/N2) |
| Encrypted credentials | Token encrypted with `apps.core.crypto`; never returned by an API | Complete |
| Provisioning services | `request_hosting`, `assign_server`, `complete_provisioning` (retry-safe), suspend, unsuspend, terminate, change package, sync, backup, usage | Complete |
| Hosting permissions | `view_hosting`, `manage_hosting`; customers act through "contact of the client" self-service | Complete; reused as is |
| APIs | `/api/v1/hosting-accounts/...` (lifecycle actions) | Complete for lifecycle |
| WHM adapter | `WhmApiAdapter`: create, suspend, unsuspend, terminate, backup, change package, status, usage. **Never verified against a real server** (known launch blocker) | Partial |
| Hosting UI | Customer list/detail/information/addons, staff list/detail/actions | Complete for lifecycle; **no files/databases/email/DNS/SSL/PHP/cron screens** |
| Audit | `apps.audit.services.record` used by every service | Complete |
| Tests | 69 tests across services, API, web, WHM adapter | Good for lifecycle |

**Gap:** the adapter knows only the account lifecycle and usage. Nothing models the roadmap's capabilities
(files, databases, email, domains, DNS, SSL, PHP, cron, backups), and there was no single place that decides who may
perform such an operation, which operations exist, and how they are audited.

## 2. Decisions (architecture decision records)

1. **Keep `HostingAdapter` as the provider interface.** `WhmApiAdapter` is the cPanel provider; a native provider
   arrives with the N track as another adapter chosen by `Server.kind`. Renaming to `HostingProvider` would only churn
   code and tests.
2. **Capabilities are opt-in contracts** (`adapters/capabilities.py`, contract v1). A provider subclasses the capability
   mixins it implements; `supported_capabilities(adapter)` reports them. Account lifecycle and usage are always
   supported. Providers return neutral records (`DnsRecord`, `MailboxInfo`, ...), never provider-shaped data.
3. **One gateway** (`apps/hosting/control.py`, `perform(user, account, capability, operation, ...)`). It owns ownership
   and permission checks (read: `view_hosting` or contact of the client; change: `manage_hosting` or contact), the
   account must be live and have a server, only operations in `capabilities.OPERATIONS` can be reached (no arbitrary
   method names, no shell), provider errors become `ServiceError`, and every change is audited
   (`hosting.control` / `hosting.control_failed`) with caller-chosen non-secret metadata. A test proves the allowlist
   equals each contract's methods exactly, so a new provider method cannot become reachable by accident.
4. **API paths keep the existing base** `/api/v1/hosting-accounts/{id}/...`, not the roadmap's illustrative
   `/api/v1/hosting/services/{id}/...`: the roadmap itself says to keep stable API contracts. Provider-neutrality is
   the real requirement and holds (no cPanel names in any path or response). Capability routes will be added under
   the existing base as each slice lands (`.../files/`, `.../databases/`, ...).
5. **No provider name leaks to customers.** The new `GET .../capabilities/` returns only capability names.

## 3. What was built in this slice

- `apps/hosting/adapters/capabilities.py`: the 11 capabilities, neutral records, 9 capability contracts, the operation
  allowlist, the read/change split, `supported_capabilities()`.
- `apps/hosting/control.py`: the gateway described above.
- `GET /api/v1/hosting-accounts/{id}/capabilities/`: what can be managed on an account (scoped like the rest of the
  hosting API: staff see all, customers only their own).
- `apps/hosting/tests/test_control.py`: 14 tests (discovery, a fake provider implementing only DNS, read and change
  paths, audit, provider failure, unsupported capability, allowlist, terminated account, owner, stranger, API scoping).

Verified: the hosting test package passes (existing 69 plus the new 14). No existing behaviour changed: `ManualAdapter`
and `WhmApiAdapter` are untouched and report `["account", "usage"]`.

## 4. What H0 deliberately does not do

- No capability is implemented by a real provider yet (that is H2 to H11 for the UI and H14 for the cPanel adapter;
  the native provider is the N track). Today every account honestly reports only account and usage.
- No customer screens (H1 onwards), no Admin Access context (H12), no node/health fields (H13).
- The WHM adapter's live verification remains a blocker for H14, not something H0 can fix.

## 5. Exit criterion check

The customer UI and API ask the gateway for a capability and never touch an adapter class or a server kind; swapping the
provider behind an account changes only which capabilities it reports. Demonstrated with the in-memory fake provider in
the tests. Not yet demonstrable end to end with a real cPanel or native provider (needs H14 / N).

## 6. Next slice

**H1: Websites and Hosting Dashboard.** It needs only data we already have (service list, status, plan, primary domain,
disk and bandwidth from usage) plus the new capabilities list to decide which management sections to show. It can be
built and browser-tested locally with the manual adapter.
