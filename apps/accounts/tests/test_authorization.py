import pytest
from django.contrib.auth.models import Group

from apps.accounts.models import AccountStatus
from apps.accounts.roles import ROLE_PERMISSIONS, Role, perm, sync_roles
from apps.audit.models import AuditEvent
from conftest import PASSWORD

pytestmark = pytest.mark.django_db


def test_every_role_has_a_group_with_exact_permissions():
    sync_roles()
    for role in Role:
        group = Group.objects.get(name=role.label)
        assert set(group.permissions.values_list("codename", flat=True)) == set(ROLE_PERMISSIONS[role])


def test_sync_roles_is_idempotent_and_repairs_drift():
    sync_roles()
    group = Group.objects.get(name=Role.SUPPORT_AGENT.label)
    group.permissions.clear()
    sync_roles()
    sync_roles()
    assert group.permissions.count() == len(ROLE_PERMISSIONS[Role.SUPPORT_AGENT])


@pytest.mark.parametrize("role,expected", [
    (Role.CUSTOMER, {"view_users": False, "manage_billing": False, "view_audit_log": False}),
    (Role.SUPPORT_AGENT, {"view_support": True, "manage_support": True, "manage_billing": False, "view_users": False}),
    (Role.MANAGER, {"view_users": True, "manage_users": True, "manage_billing": True, "manage_providers": False, "assign_roles": False}),
    (Role.ADMIN, {"manage_providers": True, "manage_settings": True, "assign_roles": True}),
    (Role.SUPER_ADMIN, {"manage_providers": True, "assign_roles": True}),
])
def test_role_permission_matrix(make_user, role, expected):
    user = make_user(f"{role}@example.com", role=role)
    for codename, allowed in expected.items():
        assert user.has_perm(perm(codename)) is allowed, codename


def test_role_drives_staff_and_superuser_flags(make_user):
    assert not make_user("c@example.com").is_staff
    agent = make_user("a@example.com", role=Role.SUPPORT_AGENT)
    assert agent.is_staff and not agent.is_superuser
    boss = make_user("s@example.com", role=Role.SUPER_ADMIN)
    assert boss.is_staff and boss.is_superuser


@pytest.mark.parametrize("path", ["/api/v1/users/", "/api/v1/audit-events/"])
def test_customer_cannot_reach_staff_endpoints(api, customer, path):
    api.force_authenticate(customer)
    response = api.get(path)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


def test_support_agent_cannot_list_users_but_manager_can(api, staff, customer):
    api.force_authenticate(staff(Role.SUPPORT_AGENT))
    assert api.get("/api/v1/users/").status_code == 403
    api.force_authenticate(staff(Role.MANAGER))
    response = api.get("/api/v1/users/")
    assert response.status_code == 200
    body = response.json()
    assert {"count", "next", "previous", "results"} <= set(body)


def test_user_list_filters_searches_and_paginates(api, staff, make_user):
    for i in range(3):
        make_user(f"cust{i}@example.com")
    api.force_authenticate(staff(Role.MANAGER))
    assert api.get("/api/v1/users/?role=customer").json()["count"] == 3
    assert api.get("/api/v1/users/?search=cust1").json()["count"] == 1
    page = api.get("/api/v1/users/?page_size=2").json()
    assert len(page["results"]) == 2 and page["next"]


def test_manager_can_suspend_customer_and_it_is_audited(api, staff, customer):
    manager = staff(Role.MANAGER)
    api.force_authenticate(manager)
    response = api.post(f"/api/v1/users/{customer.pk}/status/", {"status": "suspended", "reason": "fraud check"})
    assert response.status_code == 200
    customer.refresh_from_db()
    assert customer.status == AccountStatus.SUSPENDED and not customer.is_active
    event = AuditEvent.objects.get(action="account.status_changed")
    assert event.actor == manager and event.metadata == {"from": "active", "to": "suspended", "reason": "fraud check"}


def test_suspension_immediately_blocks_existing_jwt(api, staff, customer):
    tokens = api.post("/api/v1/auth/login/", {"email": customer.email, "password": PASSWORD}).json()
    api.force_authenticate(staff(Role.MANAGER))
    api.post(f"/api/v1/users/{customer.pk}/status/", {"status": "suspended"})
    api.force_authenticate(None)
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    assert api.get("/api/v1/me/").status_code == 401
    api.credentials()
    assert api.post("/api/v1/auth/refresh/", {"refresh": tokens["refresh"]}).status_code == 401


def test_manager_cannot_change_admin_status_or_assign_roles(api, staff, customer):
    api.force_authenticate(staff(Role.MANAGER))
    admin = staff(Role.ADMIN)
    assert api.post(f"/api/v1/users/{admin.pk}/status/", {"status": "suspended"}).status_code == 403
    assert api.post(f"/api/v1/users/{customer.pk}/role/", {"role": "support_agent"}).status_code == 403


def test_admin_can_assign_staff_roles_but_not_admin_roles(api, staff, customer):
    api.force_authenticate(staff(Role.ADMIN))
    response = api.post(f"/api/v1/users/{customer.pk}/role/", {"role": "support_agent"})
    assert response.status_code == 200
    customer.refresh_from_db()
    assert customer.role == Role.SUPPORT_AGENT and customer.is_staff
    assert list(customer.groups.values_list("name", flat=True)) == ["Support Agent"]
    assert AuditEvent.objects.filter(action="account.role_changed", metadata__to="support_agent").exists()

    assert api.post(f"/api/v1/users/{customer.pk}/role/", {"role": "admin"}).status_code == 403


def test_super_admin_can_grant_admin(api, staff, customer):
    api.force_authenticate(staff(Role.SUPER_ADMIN))
    assert api.post(f"/api/v1/users/{customer.pk}/role/", {"role": "admin"}).status_code == 200
    customer.refresh_from_db()
    assert customer.has_perm(perm("manage_providers"))


def test_users_cannot_change_their_own_role_or_status(api, staff):
    boss = staff(Role.SUPER_ADMIN)
    api.force_authenticate(boss)
    assert api.post(f"/api/v1/users/{boss.pk}/role/", {"role": "customer"}).json()["error"]["code"] == "self_action"
    assert api.post(f"/api/v1/users/{boss.pk}/status/", {"status": "closed"}).json()["error"]["code"] == "self_action"


def test_audit_log_readable_by_manager_and_filterable(api, staff, customer):
    manager = staff(Role.MANAGER)
    api.force_authenticate(manager)
    api.post(f"/api/v1/users/{customer.pk}/status/", {"status": "suspended"})
    response = api.get("/api/v1/audit-events/?action=account.status")
    assert response.status_code == 200
    assert response.json()["count"] == 1
    # Audit log is read-only through the API.
    event_id = response.json()["results"][0]["id"]
    assert api.delete(f"/api/v1/audit-events/{event_id}/").status_code == 405
