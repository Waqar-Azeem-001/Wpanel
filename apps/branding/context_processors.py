from django.conf import settings

from . import services


def brand(request):
    """``brand`` (name, colours, footer, formats), ``SITE_NAME`` and ``STORE_CURRENCY`` for every template."""
    current = services.get()
    return {"brand": current, "SITE_NAME": current.name, "STORE_CURRENCY": settings.STORE_CURRENCY}
