"""Which page shell a request is drawn in, and the navigation and breadcrumbs that shell shows."""
from . import navigation

PUBLIC = "layouts/public.html"
CLIENT = "layouts/client.html"
STAFF = "layouts/staff.html"


def area_for(user):
    """"staff", "client" or "public": staff get the staff shell everywhere, signed-in customers the client shell."""
    if user is not None and user.is_authenticated:
        return "staff" if user.is_staff else "client"
    return "public"


def layout(request):
    """
    ``layout_template`` and ``ui_area``. ``base.html`` extends the template, so every existing page is drawn in the
    right shell without being edited.
    """
    area = area_for(getattr(request, "user", None))
    return {"layout_template": {"staff": STAFF, "client": CLIENT, "public": PUBLIC}[area], "ui_area": area}


def menus(request):
    """``nav`` (the menus for this person, generated from the registry) and automatic ``breadcrumbs``."""
    area = area_for(getattr(request, "user", None))
    menus_for_person = navigation.build(request, area)
    context = {"nav": menus_for_person}
    if area == "client":  # the phone's bottom bar: worth showing only when there are enough places to go
        tabs = [e for e in menus_for_person["main"] if e.mobile_tab]
        if len(tabs) >= 3:
            context["mobile_tabs"] = tabs
    crumbs = navigation.breadcrumbs_for(request)
    if crumbs:
        context["breadcrumbs"] = crumbs  # a page that supplies its own breadcrumbs overrides these
    return context
