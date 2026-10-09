# Syncing a server's plans into the catalog

A WHM server knows its hosting **packages and what each includes**, but not what you charge for them. So the sync copies *features* and never
*prices*.

## What is copied (from WHM `listpkgs`)
Disk space, bandwidth, email accounts, databases, FTP accounts, subdomains, addon domains, parked domains, mailing lists, emails per hour,
shell access and dedicated IP (WHM's `QUOTA`, `BWLIMIT`, `MAXPOP`, `MAXSQL`, `MAXFTP`, `MAXSUB`, `MAXADDON`, `MAXPARK`, `MAXLST`,
`MAX_EMAIL_PER_HOUR`, `HASSHELL`, `IP`; "unlimited" and 0 become Unlimited). They fill the product's "What is included" list and the feature
lines of its description (storage, bandwidth, databases, mailboxes...). Every other description line (free domain, support, anything staff
wrote) is kept. A product's name, price, status, category and add-ons are never touched.

## Three ways
- **Servers > Sync plans** (a WHM server's row): shows what is broken (a product pointing at a package the server lacks), what is new on the
  server, and what differs; tick and Apply.
- **`python manage.py sync_server_plans`**: every WHM server, copies the features onto linked products. `--create` adds the server's other plans
  as hidden, unpriced products; `--dry-run` only reports; `--server <id>`; `--as <email>` for the audit trail. Safe to run again and again, so it
  can be scheduled.
- **The API/service**: `apps.products.services.apply_server_plans`.

## Prices
Set a price on each product (Products > the product > Prices). New products from a server arrive hidden and unpriced on purpose; nothing is
shown in the store until you price and publish them. Prices from WHMCS can be imported once the WHMCS import is built.
