# H4: Email Management

**Source:** next-generation roadmap, H4. **Date:** 2026-10-06. Local only.

## Rule 1 inspection
Only H0's thin contract existed (mailboxes, quota, password, forwarders). No screen, no provider implementation, and none of
the roadmap's other items: autoresponders, routing, spam controls, usage, webmail access. Reused: the hosting-control
gateway, the workspace menu, the shared password helpers (now `passwords.js`, used by Databases and Email), the demo provider.

## What was built
- **Contract v1 additions:** `email_limits` (mail domain, plan limits, quota range, password rule), autoresponders
  (`list/set/delete`), `get/set_mail_routing` (local or remote), `get/set_spam_settings` (on/off, low/medium/high, delete or
  file), `webmail_access` (`external` one-time https address, or `embedded`), `list_messages` and `read_message` for the
  embedded inbox viewer.
- **Demo provider** (`demo_email.py`): the account's own domain only; lower-cased validated names; plan limits (10 mailboxes,
  20 forwarders); quota 10 to 5120 MB, never below what is used; forwarders must be valid, cannot point to themselves, cannot
  duplicate, cannot form a loop, and can only start at your own domain; automatic replies need a subject and message with length
  limits; deleting a mailbox removes its mail, reply and forwarders; passwords only as a hash. No mail is sent or received.
- **Screen** (`views_email.py`, `email.html`, `email.js`): counts against the plan; create a mailbox (name at your domain,
  size, password with Generate and show/hide); mailbox list with storage bars (amber at 80%, red at 95%), Webmail, change
  password, change size, automatic reply, delete; forwarders; where mail is delivered; spam protection (on/off, strictness,
  delete or keep in the Spam folder).
- **Webmail:** a simple inbox viewer for the demo provider (unread marker, message pane, marks read when opened, values escaped);
  a provider with Roundcube sends the person there, and only to an https address.
- Passwords go straight to the provider, never stored, logged, shown or audited; audit records the address and the change.

## Verified
28 tests (the provider's rules, the screens end to end, errors as messages, limits, passwords absent from audit and pages,
webmail with escaping, secure-address-only redirect, strangers get 404, a provider without email shows the not-available page).
Hosting, client-area, crawler, link-integrity, design and navigation suites pass. Browser check: no console errors.

## Update 2026-10-06: webmail as a proper mail client
Owner feedback: the inbox sat inside the website menu with all its details; it should be a focused mail screen with a way
back. Rebuilt as its own page (no website menu or site header around it):
- Top bar with **Email accounts** (step back), the mailbox, and **Compose**.
- Three panes: folders (**Inbox, Sent, Spam, Trash**, with unread counts), the message list (unread in bold with a dot), and the
  reading pane (From, To, date, body; **Reply**, **Mark as spam** / **Move to inbox**, **Delete** and **Delete for good** from
  the trash). On a phone the panes become one at a time with "Back to the list".
- **Compose** (to up to 5 addresses, subject, text). Mail sent to another mailbox on the same account really arrives in its
  inbox as unread; mail to other addresses is only recorded in Sent (no real delivery). Errors keep what was typed.
- Contract: `message_folders`, folder-aware `list_messages`, `move_message`, `delete_message`, `send_message`. Messages saved
  before folders existed are read as the inbox.
- Found and fixed while testing: a page variable named `messages` hid Django's flash messages; renamed (`mail_items`).
- 12 more tests (40 for H4), a screenshot check at desktop and phone width, no console errors.

## Deferred
Sending and receiving real mail and a full webmail client (needs a real provider: H14, or the native mail platform N7),
catch-all and per-domain routing, mail filters, mailbox import, additional domains (H5), DKIM/SPF/DMARC records (H5 DNS),
the 375 / 768 px check.
