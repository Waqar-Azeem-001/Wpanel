from django import template

from apps.lifecycle import services
from apps.lifecycle.models import Stage

register = template.Library()


@register.inclusion_tag("lifecycle/_service_card.html", takes_context=True)
def lifecycle_card(context, service, staff=False):
    """The cancel/stage card shown on a hosting or domain page."""
    user = context["request"].user
    stage = services.stage_of(service)
    config = services.LifecycleSettings.load()
    return {
        "service": service, "staff": staff, "kind": services.kind_of(service), "is_domain": services.kind_of(service) == "domain",
        "stage": stage, "stage_label": Stage(stage).label if stage else "",
        "timeline": services.timeline(service, config=config),
        "open_request": services.open_request_for(service),
        "can_request": services.can_request(user, service) and services.is_live(service),
    }
