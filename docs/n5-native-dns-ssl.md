# N5: Native DNS and SSL

Goal: `Domain -> DNS -> Validation -> Certificate -> HTTPS -> Auto renewal`, with the customer's DNS (H5) and SSL (H6) screens unchanged.

## What is built and what is not

| Part | State |
|---|---|
| DNS: `NativeDns` (the same `DnsCapability`), zone made when the website becomes active and removed when it is deleted, same record rules as every other provider | Built, tested against `FakePowerDns` |
| `PowerDnsClient` (PowerDNS HTTP API) | Written from PowerDNS's documented API, tested against a scripted fake session (URLs, headers, payloads, errors). **Never run against a real PowerDNS** |
| SSL: `NativeSsl` (the same `SslCapability`), pre-check that the domain points at the node, issue, status, renewal switch, HTTPS redirect, remove | Built, tested |
| Node: issue and install, 443 virtual host, redirect that leaves the validation path open, daily renewal (`Ssl.renew_due`) | Built in Python, tested with `FakeAcme` that makes real self-signed certificates (so reading, dates and status are real) |
| **`RealAcme`** (certbot, HTTP-01) | **Never run against Let's Encrypt.** It only supports HTTP-01 |
| **DNS-01 / wildcard certificates** | **Not built.** The command accepts `dns-01` but the node answers "not available"; the adapter always uses HTTP-01 |
| Who runs the daily renewal | `Ssl.renew_due(accounts)` exists and is tested. **Nothing calls it yet**: the Go agent (or a timer on the node) must run it every day. Until then certificates are not renewed automatically |
| ZeroSSL fallback, customer-uploaded certificates, DNSSEC, second nameserver | Not built (open questions in ADR 07/08) |
| Go agent | Written in Go (`agent/`) for every command of this stage, checked against the Python reference by golden scenarios and against live Django end to end on a pretend machine. **The real-machine paths have never run on a real node** (see agent/README.md) |

## DNS

* Configure `POWERDNS_URL`, `POWERDNS_API_KEY` and `POWERDNS_NAMESERVERS` (comma separated). **Without them the DNS tool is not offered on native accounts** (rather than failing).
* WHPanel talks to PowerDNS itself; the node's agent is not involved (ADR 07).
* The zone starts with the nameservers, `A @ -> node IPv4` and `www CNAME @`. SOA and NS records are managed by the service: not listed, cannot be removed.
* Only the account's own domain can be read or changed. A record's id is its type, name and content hash; changing a record validates the new value **before** removing the old one.
* A DNS problem when creating or deleting a zone is audited (`hosting.dns_zone_failed`) and never undoes the website.
* Nameservers must be reachable by registries: the customer still has to point the domain's nameservers (or A records) at WHPanel's. Registrar integration for this is not part of N5.

## SSL

1. The customer presses issue. WHPanel checks that `domain` and `www.domain` resolve to the node's address; if not, it says which name and which address to use, and nothing is sent.
2. The node asks the authority over HTTP-01 for both names, writes the key (root only, mode 0600) and chain to `/etc/whp/tls/<account>/<domain>/`, adds a 443 virtual host
   (TLS 1.2/1.3), tests the config (`httpd -t`) and reloads. If the web server refuses the new config the previous one is put back.
3. Status (active / expiring at 14 days or less / expired / none), issuer and dates are read from the installed certificate itself.
4. The redirect switch adds a 301 to HTTPS on port 80 **except for `/.well-known/acme-challenge/`**, so renewal keeps working. Auto-renew and redirect are independent switches.
5. Suspending and reactivating a site keeps its HTTPS settings. A suspended site shows the suspended page on port 80 only.

## Tests

`apps/agent/tests/test_native_dns_ssl.py` (23): zone life cycle and outage, the DNS screen operations, shared record rules, failed change keeps old record, ownership and protected records, capability only
with nameservers, the real client's requests, issue/install/status, domain-not-pointing-here, authority refusal, redirect and renewal switches, removal, suspend/unsuspend, expiring/expired,
renewal due/not due/switched off/suspended/failure isolation, other domains refused, DNS-01 honesty, the real customer SSL page.
