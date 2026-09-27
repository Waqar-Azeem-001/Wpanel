# Phase E8 — Order, account and invoice alerts that reach the owner; policy pages

Status: implemented locally (owner request, 2026-09-27). Nothing is deployed.

## 1. Why nothing arrived

The team alerts (a new order, a new account, a reported payment) reached the staff **bell** only; the email copy went only to addresses set under *Setup › Email provider*, and none were set. Invoice changes were not announced to the team at all, and the messages had almost no detail.

## 2. What changed

* **Default recipient**: with no alert address set, the alerts go to the **Super Admins' own email addresses**. Setting addresses replaces that. Each kind can be switched off.
* **Details in the message**: a new order lists the client (name, email, phone), each item with its price, discount, tax, total and the payment method; a new account lists name, email, phone and company; an invoice lists the client, its lines, total, paid and balance due, and the due date; a reported payment adds the method and reference. The email has the same layout as the others, with an "Open it in the portal" button.
* **New alerts** (bell and email copy): an invoice is **issued, paid (or part-paid), refunded, cancelled**; an order is **activated or cancelled**. The person who did the action is left out of the bell (they know), the alert copies still go out. Two new switches on the *Sending and receiving* card: "An invoice is issued, paid, refunded or cancelled" and "An order is activated or cancelled".
* An alert can never break the payment, invoice or order that caused it.

Mail still has to be deliverable: see *Check deliverability* (E4) if messages are "sent" but not received.

## 3. Policy pages

*Settings › Brand › Policy pages*: rows of a title and a web address (add, remove, order kept; up to 12; each must be an http/https address). They show in the **footer**, under **sign in** and **create account**, and at **checkout** ("By placing an order you agree to our policies"), opening in a new tab. The five Web Host Era pages (Acceptable Use Policy, Terms of Services, Refund Policy, Shipping Policy, Return Policy) are loaded into your local database and into `seed_webhostera`.

## 4. Verified

`apps/core/test_owner_tools3.py` (5 new alert tests) and `apps/branding/tests/test_branding.py` (5 new policy tests). Browser (1440 and 390): sign-in and footer show the five links; the brand page adds, removes and saves rows.
