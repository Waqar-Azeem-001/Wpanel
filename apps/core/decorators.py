from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


def portal_permission_required(*perms):
    """
    Web-view counterpart of ``HasPortalPermission``: anonymous users are sent to
    login, signed-in users without every listed permission get 403.
    """

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not request.user.has_perms(perms):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
