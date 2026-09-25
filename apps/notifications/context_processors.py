from . import services


def unread_notifications(request):
    """``unread_notification_count`` for the navigation bell."""
    user = getattr(request, "user", None)
    return {"unread_notification_count": services.unread_count(user) if user is not None else 0}
