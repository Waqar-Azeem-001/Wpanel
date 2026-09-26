# Phase D6 — Sign-in, application shell and the Users area

Status: implemented locally (owner request, 2026-09-26). Not deployed. Built on the existing platform: no second
authentication or permission system, no backend rewrite.

## 1. Audit (what was there before)

| Area | Finding | Decision |
|---|---|---|
| Sign-in / sign-up | Working (`accounts` services, throttling, audit). Presented as a small card under the full site navigation. | Reuse all logic; new split-screen design (`layouts/auth.html`). |
| Google sign-in | **Not present** (no provider, no library). The live address the owner gave (`myaiwhe.com`) serves a different product with the same password-only form. | Not built. Deferred: needs a stored, encrypted OAuth provider record (Rule: no provider credentials in ENV) and belongs with Phase 16 (MFA). |
| MFA | Not present (Phase 16). | Not shown anywhere; the Users screens will show it once it exists. |
| Roles | Five: Customer, Support Agent, Manager, Admin, Super Admin (`apps/accounts/roles.py`, one role per user, groups synced). No technical role. | Added one: **Technical Staff** (see §4). Nothing else in RBAC changed. |
| Navigation | Registry-driven (`apps/core/navigation.py`), but three top bars with WHMCS-style dropdowns and labels ("List All Orders", "Predefined Replies", "View / Search Clients", Setup gear). | Same registry, new structure and labels; left rail, top bar, phone drawer and bottom bar. |
| Users management | "Staff & Roles" (staff only, no customers, no detail page, no search). | Replaced by **Users** (all roles). Old address still works (redirects). |
| Django admin | Registered models are all reachable through app screens except gateway-only ones (payment providers: manual payments only; webhook events; carts; coupon redemptions). Superusers had a "Django admin" menu item. | Kept for superusers only, renamed "Database admin (technical)", last item in the profile menu; never in the rail. |
| Providers | Email provider, registrar, servers, payment methods each have their own screen (D4d and earlier). | Grouped under **System → Providers** in the rail. No hub page or test-connection button (Phase 17). |
| Responsive | Top bars collapsed into a hamburger; wide staff bar needed an xxl breakpoint. | Rail collapses to icons on desktop and becomes a drawer below 992 px; customers get a bottom bar below 768 px. |
| Design system | `theme.css` tokens, Bootstrap 5 mapped onto them, brand from `/brand.css`. | Reused. New `static/css/shell.css` holds the shell and sign-in components only. |

## 2. Sign-in and create account

`templates/layouts/auth.html` (split screen: product panel on the left, form on the right; on a phone the panel
shrinks to a logo strip). Used by sign in, create account, password reset (request, sent, confirm) and email
verification. Fields go through `partials/auth_field.html` (label, show/hide password, "Forgot password?" beside the
label, hint, error under the field) and `partials/auth_errors.html`. Behaviour is unchanged: the same views,
forms and services, the `next` address is kept, throttling and audit are untouched.

The acknowledgement ("By signing in you agree to our terms of service and privacy policy.") sits under the button. It is
plain text: **there is no terms or privacy page yet** (roadmap Phase 15 "Terms of service page", deferred), so it cannot
link anywhere without breaking Rule 5. Add the links when the pages exist.

## 3. The application shell

`layouts/app.html` (used by `layouts/client.html` and `layouts/staff.html`, chosen per request by the existing
context processor; pages did not change):

* **Rail** (`components/rail.html`; from D7 it is one flat list, see `d7-visual-system.md` §3): entries come only from the menu registry. Top-level entries are single links or
  groups; a group's own link opens its first page and a chevron button shows its other pages (only the group you are
  in starts open; without JavaScript every group is open). Sections (staff): *People, Commerce, Operations, Insights,
  System*. Dark for staff, light for customers. Collapses to icons (name shown as the tooltip) and remembers the choice
  (`localStorage` key `wp.rail`, read in the head before paint).
* **Top bar** (`components/topbar.html`): breadcrumbs (from the registry), "Search or jump to…" (Ctrl/⌘ K), the bell with
  the unread count, the cart (customers) and the profile menu (name, email, role, account pages, sign out as a POST).
* **Search or jump to** (`components/quick_nav.html`, `static/js/shell.js`): lists the pages in the rail as you type;
  staff also get "Search everything for …" and a link to the full search page.
* **Phone**: the rail is a drawer (hamburger, Esc, backdrop, links close it). Customers also get a bottom bar with
  Overview, Services, Domains, Billing, Support (entries marked `mobile_tab`; shown only when there are at least three).
* The store keeps its top bar (`layouts/public.html`); the footer inside the shell is a quiet line.

### Information architecture

Customer: Overview · Services (My hosting, Order new services, Add-ons) · Domains (My domains, Register a domain) ·
Orders · Billing (Invoices, Quotes, Payment methods) · Support (Tickets, Open a ticket, Help articles) · Affiliates.
Profile menu: Profile & security, Your account, Contacts, Cancellation requests, Change password, Email history,
Notification preferences.

Staff, per permission (a section or group with nothing openable is not drawn):

| Section | Entries |
|---|---|
| — | Overview |
| People | Clients (All clients, Add a client) · Users · Affiliates |
| Commerce | Orders (All orders, New order) · Billing (Overview, Invoices, Transactions, Quotes, Billable items, Renewals & upgrades, Coupons) · Products (Products & plans, Add-ons) |
| Operations | Hosting · Domains (Registrations, Pricing by extension) · Lifecycle (Overview, Cancellations) · Support (Overview, Tickets, New ticket, Saved replies, Knowledge base, Departments) |
| Insights | Reports · Activity log |
| System | Providers (Servers, Domain registrar, Email provider, Payment methods) · Notifications (Delivery statistics, Email log) · Settings (Brand, Billing, Tax rules, Lifecycle timings, Affiliate programme) |

Nothing was added to the registry that has no page behind it (no "Provisioning", "Technical logs", "Security" or "System"
entries: those pages do not exist yet; they arrive with Phases 16 and 17). Hiding a link is never the protection: every
page keeps its own permission check, and tests request the hidden pages directly.

## 4. Roles and the Users area

**Technical Staff** (`Role.TECHNICAL`, migration `accounts.0003`): view clients, orders, hosting, domains, support;
manage hosting, domains, support. No billing, reports, users, providers, settings, audit log; cannot create clients,
orders or products. It is a staff role, not a privileged one (only a Super Admin grants Admin/Super Admin; an Admin or
Super Admin can create technical staff; a Manager cannot, having no `assign_roles`).

**Users** (`console:users`, `console:user_detail`, `console:user_edit`; `apps/console/views_users.py`):

* List of everyone with role tabs and counts (All, Customers, Support, Technical, Managers, Admins, Super admins), search
  (name, email, phone), status filter, paging, the client a customer belongs to, last sign-in. Constant query count.
* Detail: account facts (role, status, email verified, password set or invited, joined, last sign-in), client accounts
  with what they own (orders, services, domains, open tickets), open tickets assigned (staff), recent activity (needs
  `view_audit_log`).
* Actions, all through existing or one new audited service: edit name and phone (`update_user_details`, new), suspend or
  reactivate (`set_account_status`), change staff role (`assign_role`), add a staff member (`create_staff_user`). Rules
  live in the services and are unchanged: never yourself, an admin account only by a Super Admin, roles need
  `assign_roles`. The pages hide controls a person cannot use; the services refuse them anyway.
* Not built: department/team membership (a department only has a default assignee, so there is no membership to
  manage), MFA status (Phase 16), delete user (accounts are suspended or closed, never deleted).

## 5. Tests

`apps/core/test_shell.py` (new) plus updates to the older navigation tests: sign-in/sign-up/reset behaviour and design,
shell per role, active state and single open group, headings, phone bar, profile menu, technical role permissions and
direct-URL 403s, Users list/detail/actions, admin/Super Admin separation by direct POST, query count, old address.
The link crawler now runs a ninth role (technical staff): it found the Orders page linking to billing tabs the role cannot
open, fixed in `billing/staff/_nav.html`.

## 6. Left as it was, on purpose

* The in-page tab bars on billing, support and other modules (they duplicate the open group in the rail but also work
  when the rail is collapsed or on a phone).
* Customer sidebar panels (Your info, Contacts, Shortcuts): only the duplicate "Sign out" was removed.
* Every business service, API endpoint, permission and model other than the one role.

## 7. Deferred

Google sign-in; MFA and security status on user pages (Phase 16); terms and privacy pages to link from the
acknowledgement; provider hub with connection status, test connection and credential rotation (Phase 17 and the provider
work); technical logs and job-queue pages (Phase 17); department membership; removing the module tab bars once the rail
is proven.
