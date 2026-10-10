"""Choosing a native node for a new website (N3): enrolled, healthy, able to host websites, with room left; the least full wins."""
from django.db.models import Count, Q

from apps.hosting.models import LIVE_STATUSES
from apps.products.models import ServerKind, ServerStatus

from . import enrollment

PLACEABLE = ("healthy", "warning")  # a node that is struggling a little still takes new sites; a critical or offline one does not
from .models import AgentNode, NodeStatus


def pick_node(region="", among=None):
    """The Server to put the next website on, or None if no node qualifies. ``among`` limits the choice to those servers."""
    nodes = (AgentNode.objects.select_related("server").filter(status=NodeStatus.ENROLLED, server__kind=ServerKind.NATIVE, server__status=ServerStatus.ACTIVE)
             .annotate(sites=Count("server__hosting_accounts", filter=Q(server__hosting_accounts__status__in=LIVE_STATUSES))))
    if among is not None:
        nodes = nodes.filter(server__in=among)
    best, best_load = None, None
    for node in nodes:
        if "web" not in node.capabilities or enrollment.health_state(node) not in PLACEABLE:
            continue
        if region and node.server.region.lower() != region.lower():
            continue
        limit = node.capacity_sites or node.server.max_accounts
        if limit is not None and node.sites >= limit:
            continue
        load = node.sites / limit if limit else node.sites / 1000  # nodes with no stated capacity fill last-ish but still fill evenly
        if best is None or load < best_load:
            best, best_load = node.server, load
    return best




def _mail_nodes():
    return (AgentNode.objects.select_related("server").filter(status=NodeStatus.ENROLLED, server__kind=ServerKind.NATIVE, server__status=ServerStatus.ACTIVE)
            .annotate(boxes=Count("server__mail_accounts")))


def has_mail_nodes():
    """Is any enrolled mail node set up at all? (Without one the Email tool is not offered on native accounts.)"""
    return any("mail" in n.capabilities for n in _mail_nodes())


def pick_mail_node():
    """The Server to hold a new account's mail: enrolled, active, able to do mail, healthy, with the fewest accounts."""
    best = None
    for node in _mail_nodes():
        if "mail" in node.capabilities and enrollment.health_state(node) in PLACEABLE and (best is None or node.boxes < best.boxes):
            best = node
    return best.server if best else None
