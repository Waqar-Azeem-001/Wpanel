# H15: Temporary cPanel escape hatch ("Advanced tools: Open cPanel")

**Source:** next-generation roadmap, H15. **Transitional by design: remove this slice when cPanel is retired.** Local only.
Written against WHM's documented `create_user_session`; tested with a fake WHM, **not run against a real server yet**, so it is
off by default like the other cPanel tools.

## What it does
For a cPanel server whose staff have turned it on, the website menu gets **Advanced tools (cPanel)** under Advanced. The page explains
that this is temporary, then **Open cPanel** signs the customer in with a one-time link and redirects the browser there. Servers that
are not cPanel (the demo, manual, a future native node) never show it.

## How the roadmap's requirements are met
- **Secure temporary login:** WHM's `create_user_session` makes a short-lived, single-use link for one account's cPanel; the link
  is used once by the redirect.
- **Authorization before the session is created:** the same hosting-control gateway as every other tool decides first. An owner of
  the account may; staff must be in Admin Access (H12) for that account *and* hold the manage-hosting permission, so a view-only role
  cannot. The account must be **active** (not pending, suspended or terminated).
- **Never expose the WHM token:** it is used only on the server side inside the adapter; the browser only ever gets the one-time link.
- **Never display the cPanel password:** no password is involved at all, and none is shown or asked for.
- **Audit the access requests:** every request writes `hosting.control` (or `hosting.control_failed`) with the account, who, and
  `transitional: true` (and `admin_access: true` for support). **Neither the link nor the session token is stored or logged**; the
  page's "Recent sign-ins" list shows only who and when. A short-term limit (6 per account per 10 minutes, refused ones included)
  stops a stuck button or abuse.
- **Clearly transitional:** a warning on the page, the word Temporary, and this module is one file (`cpanel_native.py`) plus one
  screen (`views_native.py`, `native.html`) to delete.
- The redirect is POST-only, sent with `Cache-Control: no-store` and `Referrer-Policy: no-referrer`; a link that is not http(s) (for
  example `javascript:`) is refused.

## Seeing it on the demo server
Demo accounts show **Advanced tools (cPanel)** too. **Open cPanel** there signs in to a **sample page** that stands in for cPanel (a
one-time link to `.../manage/native/sample/?session=...`): the link works once and for two minutes (only a hash of it is kept; a wrong
guess also spends it), and reloading shows "This sign-in link has expired". Nothing about it is cPanel-specific, so the whole flow,
audit and warnings can be looked at without a cPanel server.

## Turning it on
`HOSTING_CPANEL_CAPABILITIES=native_panel` (add to the list of tools; see `docs/h14-cpanel-adapter.md`). Check on a staging server that
`create_user_session` with `service=cpaneld` returns `data.url` and `data.expires`.

## Verified
23 tests (`test_native_panel.py`): off by default; the exact WHM call (account only, no password); odd or dangerous links refused;
menu hidden for cPanel when not enabled; the demo flow with its sample page (single use, expiry, only a hash stored, demo-only, owner-only); the page makes no sign-in just by opening; the redirect headers; the audit
holds no link or token; failures shown and audited as failed; the limit; other customers get 404 and no sign-in is made; staff need
Admin Access and a managing role; pending, suspended and terminated accounts refused.

## Noticed on the way
The gateway treats a *pending* account as live (it holds the username), so other tools can be called for an account WHM has not
created yet; they simply get WHM's own error. Open cPanel therefore checks for **active** itself. Tightening the gateway for every tool
is a separate, small change worth doing before going live.
