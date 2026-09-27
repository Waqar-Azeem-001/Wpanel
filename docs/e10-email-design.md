# Phase E10 — Designed emails: an editor, pasted HTML, and the logo inside the message

Status: implemented locally (owner request, 2026-09-27). Nothing is deployed.

## The logo

The email showed a broken picture because the logo was a web address (`SITE_URL` + `/brand/logo/`), and a mail program cannot fetch `localhost`. The logo now **travels inside the email** (an inline picture, `cid:brand-logo`), so it shows wherever the message is read, even before the portal is on a public address and even when the reader's program blocks remote pictures. The stored copy in the email log keeps the address. If the picture cannot be embedded the email is sent without it.

## Writing a message: text, an editor, or your own HTML

*Setup › Notifications › Email templates › (a message)* now offers two ways to write it:

* **Plain text**: as before, put in the standard layout (logo, colours, footer).
* **Designed message**: an **Editor** (bold, italic, underline, heading, lists, alignment, links, a brand-coloured **Button**, a divider, clear formatting, undo) and an **HTML** tab to paste or write HTML. The two stay in step when you switch. Click a variable in the side list to put it where the cursor is. The design goes inside the branded shell; the headline is the design's own. An optional **plain-text version** is used by mail programs that cannot show a design; left blank it is made from the design.
* **Preview** shows the message inside the branded shell in a sandboxed frame, with every variable as «its name». Nothing is saved until you press Save; **Reset to default** removes your changes.

## Safe by construction

What a Super Admin writes or pastes is cleaned when the email is *rendered* (`apps/notifications/html_clean.py`): variables are filled in first (escaped, so a customer's name cannot carry markup), then only an allowlist of email-safe tags, attributes and style properties is kept. Scripts, style blocks, frames, forms, event handlers, `javascript:` and `data:` addresses, `url()` in styles, comments and anything else are removed; links open in a new tab with `noopener`; tags are rebuilt so they are always balanced. The editor also cleans what is pasted into it, in an inert document. A message that carries a one-time link must keep `{{ link }}`. Broken designs are refused when saving; if a stored one stops working the default is sent.

## Two bugs fixed on the way

* Customised **plain-text** wording HTML-escaped what filled a variable (a name like O'Brien or A & B came out as `O&#x27;Brien`, `A &amp; B`). Text is now text.
* **Preview** failed for any message using the `date` filter (for example the invoice email) because a stand-in cannot be formatted as a date. Every default message can now be previewed.

## Verified

`apps/notifications/test_email_design.py` (44 tests: 20 hostile inputs to the cleaner, rendering, the page, saving, preview, the logo). Mutation checks on the cleaner (URL rules, dropped blocks, attribute and style allowlists, comments, balancing, escaping, the layout button) each make a test fail. Browser (1440 and 390): typing in the editor, the toolbar, inserting a variable, the Button tool, the HTML tab, pasting hostile HTML, preview, save and reload.
