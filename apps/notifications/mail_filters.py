"""
Filters for the wording engine that behave like Django's but survive a preview: in a preview every variable is a stand-in that shows its
name, and Django's ``date`` filter cannot format a stand-in. Loaded as builtins of the wording engines, so they replace the ones with the
same name; a real date is formatted exactly as before.
"""
from django import template
from django.template import defaultfilters

register = template.Library()


def _is_standin(value):
    return type(value).__name__ == "_Placeholder"


@register.filter(name="date", expects_localtime=True, is_safe=False)
def date(value, arg=None):
    return str(value) if _is_standin(value) else defaultfilters.date(value, arg)


@register.filter(name="time", expects_localtime=True, is_safe=False)
def time(value, arg=None):
    return str(value) if _is_standin(value) else defaultfilters.time(value, arg)
