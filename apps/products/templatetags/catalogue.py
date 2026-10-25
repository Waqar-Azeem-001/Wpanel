from django import template

from apps.products import categories

register = template.Library()


@register.simple_tag
def catalogue_version():
    """Part of the key of a cached page fragment that shows prices: it changes the moment staff change one."""
    return categories.version()
