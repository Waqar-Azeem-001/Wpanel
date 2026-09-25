# Phase 10 Gap Report: Customer Service & Support

**Date:** 2026-09-25
**Baseline:** Phases 01-09 complete (`3d93cfa`)

## FOUND

- **Permissions** `view_support` / `manage_support` already existed and were already granted correctly (Support Agent: view + manage; Manager and above: view + manage). No permission changes.
- **Foundations to reuse:** the audit log, the notification/email entry point, the shared web helpers, the client-profile "Account records" section (Phase 07), the `client`/`hosting account`/`domain` records a ticket relates to, and the state-machine pattern proven on orders (Phase 09).
- **Two roadmap debts that landed here:** the client profile listed no tickets ("tickets ... will appear as those modules are delivered"), and `/api/v1/tickets/` was named in the API section.
- Searched for any existing ticket, attachment or knowledgebase code: none. There was also **no file-upload handling anywhere** in the project, so nothing to reuse and a security surface to design from scratch.

## PARTIAL / BROKEN

None.

## MISSING (now implemented)

Support overview, tickets (customer and staff, web and API), new ticket, departments, assignment, priority, status, internal notes, predefined replies, attachments, related hosting account and domain, and the knowledgebase - everything on the roadmap's Phase 10 list.

## REUSE

| Reused | How |
|---|---|
| The order state-machine pattern | `support/lifecycle.py`: one table and one `transition()` through which every ticket status change passes, auditing from/to |
| `audit.record`, `notifications.notify` / `send_email` | Every ticket event; reply and confirmation emails; in-app notifications |
| `contact_role`, `single_contact_client` | Who may act for a client; which client a customer's ticket is for |
| `core.utils.unique_slug`, `core.web` helpers, `partials/form.html`, the billing client picker | Slugs, view plumbing, forms, "choose a client" for staff-opened tickets (the picker template is shared, not copied) |
| The record-list section on the client profile | Tickets added as one more section, permission-gated like the others |

## CHANGES

- **New `apps.support` app.** `Department`, `Ticket`, `TicketMessage`, `TicketAttachment`, `CannedReply`, `KBCategory`, `KBArticle`. A data migration seeds the departments (Technical Support, Billing, Sales, General) and the roadmap's eight knowledgebase categories (Getting Started, Hosting, Domains, cPanel, DNS, Email, WordPress, Billing).
- **Ticket states** exactly as the roadmap lists them - Open, Agent Reply, Customer Reply, Pending, Resolved, Closed - defined by *whose move it is*. A customer reply reopens a Resolved ticket; a closed ticket can only be reopened (by staff, or by a customer's reply within a week - as two audited moves, Closed -> Open -> Customer Reply); a Resolved ticket with no reply for a week **closes itself** (Celery beat, daily). An agent can reply and set the next status in one step.
- **Internal notes are enforced in the service layer, not the templates.** A note never changes the ticket's status, never emails or notifies the customer, and is excluded from *every* customer path: the page, the API, and the attachment download (a file attached to a note is 404 to the customer). One function (`messages_for`) decides what a viewer may read.
- **A customer never learns an agent's email address.** Staff replies show the agent's name (or "Support team"), never their email; the API shows a customer only that the ticket is "support"-assigned. (Found by a test - see defects.)
- **Attachments are the security-critical part of this phase**, so they were designed defensively:
  - stored **outside** anything web-served (`PRIVATE_MEDIA_ROOT`, its own docker volume mounted on the web and worker containers but *not* nginx; nginx has no route to it), under random unguessable names - the uploader's filename is display text only;
  - validated **before anything is written**: an extension allow-list (png, jpg, gif, webp, pdf, txt, log, csv - no HTML/SVG/executables), the file's *content signature must match its extension* (a `.png` must start like a PNG; text may not contain NUL bytes), per-file (5 MB) and per-message (5 files) limits, no empty files, filenames cleaned of paths and odd characters, and the stored content type comes from the extension, never from what the browser claimed;
  - served **only** by an authenticated view that re-checks who is asking, always as a download (`Content-Disposition: attachment`, `X-Content-Type-Options: nosniff`, a sandboxing `Content-Security-Policy`, `Cache-Control: private, no-store`);
  - a storage failure part-way through removes the files already written and the whole ticket rolls back.
- **Rules:** customers can open tickets only for their own clients, choose low/normal/high (staff choose urgent), are **rate-limited** (10 tickets an hour), and can attach a related hosting account or domain only if it belongs to the same client. Staff can open a ticket on a client's behalf (the client is emailed, and the email goes to the client - not the agent). A department can have a **default assignee**, who is assigned and notified when a ticket arrives.
- **Screens.** Staff: Support overview (open, waiting for us, unassigned, mine, urgent, resolved this week; the longest wait; what needs a reply; open by department; recent activity), ticket list with filters (search, status, department, priority, mine/unassigned), the ticket page (conversation with internal notes marked, reply with **predefined-reply insertion** - it fills the box for review and sends nothing until "Send" - plus note toggle, next-status, attachments, and assign/status/priority/department controls), departments, predefined replies (`{client}`, `{ticket}`, `{agent}` filled in), knowledgebase management. Customer: My tickets (open/closed), new ticket, the conversation, reply, close. Public: the **Help centre** (categories, search, articles), linked from the navigation and from the new-ticket page.
- **API.** `/api/v1/tickets/` (list with filters, create with JSON or multipart, `reply`, `close`, and staff `status` / `assign` / `priority` / `department`, plus authenticated `attachments/{id}/` download), `/departments/`, `/canned-replies/`, `/kb/categories/`, `/kb/articles/` (public read of *published* content; staff write). Someone else's ticket or client looks exactly like one that does not exist (404).
- **Client profile:** a Tickets section (permission-gated). **Migrations:** `support 0001`, `0002` (seed).

## Defects found and fixed while building it

1. **A customer could see the agent's email address.** A staff reply's author name fell back to the agent's *email*, and that was shown on the customer's ticket page and returned by the API. Found by the test that asserts the agent's address appears nowhere in a customer's response. Staff are now shown by name or "Support team".
2. **Callable file storage cannot be redirected.** I first gave the attachment field `storage=<callable>`, which Django evaluates once at import, so the private location could not be pointed at a test directory (test files landed in the project folder). Replaced with a storage class that reads `PRIVATE_MEDIA_ROOT` on each use.
3. **A customer reply to a recently closed ticket failed** (`Closed -> Customer Reply` is illegal in the state machine). The reply now reopens it first - two audited transitions.
4. **A view-only staff member posting to a ticket got a server error** instead of 403 (an unhandled service error in a view). Now a proper 403.
5. **Templates crashed on an unassigned ticket** (looking up an email on `None`); a small `assignee_label` property replaced the fragile template expression.
6. **My own leftovers:** three times I left placeholder lines (`_ = SomeName`) in new files to silence unused-import warnings; each was removed and the imports fixed instead.

## TEST PLAN

**97 new tests: 885 in total on PostgreSQL** (875 on SQLite; the 10 concurrency tests skip there). All pass against a local PostgreSQL 17.5 before pushing.

- **Services (60):** opening (audit, confirmation email, default assignee notified, every invalid input refused with nothing written, only contacts and staff, staff on behalf with the email going to the client, related records must belong to the client, the hourly rate limit); attachments (stored privately under random names; **nine hostile or malformed uploads** - exe, html, svg, no extension, PNG-named HTML, PDF-named PNG, NUL-bearing text, empty - each refused *before anything is written*, including the valid file sent alongside; size and count limits; filename cleaning against path traversal; content type from the extension); replying (the state moves and audit trail, reply-and-set-status, a reply reopening a Resolved ticket, **internal notes invisible to customers in messages, emails, notifications and attachments**, customers cannot write notes or set statuses, strangers cannot reply, the closed-ticket window); statuses (any legal move, a closed ticket can only reopen, customers may only close their own, auto-close after a week); assignment (only agents, idempotent, notifications), priority, department; visibility; search; the overview; departments, predefined replies and their placeholders; the knowledgebase (published/active only, search, unique slugs, permissions, escaping); view-only staff can read but not act; a storage failure mid-upload leaves nothing behind; private files are outside every web-served path and nginx has no route to them.
- **Web and API (37):** authentication and permissions; the customer flow with a real multi-file upload; a bad upload reported with nothing created; internal notes absent from the customer's page and present for staff; strangers get 404; closing and the closed tab; related hosting limited to the client's own; **attachment downloads** (headers, who may fetch what, an internal note's file is 404 to the customer, anonymous is redirected, a missing file is 404 not a crash, an HTML-looking upload is only ever a sandboxed download); the overview and every filter; predefined-reply insertion sends nothing; every staff action and its permission; staff-opened tickets; department/reply/knowledgebase management; the client profile; the help centre (unpublished hidden, empty categories hidden, article text escaped so a `<script>` cannot run); the API end to end including multipart upload/download, customers never receiving internal notes or an agent's identity, scoping, 409 on illegal moves, and the public knowledgebase.
- **Browser check:** headless Chromium at 1366, 768 and 375px: help centre and search; a customer opens a ticket - first with an `.exe` (refused) then a real PNG (accepted); an agent inserts a predefined reply, replies with a next status, leaves an internal note, assigns, sets priority, and visits every management page; the customer then sees the reply but **not the note, not the agent's email**, downloads their file (verified: PNG bytes, `attachment`, `nosniff`) and replies. No JavaScript errors, no overflow.

## PHASE 10 EXIT CRITERIA

| Criterion | State |
|---|---|
| Existing implementation inspected, no duplicates | ✅ |
| Support overview, tickets, new ticket, departments, assignment, priority, status, internal notes, predefined replies, attachments, related service/domain | ✅ |
| Ticket states Open -> Agent Reply -> Customer Reply -> Pending -> Resolved -> Closed | ✅ one table, audited |
| Knowledgebase (Hosting, Domains, cPanel, DNS, Email, WordPress, Billing, Getting Started) | ✅ |
| Uploads validated; files never publicly reachable | ✅ tested |
| Authorization tested; full suite green on SQLite and real PostgreSQL | ✅ 885 |
| Migrations, `makemigrations --check`, OpenAPI schema with no warnings | ✅ |
| Browser check (desktop, tablet, mobile) | ✅ |
| CI green | pending push |

## Known limitations / deferred

- **No antivirus scanning of uploads.** Files are restricted to safe types, checked by content, stored privately and only ever downloaded (never rendered), but they are not scanned for malware. Add a scanner (for example ClamAV) before accepting files from the public in production; recorded as pre-launch work.
- **No reply-by-email and no email-to-ticket.** Customers reply in the portal; the notification emails link back to it (email piping arrives with the email work in Phase 11).
- **Staff are notified only through assignment.** A ticket in a department with no default assignee shows on the overview and list but notifies no one (Phase 11 adds team notifications).
- **No SLA timers, escalation, ticket merging, customer satisfaction rating or canned-reply categories.**
- **Knowledgebase articles are plain text** (paragraphs and line breaks; no images, formatting or versioning) - rendered escaped by design.
- **Attachments are not deleted** when a ticket is (tickets are not deletable); a retention/cleanup policy belongs with data-retention work in Phase 16/17.
- **A customer cannot change a ticket's priority after opening it** (staff can).
