from rest_framework.permissions import BasePermission


class HasPortalPermission(BasePermission):
    """
    Grants access when the user holds the portal permission(s) declared on the view.

    Usage on a view::

        permission_classes = [HasPortalPermission]
        required_permissions = {"GET": ["portal.view_clients"], "*": ["portal.manage_clients"]}

    ``required_permissions`` may also be a plain list applying to every method.
    Views that declare nothing are denied (fail closed).
    """

    def _required(self, request, view):
        required = getattr(view, "required_permissions", None)
        if isinstance(required, dict):
            return required.get(request.method, required.get("*"))
        return required

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated and user.is_active):
            return False
        required = self._required(request, view)
        if not required:
            return False
        return user.has_perms(required)
