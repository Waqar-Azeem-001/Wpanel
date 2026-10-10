# N7: Native mail platform

Roadmap: SMTP, IMAP, mailboxes, webmail, forwarders, aliases, spam filtering, SPF, DKIM, DMARC, quotas, queues, reputation monitoring, abuse controls; and *evaluate whether mail should be separated from web-hosting nodes*.

## The separation question, answered

**Yes: mail runs on its own mail nodes** (ADR 06's recommendation, now built). A hacked website or a spammer on a web node must not damage the reputation of the address mail leaves from, and mail can be scaled, backed up
and replaced on its own. A mail node is a normal enrolled node (N2) with the capability `mail`. A native web account is given one mail node the first time it uses mail (the healthiest with the fewest accounts) and
keeps it (`HostingAccount.mail_server`).

**What is still the business's decision (ADR 06 open question):** whether to run mail at all, or use a hosted provider. The Email tool is built behind the same `EmailCapability` as cPanel mail, so a hosted provider could
replace the mail node later without changing a screen. Running your own mail brings an ongoing duty to watch reputation; the monitoring below helps but does not remove it. Also decide: outbound relay through a provider while a
new IP builds reputation (not built).

## What is built and what is not

| Part | State |
|---|---|
| `NativeEmail` (the same `EmailCapability`): mailboxes, quotas, passwords, forwarders, auto-replies, spam settings, routing, embedded webmail (folders, read, star, move, trash, drafts, send), profile | Built, tested through `control.perform` and the real Email page |
| Mail node logic (`apps/agent/node/mail.py`): per-domain state, rendered Postfix maps, Dovecot password file, Rspamd spam and DKIM settings, Sieve auto-replies, Maildir read/write, sending, rollback of a failed reload | Built in Python, tested against `FakeSystem` and **real Maildir folders** |
| SHA-512 crypt for Dovecot passwords | Written from the specification; **matches both published test vectors** |
| SPF, DKIM, DMARC, MX published into the PowerDNS zone (existing TXT records kept, old SPF replaced, long DKIM key split into pieces) | Built, tested against `FakePowerDns` (DNS must be configured, see N5) |
| Quotas (per mailbox, plan maximum), mailbox/forwarder limits, hourly sending limit per mailbox | Built; limits come from the plan (`email_accounts`, `forwarders`, `mailbox_quota_mb`, `emails_per_hour`) and follow plan changes |
| Abuse control: hourly limit when sending from webmail; `scan_logs` blocks a mailbox whose recipients exceed the limit (Postfix `blocked_senders` map) | Built, tested on sample log lines. **Nothing runs `scan_logs` periodically yet** (the Go agent or a timer must) |
| Reputation: blacklist checks of each mail node's addresses plus queue size, staff screen, `check_mail_reputation` command, one audit event when it turns bad | Built, tested with a fake resolver. **Never run against real blacklists.** A refused or failed query is reported as "could not ask", never as clean. Schedule the command (hourly) yourself |
| **Real Postfix, Dovecot, Rspamd, Roundcube** | **Never run.** The generated maps and settings are written from their documentation. Expect fixes on first contact (Postfix lookup types, Dovecot `userdb`, Rspamd setting names, Sieve plugin setup, TLS) |
| Roundcube or any external webmail | **Not built.** Webmail is WHPanel's own simple viewer; no attachments are shown or sent |
| IMAP/SMTP access for mail clients | The mail node's Dovecot and Postfix would serve it; **nothing configures TLS certificates for the mail host** (use N5's certificate for the mail host name, not yet wired) |
| Aliases beyond forwarders, catch-all, mailing lists, greylisting, per-domain outbound relay | Not built |
| Migration of mailboxes from cPanel | Not built (ADR 19) |
| Go agent | Written in Go (`agent/`) for every command of this stage, checked against the Python reference by golden scenarios and against live Django end to end on a pretend machine. **The real-machine paths have never run on a real node** (see agent/README.md) |

## Rules worth knowing

* Message **content never stays in WHPanel**: lists, folders and messages are wiped from the job record as soon as the screen has read them, drafts/sent copies live only on the mail node, passwords become `***` when the
  job ends, and nothing of it reaches the audit trail. Message text is the one other place (besides file names and content) where the agent accepts free text; the catalogue allows it only on `MAIL_*` commands.
* Passwords are hashed on the node (SHA-512 crypt); only the hash is in `/etc/dovecot/whp/passwd`.
* Sending builds the message on the node and runs `sendmail -i -f <address> -- <recipients>` from an **argument list**, with every recipient validated; line breaks in a subject cannot add headers.
* An address can only be used for the account's own domain; the node checks that the domain belongs to the asking account for every command.
* A configuration change is applied for all domains together; if a map rebuild or reload fails the previous configuration is put back.
* Terminating the website queues removal of the mail domain, its mailboxes and its DKIM key from the mail node (mail is deleted with it; the website archive does not include mail).
* On Windows the Maildir flag separator is `;` instead of `:` (Windows cannot name files with a colon); this exists only so tests can run here and is not used on Linux.

## Settings and operation

Enrol a server with capability `mail` (N2), give it a stable IPv4 address and a host name that resolves to it (the MX target), and run `python manage.py check_mail_reputation` hourly.

## Tests

`apps/agent/tests/test_native_mail.py` (30): hash vectors; first use and node choice; DNS records; no DNS zone; no healthy node; tool offered only with a mail node; mailbox life cycle and rules; forwarders; escaped Sieve
auto-replies; spam and routing; rollback; inbox reading; star/move/trash/delete; sending, copy in Sent, draft cleanup; header injection and bad recipients; hourly limit; log scanning and blocking; account isolation (also at the node);
plan change and termination; no content or passwords kept; the real Email page; typed commands; blacklist logic; reputation saving, display, once-only alert and the command.
