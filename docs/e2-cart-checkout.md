# Phase E2 — A real cart and checkout for visitors, with upsell and cross-sell

Status: implemented locally (owner request, 2026-09-26: "if someone arrives choosing a plan from the first page there should be a proper cart and a proper checkout; upsell and cross-sell in products; it just shows Sign in and that is not good"). Nothing is deployed.

## 1. What was wrong

The plan page and the domain search only showed "Sign in or create an account to order". A visitor who chose a plan on the front page hit a login wall, and the plan they chose was lost.

## 2. The flow now

1. **Anyone can fill a cart.** "Add to cart" on a plan page and on a domain search result works for a visitor. Their cart is a *guest cart*: a `Cart` row with no user and no client, found only through a random token kept in their session (`apps/orders/guest.py`). Nobody can see or change another visitor's cart. It holds selections only, so nothing can change a price. The header shows a Cart link with the number of lines.
2. **Cart page** (step 1 of 3): lines with remove, the discount code, a domain transfer, the summary that stays in view, and the suggestions in section 3.
3. **Checkout** (step 2): a visitor gives first name, email, country (for tax), a password and a payment method, and gets an account **in the same step**; "Already have an account? Sign in" keeps the cart. Choosing the country re-shows the totals with the tax (what was typed is kept, passwords are never sent back). A signed-in customer just confirms their saved billing details and pays, as before.
4. **Confirmation** (step 3): the existing order page, with the payment instructions (payments stay manual). The person is already signed in and is told to verify their email.
5. **Signing in keeps the cart.** When someone signs in (or registers) with a guest cart, its lines join their own cart (duplicates skipped, add-ons follow their plan, the coupon comes along) and the guest cart is deleted. Staff and customers with several accounts leave it as it is. Guest carts untouched for 30 days are deleted daily.

Order of work at checkout for a visitor: the cart is checked (`price_cart` strict, including domain availability) **before** an account is created; a bad cart creates nothing. If the account is created and placing the order then fails, the person is told, keeps the account and the cart, and can retry. An email that already has an account is told to sign in.

## 3. Upsell and cross-sell (chosen by staff, nothing guessed)

* **Upsell** — *Products › edit a plan › "Suggest this bigger plan"*. In the cart, that plan's line shows "Upgrade to X (only N more)"; one click swaps the plan and keeps the domain, billing cycle and add-ons. Only shown when the bigger plan is active, orderable and priced for the same cycle; refused if it would duplicate a line. The plan page also says "Need more room?" with a link.
* **Cross-sell, add-ons** — *"Recommended add-ons"* on the plan. They come first in the cart's "Add to this plan" list with a *Recommended* badge and are listed on the plan page.
* **Cross-sell, domain** — for each plan whose domain is not being registered or transferred in the cart (and is not already ours) the cart offers "Register it" at the registration price.

## 4. Tax for visitors

A guest cart has no client yet, so the cart shows "worked out at checkout"; at checkout, choosing the country shows the tax rule for it. The order stores the tax computed from the new account's country (unchanged rule: the client's country decides).

## 5. Files

`apps/orders/guest.py` (session cart), `services.py` (guest-aware checks, `upgrade_options`, `upgrade_item`, `domain_offers`, `recommended_addon_ids`, `adopt_guest_cart`, `purge_guest_carts`), `signals.py` (claim the cart on sign-in), `views.py` (cart and checkout for visitors), `pricing.py` (client-less carts, `country`), `apps/core/countries.py`, templates `orders/customer/{cart,checkout}.html`, `orders/_steps.html`, migrations `orders 0003`, `products 0003` (`Cart.user/client` nullable + `guest_token`; `Product.upsell_product`, `Product.recommended_addons`).

## 6. Verified

* `apps/orders/tests/test_guest_shop.py` (25 tests): adding as a visitor, isolation between visitors, add-on/coupon/domain/transfer as a visitor, empty states, staff still cannot shop, sign-in merge (duplicates, add-ons, staff), checkout creating the account and order with tax, the country refresh creating nothing, existing email, weak or mismatched password, a cart that cannot be ordered creating no account, missing fields, the signed-in path unchanged, upsell (swap, hidden target, no suggestion, duplicate), recommended add-ons, domain cross-sell, plan page, staff setting and the self-upgrade guard, and the clean-up job. Mutation checks: guest isolation, strict check before registering, duplicate guard, active check, tax-pending flag, clean-up scope (each fails a test; two survivors were equivalent code).
* Browser (Chromium, 1440 and 390): visitor picks a plan on its page, sees the recommendation, adds it, sees the cart badge, upgrades, registers the domain, checks out with a new account and lands signed in on the order; the country change shows GST and keeps typed values; no horizontal overflow; no JS errors. On phones the cart lines stack instead of scrolling sideways.

## 7. Not changed / limits

Payments are still manual. No abandoned-cart emails. Visitors cannot yet save a cart to an email address. Product comparison tables and bundles are not built.
