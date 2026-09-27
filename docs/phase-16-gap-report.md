# Phase 16 Gap Report: Security Hardening (Slices 1-2)

**Date:** 2026-09-27
**Baseline:** Phases 01-14 and the D/E design and owner-request tracks complete.

Phase 16 is large; this is its first slice. It covers two-factor sign-in end to end (the item with the most surface
area and the only one the codebase had nothing of yet) and the account-lockout, security-header and open-redirect
items that go with "secure sessions". What is deferred is listed at the end.

## FOUND (already in place before this slice)

- **CSRF**: Django's `CsrfViewMiddleware` on every request; every form in the project uses `{% csrf_token %}`.
- **Secure sessions**: `SESSION_COOKIE_HTTPONLY`, `SESSION_COOKIE_SAMESITE=Lax`, a 12-hour default age with an explicit
  "keep me signed in" (30 days) or "until the browser closes" choice (Phase E4); `SESSION_COOKIE_SECURE` and
  `CSRF_COOKIE_SECURE` in production.
- **Rate limiting**: every auth API endpoint (register, login, refresh, verify, password reset/change) throttled by
  DRF's `ScopedRateThrottle` (`auth` scope), on top of the general anon/user throttles.
- **Secure passwords**: Django's validators (similarity, 10-character minimum, common-password list, not all
  numeric); passwords are always hashed, never logged, never re-shown once set.
- **Credential protection**: provider credentials (registrar, WHM, payment, email) are encrypted at rest
  (`apps.core.crypto`, Fernet); cPanel passwords are never stored; transfer auth codes are never returned; a `.env`
  password is the only place a Super Admin's own password ever lives outside the hash.
- **Server-side billing validation**: prices, totals, discounts and coupon rules are recomputed server-side at every
  step (Phases 06-09); nothing from the client is trusted for money.
- **Webhook signature verification**: `apps/billing/gateways.py` verifies each gateway's own HMAC signature and
  timestamp before a webhook is processed; a bad signature is a 400 that changes nothing (Phase 09).
- **API authentication**: JWT (access + refresh, with blacklisting on logout/password change) or session auth,
  `IsAuthenticated` by default, object-level permission checks throughout (`HasPortalPermission`).
- **Sensitive-action confirmation**: the `data-confirm` modal on every destructive or hard-to-reverse staff action
  (Phase D-series); `@sensitive_post_parameters` on every view that takes a password, so Django's error reporter
  never captures one.
- **Open-redirect protection**: every `next` parameter (sign-in, bulk actions, the affiliate referral redirect) is
  checked with `url_has_allowed_host_and_scheme` before it is used; a hostile `next` falls back to a safe default
  (`apps/core/test_redirects.py`).
- **Audit coverage**: authentication, security, user, product, price, invoice, payment, provisioning, suspension,
  termination, domain and admin actions were already recorded (`apps.audit.services.record`) across every earlier
  phase.
- **"Login as Client"**: no such feature exists in the codebase to audit - the closest thing is the Super Admin
  "sign-in link" (a one-time password-set link, already audited, Phase E4). True session impersonation was never
  built and is not added here; if it is wanted later it needs its own audited-with-banner design.

## MISSING (now implemented in this slice)

- **Two-factor sign-in (TOTP)**, optional for everyone - a staff member turns it on themselves, from their profile,
  the same way a customer would; nothing forces or steers anyone towards it (see "Revised after this slice, the same
  day" at the end of the slice 2 section, for why staff are not required to use it):
  - The maths (`apps/accounts/mfa.py`): RFC 6238 TOTP, no third-party library, no network call. A 6-digit code, 30
    second steps, one step of clock drift accepted either side.
  - Setup (`/account/mfa/setup/`): a secret to type into an authenticator app (or open via its `otpauth://` address),
    confirmed with a code, after which ten single-use recovery codes are shown once and never stored in the clear
    (hashed like a password). Regenerating them, or turning two-factor sign-in off, needs the current password.
  - Sign-in: a correct password no longer signs someone straight in when it is on - it opens a short-lived pending
    state (`/account/mfa/verify/`) that a correct code or recovery code completes. The API's login endpoint carries
    the same rule (`mfa_code` field; `mfa_required` / `mfa_invalid_code` when it is missing or wrong).
  - A Super Admin can reset someone else's (never their own) from the Users screen, for a lost authenticator app.
  - A wrong code is rate-limited the same way a wrong password is (below), and every change (on, off, admin reset,
    new recovery codes) is audited and emailed to the account.
- **Account lockout beyond the per-IP rate limit**: after five wrong passwords for the same email address (real or
  not - the lockout looks the same either way, so it never hints at which emails exist), that email cannot be tried
  again for fifteen minutes; a correct password clears the count. The same mechanism guards a wrong two-factor code.
  Kept in the cache (Redis in production, per-process in dev/test), not the database.
- **Security response headers**: a Content Security Policy and a Permissions Policy, which Django has no setting for
  (`apps.core.middleware.SecurityHeadersMiddleware`). Every script and stylesheet the portal serves is its own - no
  CDN, and (found and removed in this slice) four inline `onclick`/`onchange` attributes across the templates - so
  the policy is strict: no inline or third-party script, no framing, no browser feature (camera, microphone,
  geolocation...) a hosting portal has no reason to ask for. A response that sets its own tighter policy (an
  uploaded file, an internal preview) is left alone.
- Two small, unrelated bugs the middleware and its tests found on the way: the header's own hide-on-desktop rule was
  losing to a more specific selector (fixed in the E6 owner round, unrelated to this phase, noted here only because
  the same audit pass touched the header) - not part of this slice's diff.

## PARTIAL / DEFERRED to a later Phase 16 slice

- **RBAC review**: the role/permission system (Phase 03) was not re-audited line by line in this slice; nothing here
  changed a permission's meaning. (Done in slice 2, below.)
- **"Login as Client" audited with banner**: not built (see FOUND); deferred until the feature itself is wanted.
- **Re-authentication for the most sensitive actions** beyond password confirmation on MFA changes (a step-up prompt
  before, say, changing a Super Admin's own email) - not built in this slice. (Built in slice 2, then reverted after
  the owner tried it - see "Revised after this slice, the same day" at the end of the slice 2 section.)
- **CSP for uploaded/embedded images beyond the site's own** - the policy allows `data:` for images; nothing today
  serves a third-party image, so this has not been exercised against a real case.
- **Reliability and failure handling** (Phase 17) and the rest of production launch hardening (Phases 18-19) are
  separate phases, not part of this slice.

## REUSE

| Reused | How |
|---|---|
| `apps.core.crypto` | The MFA secret is encrypted at rest the same way a provider credential is. |
| Django's password hasher (`make_password`/`check_password`) | Recovery codes are hashed and checked the same way a password is - one-way, never stored in the clear. |
| The cache (already used for branding settings, menu settings) | Login and MFA lockout counters; nothing new to configure. |
| `data-confirm`, `data-copy`, `data-select-on-click`, `data-submit-on-change` (shell.js) | The MFA pages, and the four templates whose inline handlers were replaced, use the project's existing attribute-driven behaviours - no new inline script anywhere. |
| The "sign-in link" / "set password" pattern on the Users screen (Phase E4) | The Super Admin's "reset two-factor sign-in" button follows the same layout, permission shape and confirmation. |
| `apps.notifications` (events, templates, `dispatch`) | Three new account-security notices (on, off, reset by admin) follow the existing catalogue and locked-down wording engine. |

## VERIFIED

`apps/accounts/mfa.py` (the TOTP maths, no database) and `apps/core/test_owner_tools4.py`: setup, sign-in with a code
or a recovery code, disable, admin reset, that staff reach the console with or without it exactly like a customer
does, account and two-factor lockout, the API's `mfa_required`/`mfa_invalid_code`, the security headers, and that no
inline event handler remains anywhere in `templates/`. Mutation checks on the cleaner logic, the lockout and both
login paths each make a test fail. The full suite passes on SQLite; browser-checked (Chromium, 1440 and 390 wide):
the whole setup walk-through (scan, confirm, recovery codes, the "on" state), and a second sign-in stopping at the
code step with a wrong code rejected and the right one working - no overflow, no console errors.

# Slice 2: RBAC review (and a step-up re-authentication feature that was tried, then reverted)

**Date:** 2026-09-27

Slice 1 left two items as "deferred": an RBAC review, and re-authentication for the most sensitive staff actions.
The RBAC review below stands. The re-authentication feature was built, verified end to end, and then removed the
same day at the owner's request: in practice it interrupted routine staff work rather than reading as a safeguard.
See "Revised after this slice" at the end for exactly what came back out and why. Two-factor sign-in itself was also
changed in this slice, from mandatory-for-staff to optional-for-everyone, for the same underlying reason: it was
blocking staff from their own console until they set it up, which the owner found more friction than it was worth
for a small, trusted team.

## RBAC review

The role/permission table (`apps/accounts/roles.py`, `ROLE_PERMISSIONS`) was read line by line against every
`portal_permission_required(...)` call in the codebase, in both directions: every permission a role is granted was
checked for at least one route that actually requires it, and every route's requirement was checked against
`docs/route-inventory.md`. Two things came out of it, both documentation/tooling accuracy rather than a real access
gap - every route in question genuinely was permission-gated before this slice, and no role's effective access
changes because of it:

- **`route_inventory.py` under-reported several genuinely-guarded `/staff/...` routes as "public".** Its access-
  detection only recognised one alias style, `VIEW = portal_permission_required(perm("view_x"))` used as a bare
  `@VIEW` decorator. It missed the equally common `VIEW = perm("view_x")` (or the tuple-unpack form,
  `A, B = perm("view_y"), perm("manage_y")`) used as `@portal_permission_required(VIEW)`, and it did not recognise
  the custom `@_superuser_only` decorator at all. Three examples that were wrongly labelled "public" before the fix
  (all were correctly permission-gated all along - the inventory just could not explain why):

  | Route | Was reported | Now reported |
  |---|---|---|
  | `console:staff_users` | public | `perm: view_users` |
  | `notifications_staff:templates` | public | `Super Admin only` |
  | `support_staff:departments` | public | `perm: view_support` |

  Fixed by extending `permission_aliases()` with a second pattern for the bare/tuple alias style, extending
  `access_of()` to follow a bare alias name used inside `portal_permission_required(...)`'s own parentheses, and
  adding a check for `_superuser_only` in a view's decorator head. Regenerated `docs/route-inventory.md` (494 routes,
  all `/staff/...` and `/account/...` routes now report a real access requirement, none "public"); two regression
  tests pin both the wrapped and the bare alias styles so this cannot silently regress
  (`test_the_route_inventory_resolves_every_staff_permission_alias_and_names_no_route_bare_public`,
  `test_permission_aliases_resolves_both_the_wrapped_and_the_bare_alias_style`).
- **`manage_reports` is a vestigial permission.** It is defined (`PORTAL_PERMISSIONS`) and granted to Admin and Super
  Admin (`ALL_CODENAMES`), but no view anywhere checks it - the reports area (`apps/reports/`) is read-only, gated
  entirely by `view_reports`, which Managers and above hold (Phase 14). Confirmed by grepping every `perm("...")`
  call in the codebase against every codename the role table hands out: it is the only one of the 26 portal
  permissions with zero call sites. Not a security issue (nothing is under-protected; a permission simply exists
  that no code asks for), so left as-is rather than changed under a security-slice diff - worth dropping the next
  time `apps/accounts/roles.py` is touched for an unrelated reason, since removing it needs a `sync_roles()` run and
  is a data-shape change, not a hardening one.

### VERIFIED (slice 2, RBAC review)

The two route-inventory regression tests above
(`test_the_route_inventory_resolves_every_staff_permission_alias_and_names_no_route_bare_public`,
`test_permission_aliases_resolves_both_the_wrapped_and_the_bare_alias_style`), in `apps/core/test_owner_tools4.py`.
`docs/route-inventory.md` regenerated from the fixed inventory command.

## Revised after this slice, the same day

Two pieces of slice 1/2 were tried and then rolled back on direct owner feedback, before this report's "MISSING"
and "VERIFIED" sections above were rewritten to describe the system as it stands now (not as a history of what was
briefly true):

- **Two-factor sign-in changed from mandatory-for-staff to optional-for-everyone.** `RequireStaffMFAMiddleware`
  (which sent any staff request under `/staff/...` to the setup page until two-factor sign-in was turned on) was
  removed entirely, along with its test coverage of that redirect. The TOTP mechanism itself - setup, sign-in,
  recovery codes, admin reset - is unchanged and works exactly as before; only the forcing is gone. Staff now reach
  the console with or without it, the same as a customer always could, and can turn it on from their profile
  whenever they choose to.
- **Step-up re-authentication ("confirm it's you") was removed.** It had gated four actions (setting someone else's
  password, a one-time sign-in link, a staff role change, resetting someone's two-factor sign-in) behind a fresh
  password prompt on a 15-minute window, and was fully built, mutation-tested and browser-verified before the owner
  used it and found it interrupted routine staff work rather than reading as a safeguard for a small, trusted team.
  Removed: `apps/core/reauth.py`, `templates/accounts/reauth_confirm.html`, the `/account/reauth/` route and view,
  and the `@require_recent_auth` decorator from all four actions, which now work exactly as they did before slice 2.
  The RBAC-review fixes to `route_inventory.py` are unrelated to this feature and were kept.
