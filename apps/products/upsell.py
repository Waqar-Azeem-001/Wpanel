"""Paid extras suggested to a customer on a page that already concerns them, with the ticket that orders one filled in."""
from urllib.parse import urlencode

from django.urls import reverse

from .categories import lowest_price
from .models import Addon, AddonKind, CatalogStatus


def whois_privacy_offer(*, hosting_account=None, domain=None):
    """The active WHOIS privacy add-on with its lowest price and the link that opens a filled-in ticket, or None when staff have not set one up."""
    addons = Addon.objects.filter(kind=AddonKind.WHOIS_PRIVACY, status=CatalogStatus.ACTIVE).prefetch_related("prices")
    priced = [(lowest_price(a), a) for a in addons]
    priced = [(p, a) for p, a in priced if p is not None]
    if not priced:
        return None
    price, addon = min(priced, key=lambda pair: pair[0].price)
    name = domain.name if domain is not None else hosting_account.domain
    query = {"subject": f"Order: {addon.name} for {name}", "body": f"Hello, I would like to order {addon.name} for {name}."}
    if domain is not None:
        query["domain"] = domain.pk
    else:
        query["hosting_account"] = hosting_account.pk
    return {"addon": addon, "price": price, "url": f"{reverse('support_customer:new')}?{urlencode(query)}"}
