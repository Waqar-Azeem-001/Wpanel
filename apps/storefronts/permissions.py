from rest_framework.permissions import BasePermission

from .models import ExternalStorefront


class IsStorefrontAuthenticated(BasePermission):
    """The request carries a valid, active storefront credential - checked against ``request.auth``, never
    ``request.user`` (a storefront is never a Django user; see ``authentication.py``). Fails closed."""

    def has_permission(self, request, view):
        return isinstance(request.auth, ExternalStorefront)
