from . import services


def brand(request):
    """``brand`` (name, colours, footer, formats) and ``SITE_NAME`` for every template."""
    current = services.get()
    return {"brand": current, "SITE_NAME": current.name}
