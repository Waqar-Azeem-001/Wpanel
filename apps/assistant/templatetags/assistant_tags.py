import re

from django.template import Library
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = Library()
HELP_PATH = re.compile(r"(/help/[a-z0-9-]+/[a-z0-9-]+/)")


@register.filter
def agent_text(value):
    """An answer as safe HTML: line breaks kept, a help article path turned into a link."""
    html = HELP_PATH.sub(r'<a href="\1">\1</a>', escape(value or "")).replace("\n", "<br>")
    return mark_safe(html)
