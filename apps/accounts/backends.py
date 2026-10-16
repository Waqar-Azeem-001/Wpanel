"""Sign-in backend: Django's own permission lookup, with the permissions an administrator hid from one person taken out. A Super Admin is never restricted."""
from django.contrib.auth.backends import ModelBackend


class PortalBackend(ModelBackend):
    def get_all_permissions(self, user_obj, obj=None):
        found = super().get_all_permissions(user_obj, obj)
        if not found or not getattr(user_obj, "is_active", False) or getattr(user_obj, "is_superuser", False) or obj is not None:
            return found
        if not hasattr(user_obj, "_hidden_permission_cache"):
            user_obj._hidden_permission_cache = {f"accounts.{c}" for c in user_obj.hidden_permissions.values_list("codename", flat=True)}
        return found - user_obj._hidden_permission_cache
