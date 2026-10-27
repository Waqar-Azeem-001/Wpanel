# Our own email: Roundcube under our name (step 1) and our own mail server (step 2)

Status: DESIGN, nothing built yet. Written 2026-10-27 for the owner to approve. Nothing here touches the live panel server until approved.

## Why two steps

Today a customer's mailbox lives on the cPanel server, and "Open webmail" sends them to that server's Roundcube through a one-time link (live since 2026-10-27, commit 3c708b7). It works, but the address is the WHM server's, not ours.

* **Step 1 (Roundcube under our name):** mail stays on cPanel. We add our own Roundcube at `webmail.<our domain>` that talks to cPanel's mail (IMAP/SMTP). Same experience as Hostinger: our brand, one click from the panel. Low risk, a few days of work.
* **Step 2 (our own mail server):** mail moves off cPanel to a mail server we run. We then own deliverability, spam, storage and abuse. It is the native-node work (V2 of the master roadmap) and needs its own server.

Step 1 does not get thrown away: Roundcube keeps working in step 2, only its back end changes (cPanel's Dovecot to ours).

## Step 1: Roundcube at webmail.<our domain>

What gets built
1. A Roundcube container (own database, its own small Postgres or SQLite volume) on a server of ours. Not on the panel's database.
2. A DNS name `webmail.<domain>` and an SSL certificate (Let's Encrypt), behind the same Cloudflare/nginx pattern as the panel.
3. Roundcube points at the cPanel server's IMAP (993) and SMTP submission (465/587) with TLS. No mailbox password is stored by Roundcube.
4. **Sign-in:** cPanel gives no password-free mail login (checked while building), and the panel never holds mailbox passwords. So the customer types the mailbox password once on our Roundcube page; the panel opens it with the address already filled in. A true one-click sign-in becomes possible in step 2 (our own Dovecot supports a master-user login).
5. "Open webmail" in the panel goes to our Roundcube; the cPanel one-time link stays as a fallback if ours is down.
6. A customer can also use `webmail.<their own domain>` later (white label), not in the first version.

Proof before anything goes to customers: a throwaway Roundcube on a test host against a test mailbox on the real WHM server, then the real log-in, send, receive. Nothing created for real customers.

Open questions for the owner (step 1)
* Which domain name: `webmail.webhostera.com` or another?
* Where does the Roundcube container run: a small second VPS (recommended, keeps mail traffic away from the panel) or the panel server (3.8 GB RAM, 2 CPU, already used 2.4 GB: it fits Roundcube alone, but I would rather not add load there before launch).

## Step 2: our own mail server

What it means
* A separate server (not the panel server) running Postfix + Dovecot + Rspamd, usually through Mailcow or a similar bundle. Mailbox storage on its own disk, backed up like the panel's database (encrypted, off-site).
* The panel creates mailboxes, quotas, forwarders and passwords through that server's API instead of cPanel's UAPI: a new adapter next to `cpanel_email.py`, reusing the same capability contract (`EmailCapability`) so the customer screens do not change.
* DNS for every customer domain: MX, SPF, DKIM (one key per domain), DMARC. The panel already checks these (`apps/notifications/deliverability.py`); it would also publish them for domains whose DNS we host.

What it costs us (be honest)
* **Server size:** a Mailcow-style stack wants about 6 GB RAM and 2+ CPU for a small start. The current panel server (3.8 GB) is too small. Plan for a separate VPS of 8 GB.
* **Port 25:** many hosts, Contabo included, block or restrict outgoing port 25 on new servers. We must check with the provider before buying or moving anything.
* **IP reputation:** a new IP is untrusted. We need reverse DNS (PTR) matching the mail host name, a clean IP (check blacklists before use), and a slow warm-up. Mail to Gmail/Outlook may land in spam for weeks.
* **Abuse:** one customer with a hacked mailbox can get the whole IP blacklisted. We need per-mailbox send limits, outgoing rate limits, spam scanning on outgoing mail, and a person who reads abuse reports.
* **Backups and recovery:** mail is customer data. Daily encrypted backups, off-site, with a restore drill, the same way as the panel (see `docs/backup-recovery.md`).
* **Moving customers:** copying existing mail from cPanel to the new server (imapsync), with a short DNS switch window. Done per customer, with their agreement.

Order of work for step 2
1. Pick provider and size; confirm port 25 and PTR with them in writing.
2. Build the server in a throwaway test setup; send and receive with a test domain; check scores on a deliverability tester.
3. Write the new adapter and its tests (same style as the cPanel adapter tests: a fake server first, then the real one).
4. Pilot with our own domains for 2 weeks (our staff mail), watch bounces and spam placement.
5. Offer it to new customers as an option; migrate existing ones only when they ask.

## What I recommend

Do step 1 now (it also fixes the customer experience for launch), and prepare step 2 in parallel only as research (provider, port 25, price) until launch track A is done, because a mail server that lands in spam hurts the brand more than having no mail server.

## Decisions I need

1. Webmail domain name for step 1.
2. Small second VPS for Roundcube (yes/no), or on the panel server.
3. Budget and provider for the step 2 mail server (8 GB class), and whether I may ask the provider about port 25 and PTR.
4. Whether step 2 starts before or after launch.

## What I will not do without asking

Buy or create servers, change DNS, touch the live cPanel mail, create real mailboxes or accounts, or move any customer's mail.
