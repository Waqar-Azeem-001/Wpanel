"""The tab bar of the staff client profile."""
from django import template

from apps.clients import tabs as client_tabs

register = template.Library()


@register.inclusion_tag("client_tabs.html", takes_context=True)
def client_tab_bar(context, client, active):
    """One link per tab this person may open, with the number of records on the tabs that list them."""
    user = context["request"].user
    counts = context.get("tab_counts")
    if counts is None:
        counts = client_tabs.counts(user, client)
    return {"items": [(tab.label, tab.url(client), tab.key == active, counts.get(tab.key)) for tab in client_tabs.visible_tabs(user)]}
