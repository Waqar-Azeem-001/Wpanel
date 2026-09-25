"""Tab bars for the customer's service and domain pages: every tab is its own page, built from a URL name."""
from django import template
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.core.portal import can_cancel

register = template.Library()


@register.inclusion_tag("components/tabs.html", takes_context=True)
def service_tabs(context, account, active):
    """Overview, Information, Addons, Upgrade / Downgrade, Cancel (the last two open the pages that already exist)."""
    pk = account.pk
    tabs = [
        ("overview", _("Overview"), reverse("hosting_customer:detail", args=[pk])),
        ("information", _("Information"), reverse("hosting_customer:information", args=[pk])),
        ("addons", _("Addons"), reverse("hosting_customer:addons", args=[pk])),
    ]
    if account.status == "active":
        tabs.append(("upgrade", _("Upgrade / Downgrade"), reverse("renewals_customer:hosting_upgrade", args=[pk])))
    if can_cancel(context["request"].user, account):
        tabs.append(("cancel", _("Request cancellation"), reverse("lifecycle_customer:new", args=["hosting", pk])))
    return {"tabs": [(label, url, key == active) for key, label, url in tabs]}


@register.inclusion_tag("components/tabs.html", takes_context=True)
def domain_tabs(context, domain, active):
    """Overview, Auto renew, Nameservers, DNS, Registrar lock, Renew, Cancel."""
    pk = domain.pk
    tabs = [
        ("overview", _("Overview"), reverse("domains_customer:detail", args=[pk])),
        ("auto_renew", _("Auto renew"), reverse("domains_customer:auto_renew", args=[pk])),
        ("nameservers", _("Nameservers"), reverse("domains_customer:nameservers", args=[pk])),
        # The DNS tab is the "dns_add" route: GET shows the tab, POST adds a record (URL names never change).
        ("dns", _("DNS"), reverse("domains_customer:dns_add", args=[pk])),
        ("lock", _("Registrar lock"), reverse("domains_customer:lock_tab", args=[pk])),
        ("renew", _("Renew"), reverse("domains_customer:renew_tab", args=[pk])),
    ]
    if can_cancel(context["request"].user, domain):
        tabs.append(("cancel", _("Request cancellation"), reverse("lifecycle_customer:new", args=["domain", pk])))
    return {"tabs": [(label, url, key == active) for key, label, url in tabs]}
