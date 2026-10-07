# H12: Client -> Services -> Manage Hosting (Admin Access)

**Source:** next-generation roadmap, H12. Local only.

## Rule 1 inspection
Staff with the view-hosting permission could already open any account's `/account/hosting/<id>/` screens, and the gateway let a
manager change things. But nothing told them (or the customer) that it was support acting, there was no reason, no time limit and no
audit of private reads. H12 keeps the same engine and adds the controls around it.

## What was built
- **Entry:** on the staff service page, "Manage this customer's hosting" asks for a reason (5 to 200 characters, for example a
  ticket number) and opens the customer's own hosting screens. Staff who open those screens directly without starting it are sent back
  to the service page with a message (a small middleware catches the missing grant).
- **No customer password:** staff use their own sign-in; nothing of the customer's is requested, shown or stored.
- **Visible Admin Access context:** a banner on every hosting screen (the website workspace, every tool, and the mail window) says
  who the customer is, which service and username, who the staff member is, whether their role can change things, minutes left, and has
  Client, Service and End Admin Access buttons (return links).
- **Time-boxed and per account:** an hour; the grant is for one account only and ends with the button or on expiry.
- **Role-based actions** (checked by the same gateway as for customers): looking needs `view_hosting`; changing needs `manage_hosting`;
  reading a customer's private content (webmail, mail folders and messages, database rows) needs `manage_hosting` and is audited. A
  support agent can therefore look at files, DNS, usage and settings but cannot change anything or read mail and database rows.
- **Audit:** `hosting.admin_access_started` (reason, minutes, whether the role can change), `hosting.admin_access_ended`,
  every change with `admin_access: true`, and `hosting.admin_read` for private reads. The customer's own Security events list marks what
  support did with a "Support" tag.
- A staff member who is also a contact of the client is treated as the customer (no banner, no grant).

## Verified
13 tests (`test_admin_access.py`): direct access refused without a grant, reason rules, start audit, banner content and return links on
several screens, no banner for the customer, same engine and `admin_access` audit, the customer's activity list marks support, a
view-only role is refused changes and private reads, a managing role's private reads are audited, ending, one-hour expiry, one account
only, non-live account and customer refused. Hosting and core suites pass.

## Deferred
Telling the customer by email or notification when support opens their account (a product decision), approval by a second person for
sensitive accounts, a staff-side list of active Admin Access sessions, and the 375 / 768 px check.
