from rest_framework.permissions import SAFE_METHODS, BasePermission


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


class PublicReadPermission(HasPortalPermission):
    """
    Safe methods (GET/HEAD/OPTIONS) are open to everyone, including anonymous
    users; unsafe methods require the permission(s) in ``required_permissions``,
    same shape as ``HasPortalPermission``.

    Pair this with a queryset that hides non-public rows (draft/retired, etc.)
    from users who lack the read permission, since this class does not filter
    rows itself - it only decides who may call the view at all.
    """

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return super().has_permission(request, view)
