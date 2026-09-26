# Phase E4 — Mail that is "sent" but not received, sign-in for new clients, editable email wording, categories, better forms and numbers

Status: implemented locally (owner requests, 2026-09-26). Nothing is deployed.

## 1. Mail says "sent" but never arrives — what was found

Checked read-only against the real provider (no message was sent):

* The mail server accepts the login, the sender and the recipient (`235`, `250 OK`, `250 Accepted`): the send path works.
* The sending server is `server.webhostera.pk` = `173.212.243.56`. The SPF record of **webhostera.pk** (the From address) does **not** list that address (it lists 212.23.222.72, 135.181.161.28 and mailbaby), so receivers see *softfail* and put the mail in Spam or drop it. The record of **myaiwhe.com** (the SMTP login's domain) does list it.
* What changed:
  * **Envelope sender** on the email provider (`EmailProvider.envelope_from`): the address the receiving server checks SPF against, separate from the visible From. In the owner's local database it was set to `info@myaiwhe.com`, so SPF now passes. The permanent fix is in DNS: add `ip4:173.212.243.56` to webhostera.pk's SPF record.
  * **Check deliverability** (Setup › Email provider): SPF (with include/a/mx/ip4), DKIM key, DMARC, and the From/login domain mismatch, each with what to do. If this computer's DNS is blocked it falls back to DNS-over-HTTPS (`DNS_OVER_HTTPS_URL`) and says so rather than reporting "no record".
  * The test email now says "the server accepted it, which is not the same as delivered".

## 2. A client added by staff cannot sign in

A new client's owner has no password until they use the emailed link, so when the email does not arrive they are "Invited, not set yet". Now:

* **Super Admin only** — *Add client › Set a password now*: the owner can sign in at once and no email is sent.
* **Super Admin only** — *User › Password › Show a sign-in link*: a one-time link to hand over yourself (works for a few hours, audited without the link).
* Fixed the overlapping name, status and button at the top of the user page.

## 3. Email templates (Super Admin)

*System › Notifications › Email templates*: every email the portal sends (subject and text), with the variables you can use, a **Preview** (every variable shown as «its name»), **Save wording** and **Reset to default**. Stored in the database, used at once for new emails; an email that carries a one-time link must keep `{{ link }}`. The wording runs in a locked-down template engine: built-in tags and filters only, no file inclusion, and the person is exposed as name and email only (never the password hash). If a saved wording stops rendering, the default is sent instead. Edits are audited.

## 4. Categories, product and add-on forms, prices

* **Store categories are data now** (*Products › Categories*): add, rename, reorder, choose an icon and the heading group, hide, delete when empty. Products and add-ons are put in one (the form's **Category**; by default from the type or kind). A product with no category still shows under "More plans".
* **Product form** in sections — Basics, First price, Provisioning, Resource limits (one box per limit instead of JSON; limits the form does not show are kept), Selling more (recommended add-ons show their price).
* **Add-on form** — the kind, the category and a **price** box; the add-on list shows kind, category and price; the plan page shows each recommended add-on's price.

## 5. Invoice and quote lines

Add a line, remove any line (×), a running lines total, styled inputs and an amount per line. The server still recomputes everything; a removed row is simply left out.

## 6. Reports and dashboard

* **Reports**: 4 sections of numbers (Money, Orders and customers, Services, Support: 20 figures such as income today/month/year, unpaid balance, refunds, average order, orders awaiting payment, active domains, expiring in 30 days, unassigned tickets) above a **grid of report cards** with icons. A figure is shown only to someone who may run the report it links to.
* **Dashboard**: seven more tiles (active domains, income this year, refunds this month, payments to confirm, average order, new customers today, tickets resolved), each for the people who may open the area. Money is summed in the store currency only.

## 7. Verified

`apps/core/test_owner_tools2.py` (26 tests) and `apps/products/tests/test_categories.py` (12 tests) plus the updated console, setup, crawler and product tests. Mutation checks: the Super Admin checks (sign-in link, password when adding a client, email wording), the safe-user wrapper, the link-must-stay rule, the envelope sender, SPF include handling, the category-in-use guard, "More plans" and the kept limits each make a test fail. Browser (Chromium, 1440 and 390): sectioned forms, add a category, add and remove invoice lines with the running total and save, reports, dashboard, the email template preview and save, the user page. No JS errors, no overflow.

## 8. Limits

* Email wording is text only; the branded HTML layout is built around it.
* The deliverability check reads public DNS; it cannot see whether your mail server actually signs with DKIM.
* Report and dashboard money figures are in the store currency; invoices in other currencies are not added into them.
