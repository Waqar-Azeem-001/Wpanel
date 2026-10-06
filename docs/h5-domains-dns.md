# H5: Domain and DNS Management

**Source:** next-generation roadmap, H5. **Date:** 2026-10-07. Local only.

## Rule 1 inspection
The contract had only the bare operations (list/add/remove domains, one redirect call, four DNS calls) and no provider, no
limits, no redirect type, no screens. Separate from this: `apps.domains` is the customer's *registered* domains (registrar),
with its own DNS tab; H5 is the domains and DNS of a *hosting account*. Reused: the hosting-control gateway, the workspace menu,
the File Manager (each new domain gets a website folder there), the demo provider, the shared look of the earlier screens.

## What was built
- **Contract v1 additions:** `domain_limits` (extra-domain and subdomain limits, nameservers), a redirect type (301 or 302),
  `DomainInfo.redirect_permanent`, `DOMAIN_KINDS` and `DNS_RECORD_TYPES`, TTL range 300 to 86400.
- **Demo provider** (`demo_domains.py`), a service with real rules:
  - Domains: valid full names only; extra domains (addon or alias) and subdomains are counted against separate plan limits (5 and
    20); a subdomain must sit under a domain you own and puts its A record in the parent zone; an extra domain may not overlap one you
    have (either direction); an alias shows the main site; the website folder is checked to stay inside the account and is created,
    with a placeholder page, in the file manager; removing a domain deletes its zone and keeps its files; the main domain cannot be
    removed. Redirects must be http or https addresses, cannot point at themselves, 301 or 302.
  - DNS (A, AAAA, CNAME, MX, TXT, SRV, CAA): per-type validation (IPv4, IPv6, host names, MX and SRV priority, SRV name and value, CAA
    syntax), names tidied into the zone, wildcard only as the first label, CNAME not on the domain itself and never sharing a name,
    duplicates refused, 200 records per zone, TTL range, type cannot change on edit. A new zone starts with A, www, mail, MX and SPF.
- **Domains screen:** counts against the plan, an add form that adapts (Another domain, A subdomain typed as its first part under a
  chosen domain, An alias; folder suggestion follows the name), the list with kind, website folder (a link into the file manager),
  redirect, a DNS button, redirect dialog (301 or 302, remove), removal with a confirmation, and the nameservers.
- **DNS screen:** one tab per domain; a clear warning; an add form showing only the fields a type needs plus an example; the records
  list with coloured type tags; edit dialog (a TTL outside the presets survives an edit); deletion always asks, with a stronger
  warning for the A and MX records of the domain itself; "Email records" helpers (add SPF, add DMARC; DKIM noted as server-side).
- Audit records the domain, type and name of every change, never record values.

## Verified
69 tests: the provider's rules (names, overlaps, limits, folders, redirects, every record type valid and invalid, CNAME conflicts,
duplicates, edit and delete, zone size), the screens end to end, errors as messages, audit without values, strangers get 404, a
provider without domains shows the not-available page. Hosting, crawler, client-area and design suites pass. Browser check: the
add form adapts, the priority box shows for MX only, the edit dialog fills, no console errors.

## Deferred
Real DNS and a real cPanel provider (H14), DNSSEC, NS and SOA records and zone import/export, per-domain mail routing, and the
375 / 768 px check.
