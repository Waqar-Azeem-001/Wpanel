# Phase E5 — Invoice and email design, sign-in, payment details, affiliates, tickets, customer menu, plan limits

Status: implemented locally (owner requests, 2026-09-27). Nothing is deployed.

## 1. Invoice PDF

Rewritten: the brand's logo (or name) and colour, a status badge (PAID, UNPAID, PARTLY PAID, OVERDUE), a summary band (issued, due, order), billed-to and from blocks, the lines, totals with the amount due highlighted, payments received, a **PAID** watermark once settled, a **HOW TO PAY** block only while something is owed (it lists each active payment method that has details, and the reference to use), notes, and a footer with the page number. A quote gets the same look. Still deterministic (same document → same bytes).

## 2. Emails

* New layout (`templates/layouts/email.html`): brand header, a heading, the message, one clear button, a footer. The invoice emails show the invoice as a small table (lines, total, due date) and how to pay.
* **The PDF is attached**: an invoice email carries the invoice as it is when the email is sent — **unpaid → `INV-…-unpaid.pdf` marked UNPAID**, **paid → `INV-…-paid.pdf` marked PAID** (partly paid → `-partly-paid`); the quote email carries the quote. If the PDF cannot be made the email still goes, without it. The attachment is made at delivery from the stored document, so a retry always sends the current state.
* Plain-text emails without a designed layout are turned into the same look (wrapped lines joined, lists kept).

## 3. Where mail comes from and where alerts arrive

*Setup › Email provider › Sending and receiving* (Super Admin, or anyone who may manage providers):

* **From**: the address on the email provider (and the envelope sender, if set — see E4).
* **Replies go to**: a Reply-To address, so customer replies reach a mailbox you read.
* **Send team alerts to**: one address per line. Each gets a copy of the alerts you tick — new order, customer says they have paid, new ticket or customer reply, new account, affiliate application, cancellation request — in addition to the staff's own bell and email preferences.
* New alerts wired in this round: *a new order is placed* and *someone creates an account*.

## 4. Payment details

*Billing › Payment methods*: a method now has a **type** (bank, JazzCash, EasyPaisa, other), bank name, account title, account number / IBAN / mobile number, SWIFT / BIC and a payment note. Add as many as you like, edit any in place, switch them on and off, reorder them. Customers see the details on the invoice, at checkout and on the order page; the invoice PDF lists them under HOW TO PAY. The code is made from the name.

## 5. Reported payments (Confirm / Reject)

Confirm and Reject sit side by side on the invoice page and in the transaction list (they were stacked). Confirming still needs the permission, marks the invoice paid and sends the paid email with the PAID PDF.

## 6. Affiliates

* **How it works**: a customer opens *Account › Affiliate*, accepts the terms and (optionally) enters where to be paid. If approval is required the application waits; otherwise they are active at once. They get a link `…/r/<code>/` with **Copy** and **Share** buttons; anyone who arrives through it and signs up is attributed to them. Commissions become payable after the hold days, above the minimum payout; staff approve them and record each payout.
* **Where applications show for the owner**: the bell and email (alert copy if ticked), the dashboard's *Affiliate applications* row, and *People › Affiliates* filtered to pending.
* **Payout account**: how to be paid (bank, JazzCash, EasyPaisa, other), bank name, account title, account number and a note — asked when joining and editable later; staff see it as "Pay to" on the affiliate page. An account number needs a method.

## 7. Open-a-ticket page

Two columns: the form, and cards on the right — search the help centre with a few suggested articles, the customer's recent tickets (when they have any) and short tips for a faster answer.

## 8. Sign-in

A card on a soft field with the logo above it, "Keep me signed in on this device" (30 days; otherwise the session ends with the browser), a "Not registered yet?" strip, and on the dark side a list of help-centre articles.

## 9. Customer menu (Super Admin / settings managers)

*Settings › Customer menu*: for every entry in the customer's menu — show or hide, another name, and the order. The **store categories are listed under Services** (WordPress hosting, shared hosting, VPS, SSL, domains…), each linking to the store filtered to it, so customers can go straight to a category. The way home is never hidden. *Reset to standard* removes the changes. Stored in the database and cached.

## 10. Plan limits and the product form

* **Limits are rows now**: a title and a limit each, **Add a limit**, a remove button on every row, and quick-add buttons for the usual ones (disk space, bandwidth, email accounts, databases, FTP, subdomains, parked/addon domains, websites). A limit can be any title ("Free SSL" → "Included"); a known title takes a whole number, empty means unlimited; a title listed twice is refused. A removed row is removed from the plan. The public plan page shows them under **What is included**.
* **Add a product is simpler**: name and type, features, the first price and the limits are on the page; the server package, auto-provision, renewal default, upgrade suggestion and recommended add-ons are under a collapsed **More options** (it opens by itself when something there needs attention).

## 11. Verified

`apps/core/test_owner_tools3.py` (21 tests) plus the updated product, navigation and console tests. Mutation checks: the unpaid/paid PDF and file name, HOW-TO-PAY only while owed, alert copies only for ticked alerts, Reply-To, remember-me versus session cookie, a duplicated limit title, the payout method rule and the dashboard's affiliate row each make a test fail. Real browser (Chromium, 1440 and 390 wide): payment methods, the invoice and email, sign-in, open a ticket, the affiliate page, the customer menu and the product form with adding and removing limit rows.

## 12. Limits

* Each email's wording is still text (edited in E4's template editor); the designed layout is built around it.
* The From address is the one on the email provider; a different From per email type is not offered.
* The alert addresses are plain copies; they are not a login and cannot reply into a ticket.
