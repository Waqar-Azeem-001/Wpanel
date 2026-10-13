# Website workspace: the Hostinger-style flow

Customer flow: **Websites** (cards) -> **Manage** -> the website's own workspace: one menu on the left, its Overview as a dashboard of tool tiles.

| Part | What it does |
|---|---|
| Websites (`templates/hosting/customer/list.html`) | A grid of website cards: thumbnail, domain, plan, status, SSL and PHP, storage and bandwidth, **Manage** and **Visit**, a menu for quick tools |
| Workspace menu (`apps/core/portal.py: service_sidebar`) | Panel **Website**: Overview, then the tools in groups (Files, Databases, Email, Domains, Advanced). Panel **Plan**: Information, Add-ons, Upgrade / downgrade, Request cancellation, Open a ticket |
| Tools the server cannot do | **Not listed** (no "Soon" rows); the page of an unsupported tool still says so if someone opens its address |
| Overview (`detail.html`) | Usage cards, **Manage your website** tiles (only supported tools, each with one line of what it does), billing term with Renew / Upgrade, plan details |
| Phone | The menu is a drawer opened by the **Menu** button next to the breadcrumb; the left column is hidden below the large breakpoint (`sidebar.workspace`) |
| Header (`_header.html`) | Breadcrumb, domain, plan and renewal date, status, Visit site. The old row of tabs is gone: its pages live under **Plan** in the menu |

Not changed: URLs and URL names, permissions, the capability model (a tool appears when the provider supports it), domains' own tab row.

## Checked
Screenshots of the customer screens at 1366 and 390 px wide with the demo provider (every tool supported): no horizontal overflow, no console errors; the phone drawer opens. Dark mode and keyboard navigation were **not** checked.

## Add website (guided steps), home page, domains

| Part | What it does |
|---|---|
| **Add website** (`/account/hosting/new/`, `apps/hosting/views_wizard.py`) | Plan -> Domain -> Confirm. Plans are the active hosting products with a price; the domain is checked with the same rule DNS uses; the billing period comes from the plan's own prices (a made-up one falls back to a real one). The last step **posts to the existing cart** (`orders_customer:cart_add_hosting`): nothing is created, priced or charged by the steps themselves. State is in the address, so each step can be linked and the back button works |
| **Home** (`clients/customer/dashboard.html`) | After the overdue notice and the figures: **Your websites** (cards, with Add website), **Your domains** (list with Manage, and a search box for a new one), then support tickets |
| **Domains** | The same workspace shape as a website: menu *Domain* (Overview, DNS records, Nameservers, Auto renew, Registrar lock) and *Registration* (Renew, Request cancellation, Open a ticket); a Menu drawer on a phone; breadcrumb and header instead of the row of tabs |

Checked with screenshots at 1366 and 390 px (no overflow, no console errors). Not checked: dark mode, keyboard use, a real payment through checkout.
