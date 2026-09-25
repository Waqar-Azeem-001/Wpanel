"""Which page shell (layout) a request is drawn in, and the small UI facts every template needs."""

PUBLIC = "layouts/public.html"
CLIENT = "layouts/client.html"
STAFF = "layouts/staff.html"


def layout(request):
    """
    ``layout_template``: staff get the staff shell everywhere, signed-in customers the client shell, everyone else the
    public shell. ``base.html`` extends it, so every existing page moves into the right shell without being edited.
    """
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and user.is_staff:
        chosen, area = STAFF, "staff"
    elif user is not None and user.is_authenticated:
        chosen, area = CLIENT, "client"
    else:
        chosen, area = PUBLIC, "public"
    return {"layout_template": chosen, "ui_area": area}
