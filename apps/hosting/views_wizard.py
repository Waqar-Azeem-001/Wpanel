"""
"Add website": the guided way to a new hosting plan (Hostinger-style): 1 Plan -> 2 Domain -> 3 Confirm.

It is only a front door. Nothing is created here and no price is computed here: the last step posts to the cart exactly as the plan page does
(``orders_customer:cart_add_hosting``), so pricing, tax, coupons, checkout and fulfilment stay where they already are. The state travels in the
address (product, cycle, domain) so every step can be linked, reloaded and used with the back button.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.orders.pricing import cycle_label
from apps.products.models import CatalogStatus, Product, ProductType

from .adapters.base import HostingError
from .adapters.dns_rules import clean_domain

HOSTING_TYPES = (ProductType.SHARED_HOSTING, ProductType.WORDPRESS_HOSTING, ProductType.BUSINESS_HOSTING, ProductType.ECOMMERCE_HOSTING)
STEPS = (("plan", "Plan"), ("domain", "Domain"), ("confirm", "Confirm"))


def _plans():
    plans = Product.objects.filter(status=CatalogStatus.ACTIVE, type__in=HOSTING_TYPES).prefetch_related("prices")
    rows = []
    for product in plans:
        prices = [p for p in product.prices.all() if p.is_active]
        if not prices:
            continue  # a plan with no price cannot be ordered
        cheapest = min(prices, key=lambda p: p.price)
        rows.append({"product": product, "from": cheapest.price, "cycle": cycle_label(cheapest.billing_cycle, cheapest.custom_months)})
    rows.sort(key=lambda r: (r["from"], r["product"].name))
    return rows


def _options(product):
    return [{"value": p.option_value, "label": cycle_label(p.billing_cycle, p.custom_months), "price": p.price, "setup_fee": p.setup_fee}
            for p in product.prices.all() if p.is_active]


@login_required
def new_website(request):
    plans = _plans()
    product = next((r["product"] for r in plans if str(r["product"].pk) == request.GET.get("product", "")), None)
    context = {"plans": plans, "steps": STEPS, "product": product, "step": "plan", "done": []}
    if product is None:
        return render(request, "hosting/customer/new_site.html", context)

    options = _options(product)
    cycle = request.GET.get("cycle", "")
    if cycle not in [o["value"] for o in options]:
        annual = next((o["value"] for o in options if o["value"] == "annual"), None)
        cycle = annual or (options[0]["value"] if options else "")
    raw_domain = (request.GET.get("domain") or "").strip()
    context.update({"options": options, "cycle": cycle, "domain": raw_domain, "step": "domain", "done": ["plan"]})
    if not raw_domain:
        return render(request, "hosting/customer/new_site.html", context)
    try:
        domain = clean_domain(raw_domain)
    except HostingError as exc:
        context["error"] = str(exc)
        return render(request, "hosting/customer/new_site.html", context)
    chosen = next(o for o in options if o["value"] == cycle)
    context.update({"domain": domain, "step": "confirm", "done": ["plan", "domain"], "chosen": chosen})
    return render(request, "hosting/customer/new_site.html", context)
