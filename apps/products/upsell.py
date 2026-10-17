"""Paid extras suggested to a customer on a page that already concerns them; the button buys it (an invoice is made on the spot)."""
from .categories import lowest_price
from .models import Addon, AddonKind, CatalogStatus


def offers(kind, *, hosting_account=None, domain=None, limit=1):
    """Up to ``limit`` active add-ons of this kind, cheapest first, each as {addon, price, order, target_field, target_pk}; ``order`` is the one already bought or being set up."""
    from apps.renewals.addon_orders import open_order

    found = [(lowest_price(a), a) for a in Addon.objects.filter(kind=kind, status=CatalogStatus.ACTIVE).prefetch_related("prices").order_by("name")]
    found = sorted([(p, a) for p, a in found if p is not None], key=lambda pair: pair[0].price)[:limit]
    order = open_order(kind, hosting_account=hosting_account, domain=domain)
    return [{"addon": a, "price": p, "order": order, "field": "domain" if domain is not None else "hosting",
             "pk": domain.pk if domain is not None else hosting_account.pk} for p, a in found]


def whois_privacy_offer(*, hosting_account=None, domain=None):
    """The WHOIS privacy extra for a registered domain. Given a hosting plan it is offered for the plan's main domain, and only when that domain is registered with us; None when staff have not set the extra up."""
    if domain is None and hosting_account is not None:
        from apps.domains.models import Domain, DomainStatus

        domain = Domain.objects.filter(client=hosting_account.client, name__iexact=hosting_account.domain, status=DomainStatus.ACTIVE).first()
    if domain is None:
        return None
    found = offers(AddonKind.WHOIS_PRIVACY, domain=domain)
    return found[0] if found else None
