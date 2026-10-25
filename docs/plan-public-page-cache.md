# Plan: serve the public pages from Cloudflare's edge (not built yet)

**Why.** A public page takes ~40 ms in the panel but 230 to 420 ms for a visitor in Pakistan. About 150 ms of that is the round trip between Cloudflare's Karachi edge and the server in Germany. A page answered by the Karachi edge itself takes ~40 ms (that is what the CSS file measures today). Caching the public HTML at the edge removes that trip for anonymous visitors.

**Why it is not switched on.** Today the public pages contain things that are different for each visitor: a CSRF token inside the forms, the cart count and the signed-in menu in the header, and a `csrftoken` / `sessionid` cookie set on first visit. If the edge kept one visitor's copy, the next visitor would get that visitor's token and cart. Every step below exists to make that impossible.

## Step 1: make the public pages cookie-free (code, no Cloudflare change yet)

Pages in scope: `/`, `/hosting/`, `/domains/`, `/pricing/`, `/products/` and `/products/<plan>/`, `/status/`, `/help/` (and its articles).

- **CSRF.** Public forms stop printing `{% csrf_token %}` into the HTML. A small script asks `GET /csrf/` (a tiny endpoint: `Cache-Control: no-store`, sets the `csrftoken` cookie, returns the token) and fills the hidden input when the page loads or just before submit. Forms still work without JavaScript only for GET forms (the domain search is already a GET).
- **Cart count and signed-in state.** The header renders the neutral version ("Sign in", no count). A small script asks `GET /me/summary/` (`no-store`, JSON: signed in?, name, cart count) and fills the header. A visitor with no cookies gets the neutral answer instantly.
- **No session read on these pages.** Templates must not touch `request.user`, `request.session` or the cart on a cached page. A test renders each page as an anonymous visitor with a cookie and without one and asserts the two HTML bodies are identical, there is no `Set-Cookie` and no `Vary: Cookie`.
- **Headers.** The views send `Cache-Control: public, s-maxage=300, max-age=0, stale-while-revalidate=60`. Nothing else in the project changes.
- **Verify** with `curl -sI` against the live address: no `Set-Cookie`, `cf-cache-status: DYNAMIC` (Cloudflare is still not caching).

## Step 2: the Cloudflare cache rule

Rules > Cache Rules, two rules in this order (the first match wins):

1. **Bypass** when the request carries any session or sign-in cookie:
   `http.host eq "whpanel.myaiwhe.com" and (http.cookie contains "sessionid" or http.cookie contains "csrftoken" or http.cookie contains "wp_device")` → *Bypass cache*. Signed-in people and anyone with a cart always get a live page.
2. **Cache** the public pages for everyone else:
   `http.host eq "whpanel.myaiwhe.com" and http.request.method in {"GET" "HEAD"} and (http.request.uri.path in {"/" "/hosting/" "/domains/" "/pricing/" "/products/" "/status/" "/help/"} or starts_with(http.request.uri.path, "/products/") or starts_with(http.request.uri.path, "/help/"))` → *Eligible for cache*; **Edge TTL**: use the origin's `s-maxage` (5 minutes); **Browser TTL**: respect origin (0); *Cache by query string*: ignore only `utm_*` (the domain search `?domain=` must stay part of the key).

Never in scope: `/account/`, `/staff/`, `/api/`, `/cart/`, `/checkout/`, `/login/`, anything with a POST.

## Step 3: purge when the catalogue changes

- The catalogue already has one place that fires on every change (`categories.forget_snapshot`, wired to the saves of products, prices, add-ons, categories and domain prices).
- Add: on commit, a Celery task `purge_public_pages` calls Cloudflare `POST /zones/<zone id>/purge_cache` with `{"files": [<the 8 to 20 public addresses>]}` (a plan page when one plan changed). Debounced by 5 seconds so a bulk edit makes one call.
- Settings: `CLOUDFLARE_ZONE_ID`, `CLOUDFLARE_API_TOKEN` in the server `.env`. The token is created with **only** the permission *Zone > Cache Purge* on this one zone.
- A failed purge is logged, retried twice, and shows on the Health panel ("Public page cache: last purge failed"). Worst case a page is 5 minutes old, never wrong for longer.

## Step 4: roll out carefully

1. Ship step 1; check headers on live; nothing is cached yet.
2. Turn on rule 2 for `/status/` only; check `cf-cache-status: HIT` for an anonymous request and `BYPASS`/`DYNAMIC` with a cookie; change a price and confirm the purge.
3. Add `/`, `/pricing/`, `/hosting/`, `/domains/`, `/products/`, `/help/` one at a time over a few days.
4. Kill switch: disable the rule in Cloudflare; the site behaves exactly as today.

## What to expect and what could go wrong

- Anonymous visitors from Karachi: ~40 to 60 ms instead of 230 to 420 ms; the server sees far fewer public requests.
- Risk: a page that still holds a visitor-specific value. The identical-bodies test in step 1 is the guard; add every new public template to it.
- Risk: a visitor with cookies blocked cannot see the cart count or sign in from the header: the neutral header still links to Sign in; the cart link goes to the cart page.
- A deploy changes the HTML: purge everything after a deploy (the deploy script gets one `purge_everything` call).
