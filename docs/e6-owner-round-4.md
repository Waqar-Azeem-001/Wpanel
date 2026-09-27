# Phase E6 — Invoice PDF polish, header, footer, sign-in, payment-method delete, tighter spacing

Status: implemented locally (owner requests, 2026-09-27). Nothing is deployed.

## 1. Invoice PDF

* **FROM** was only the brand name because the seller details were empty. It now shows the company (bold), address, email and phone from *Billing › Settings*, and where those are empty falls back to the brand name and the support address in Brand settings. Fill *Billing › Settings* (company name, address, phone, tax number) to complete it.
* The **PAID** mark is drawn *after* the page content, so the tables no longer hide part of it. A colour bar across the top, the buyer's name in bold, right-aligned number headings, "This invoice has been paid in full" or "Payment is due by …" at the foot.

## 2. Header

The burger button was drawn on wide screens (an icon style overrode its hide rule); it now shows on phones only. The gap between the header and the page is smaller.

## 3. Footer

A proper footer for the store and the customer area: the brand and a line about it, **Store** (plans, add-ons, register a domain), **Support** (help centre, open a ticket, my tickets, email us), **Account** (overview, orders, invoices, affiliate programme; or sign in / create an account when signed out), and the copyright line. Staff pages get a slim footer (copyright, dashboard, help centre, support email).

## 4. Sign-in

The dark left panel is gone. Sign in, create account and password reset are one centred card on a soft field, the logo above it, the small print above the "Not registered yet?" strip, and (sign in) up to three help-centre articles below.

## 5. Payment methods

A **delete** button (with a confirmation) on every method. Payments and orders that used it keep its name; only the link is removed. Audited as `payment_method.deleted`. The add/edit form is in two columns.

## 6. Spacing across the project

Fields inside a grid were spaced twice (the grid gap plus the field's own margin). Now once: form fields sit .75rem apart, cards and columns are tighter, and the empty area under short pages is gone.
* **Tickets**: the conversation, the reply box and a side panel (reference, status, department, priority, dates, related hosting or domain, close, tips).
* **Billing › New invoice / quote**: search box plus a list of recent clients (or the matches) that fills the width instead of one small card.
* **Email provider**: providers and "Sending and receiving" on the left, the add/edit form on the right in two columns, the switches as compact pills.

## 7. Verified

`apps/core/test_owner_tools3.py` (31 tests) and the updated shell tests. Browser (Chromium, 1440 light and dark, 390): sign-in, client dashboard and footer, ticket page, new invoice, email provider, payment methods, staff dashboard — no overflow, no JS errors. The PDFs were rendered to images and inspected.
