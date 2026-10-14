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

## Update 2026-10-06 (third): a proper composer
Owner feedback: the message box was too small to write a whole message. Now:
- The message box **grows as you type** (up to 70% of the screen, then scrolls), is large and comfortable from the start, and has
  a counter (`6,204 / 10,000`, turns amber past 90%).
- **Reply** opens with the sender, "Re: subject" and the original quoted line by line, the cursor above the quote.
- **Ctrl+Enter** sends; the Send bar stays in view at the bottom; From (read-only), To with a hint, Subject, a close button.
- **Draft kept in this browser**: closing the tab or reloading restores To, Subject and the text with a "draft restored /
  discard it" note; sending clears it; a reply or a failed send is never overwritten by an old draft.
- Tests: a long message (9,500 characters) is sent and read back whole; the reply quote; the composer markup (30 for H4 email
  screens). Browser check: the box grew from 280 to 630 px while typing, the draft came back after a reload, no console errors.

## Update 2026-10-06 (fourth): the webmail redesigned as a full-screen mail app
Owner feedback: the look, the way Compose worked, plain-text-only writing and the colours were all disliked. Rebuilt:
- **Full screen, its own stylesheet** (`mail.css`) and page (it extends the bare root layout, so no website header, menu or
  footer): a slim top bar with a back arrow to Email accounts, "Mail", a pill **search** box (searches the open folder's
  senders, subjects and snippets), and the mailbox on the right.
- **Gmail-style layout:** a left rail with a big **Compose** button, the four folders with unread counts and a
  mailbox storage bar; a message list with an initial avatar, sender, time, subject and a **snippet**, unread in bold with a dot;
  a roomy reading pane with a clear action bar (Reply, Spam, Delete) and a Reply button under the message. Phone: one pane at
  a time, folders as a strip, a floating compose button, compose as a full-screen sheet.
- **Compose is a floating window** at the bottom right (minimise, close, draft kept in the browser), not a pane.
- **Rich-text editor**: bold, italic, underline, bulleted and numbered lists, quote, links, clear formatting; paste is always plain
  text; Ctrl+Enter sends; a live counter against the 10,000 character limit; replies quote the original.
- **Safe HTML** (`apps/hosting/mailhtml.py`): messages travel as HTML plus a text version. Everything sent or shown is reduced to
  an allowlist (basic formatting tags; links only to http, https or mailto and always opening safely); script, style, images,
  forms, iframes, event handlers, inline styles and comments are removed; text is escaped; it is cleaned on the way out and
  again on display. 25 tests for the cleaner and the screens.
- Contract: `send_message(..., html)`, `MessageInfo.snippet` and `.html`. Browser check: formatted a message in the editor, sent
  it between mailboxes, no console errors.

## Update 2026-10-06 (fifth): reply in place, every folder, its own window, mail settings
Owner feedback: Reply should not open a separate window; all the usual folders; the mail should open in its own window with a
way to sign out; basic mail settings such as a signature. Done:
- **Reply is inline**, under the message it answers (To, Subject, the signature, the quoted original already filled in); the
  floating window is only for new messages and drafts.
- **Seven folders:** Inbox, Starred (a view of starred mail), Drafts, Sent, Archive, Spam, Trash. Star, Archive, Spam and
  Move to inbox actions; a draft or a message already in Trash is deleted for good.
- **Drafts live on the server:** the compose window saves a draft quietly while you write (every 2 seconds after a change, and when
  closed), shows "Draft saved", opens again from the Drafts folder, and the draft disappears when the message is sent.
- **Own window:** the Webmail button opens a new tab. The account menu (top right) has **Mail settings**, **Close mail** (closes
  the window, or goes back to Email accounts) and **Sign out of WHPanel** (full sign-out).
- **Mail settings page:** profile (your name shown to recipients, reply-to address, storage), **signature** with the same rich
  editor (added to new messages and/or replies), automatic reply, and change password.
- Contract: `set_message_starred`, `save_draft`, `get_mail_profile`, `set_mail_profile`; `send_message(..., draft_id)`; folder
  names `drafts` and `archive`, and the virtual `starred`. 71 tests for mail and the cleaner now; browser check: inline reply,
  account menu, draft autosave, settings, no console errors.

## Deferred
Sending and receiving real mail and a full webmail client (needs a real provider: H14,),
catch-all and per-domain routing, mail filters, mailbox import, additional domains (H5), DKIM/SPF/DMARC records (H5 DNS),
the 375 / 768 px check.
