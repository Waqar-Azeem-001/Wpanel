# Phase E3 — Store categories and a redesigned cart and checkout

Status: implemented locally (owner request, 2026-09-26: the cart and checkout design was not liked; do the checkout in the same design as the cart; the front page should show every hosting category; payment method Bank transfer; reference screenshots shared). Nothing is deployed. The references were used for hierarchy and density only; the look is the portal's own tokens and brand.

## 1. Store categories

The front page and `/products/` now show the whole range in three groups, each category as its own block (and `?category=` for one):

| Group | Categories |
|---|---|
| Hosting | WordPress hosting · Shared hosting · Business hosting · E-commerce hosting · Reseller hosting (kept, it already had plans) |
| Servers | VPS servers · Dedicated servers |
| Security and domains | SSL certificates · WHOIS privacy · Domains (the domain search) |

* A hosting or server category lists the active plans of its **product type** (new types: *Business Hosting*, *E-commerce Hosting*). SSL and WHOIS privacy are **add-ons with a kind** (*Add-on kind* on the add-on form: general, SSL, WHOIS privacy) and are listed from it. An empty category says "Coming soon" instead of hiding.
* Home: a tile per category with the real lowest price ("From PKR …"), then the popular plans.
* **Staff fill the categories**: create the plan with the right type, or the add-on with the right kind, in Products / Add-ons. No prices are invented. In the owner's local database *Business Hosting* was re-typed to the new type; VPS, dedicated, e-commerce, and a WHOIS privacy add-on still need to be created (the existing SSL add-on must be set to kind *SSL* and activated).

## 2. Cart

One card per plan or domain beside one **order summary**:

* **Period menu** (the plan's own prices, cheapest term to longest) with the price per month, the real saving against paying monthly (only when a monthly price exists), the price due now and "Renews at … billed …". Changing it reprices at once; recurring add-ons follow the new period or are dropped if they have no price for it.
* **Add-ons as switches** with a *Recommended* tag (staff choice from D-track E2): SSL, backups and so on on a plan; **WHOIS privacy on a domain**, charged per year of the domain. A switch cannot be put on the wrong kind of line.
* The upgrade suggestion, "Register <your domain>", a domain search box, and "Already own a domain? Transfer it to us" (collapsed).
* Summary: each line, subtotal, discount, tax (or "worked out at checkout" for a visitor), total, "Have a coupon code?" (collapsed), **Continue**, and the notes that prices are confirmed by the server and that payment is by bank transfer after ordering.
* Phones: one column, the summary below.

## 3. Checkout

The same cards and the same summary (steps: Cart → Details and payment → Confirmation): a visitor's details (this creates the account) or a customer's billing details; **Payment method: Bank transfer** (preselected, with its instructions; only the active payment methods are offered); notes; one button. The tax appears when the country is chosen.

## 4. Verified

Automated: `apps/orders/tests/test_shop_cart.py` (categories and their contents, add-on kinds, the period menu and savings, moving or dropping recurring add-ons, switches and their isolation, WHOIS privacy priced per year, refusing hosting add-ons on domains and the reverse, WHOIS surviving checkout as an order line) and the updated cart, checkout and store tests. Browser (Chromium, 1440 and 390): home tiles, store navigation, one category, cart with period menu, add-on switch, coupon box, checkout with tax after the country, order placed; no overflow, no JS errors.

## 5. Limits

SSL and WHOIS privacy are sold as add-ons of a plan or a domain in the cart; they cannot yet be bought on their own for something already owned (no add-on provisioning exists; they are recorded on the order for staff to action). No comparison tables, no per-plan "free domain" offer, no saved carts by email.
