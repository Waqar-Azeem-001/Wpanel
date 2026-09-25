"""
Roles and portal permissions: the single permission system for the project.

* Every permission lives on ``accounts.PortalAccess`` (an unmanaged holder model),
  so checks look like ``user.has_perm("accounts.view_clients")``.
* Every role is a Django ``Group`` whose permissions are synced from ``ROLE_PERMISSIONS``
  by ``sync_roles()`` (runs after every migrate).
* A user has exactly one role (``User.role``); the service layer keeps their group
  membership, ``is_staff`` and ``is_superuser`` consistent with it.

Customers get no portal permissions: they only reach their own records, which is
enforced by ownership checks in each module rather than by these permissions.
"""
from django.db import models


class Role(models.TextChoices):
    CUSTOMER = "customer", "Customer"
    SUPPORT_AGENT = "support_agent", "Support Agent"
    MANAGER = "manager", "Manager"
    ADMIN = "admin", "Admin"
    SUPER_ADMIN = "super_admin", "Super Admin"


STAFF_ROLES = {Role.SUPPORT_AGENT, Role.MANAGER, Role.ADMIN, Role.SUPER_ADMIN}
# Only a Super Admin may grant or revoke these roles.
PRIVILEGED_ROLES = {Role.ADMIN, Role.SUPER_ADMIN}

AREAS = [
    ("users", "staff and customer user accounts"),
    ("clients", "clients"),
    ("products", "products and pricing"),
    ("orders", "orders"),
    ("billing", "invoices and transactions"),
    ("domains", "domains"),
    ("hosting", "hosting services"),
    ("support", "support tickets"),
    ("affiliates", "affiliates, commissions and payouts"),
    ("reports", "reports"),
    ("providers", "provider connections"),
    ("settings", "system settings"),
]

PORTAL_PERMISSIONS = [(f"view_{area}", f"Can view {label}") for area, label in AREAS]
PORTAL_PERMISSIONS += [(f"manage_{area}", f"Can manage {label}") for area, label in AREAS]
PORTAL_PERMISSIONS += [
    ("assign_roles", "Can assign staff roles"),
    ("view_audit_log", "Can view the audit log"),
]

ALL_CODENAMES = [codename for codename, _ in PORTAL_PERMISSIONS]


def _view(*areas):
    return [f"view_{a}" for a in areas]


def _manage(*areas):
    return [f"manage_{a}" for a in areas]


ROLE_PERMISSIONS = {
    Role.CUSTOMER: [],
    Role.SUPPORT_AGENT: [
        *_view("clients", "orders", "billing", "domains", "hosting", "support"),
        *_manage("support"),
    ],
    Role.MANAGER: [
        *_view(*(a for a, _ in AREAS if a not in {"providers", "settings"})),
        *_manage("users", "clients", "products", "orders", "billing", "domains", "hosting", "support", "affiliates"),
        "view_audit_log",
    ],
    Role.ADMIN: ALL_CODENAMES,
    Role.SUPER_ADMIN: ALL_CODENAMES,
}


def perm(codename):
    """Full permission string for ``user.has_perm``."""
    return f"accounts.{codename}"


def sync_roles():
    """Create/update one Group per role with exactly the permissions in ROLE_PERMISSIONS."""
    from django.contrib.auth.models import Group, Permission
    from django.contrib.contenttypes.models import ContentType

    from .models import PortalAccess

    content_type = ContentType.objects.get_for_model(PortalAccess, for_concrete_model=False)
    for codename, name in PORTAL_PERMISSIONS:
        Permission.objects.update_or_create(content_type=content_type, codename=codename, defaults={"name": name})
    by_codename = {p.codename: p for p in Permission.objects.filter(content_type=content_type)}

    for role in Role:
        group, _ = Group.objects.get_or_create(name=role.label)
        group.permissions.set([by_codename[c] for c in ROLE_PERMISSIONS[role]])

