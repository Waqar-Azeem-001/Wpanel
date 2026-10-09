"""The provider-neutral hosting API: /api/v1/hosting/services/{id}/<resource>/ - same gateway, permissions and audit as the screens."""
import pytest
from django.core.management import call_command

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.hosting.models import HostingAccount
from apps.products import services as product_services
from apps.products.models import ProductType

pytestmark = pytest.mark.django_db

PASSWORD = "Strong-Pass-2026"
REASON = {"HTTP_X_ADMIN_ACCESS_REASON": "Ticket 55: customer asked for help"}


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def client_obj(manager):
    return client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test"})


@pytest.fixture
def owner(client_obj):
    return client_obj.contacts.get().user


@pytest.fixture
def account(owner, manager):
    product_services.create_product(manager, {"name": "Starter", "type": ProductType.SHARED_HOSTING})
    call_command("demo_hosting_data")
    return HostingAccount.objects.get(domain="demo-shop.example.com")


def url(account, resource):
    return f"/api/v1/hosting/services/{account.pk}/{resource}/"


@pytest.fixture
def me(api, owner):
    api.force_authenticate(owner)
    return api


# --- Access ---------------------------------------------------------------------------------------------------------------

def test_every_resource_needs_a_signed_in_user_and_hides_other_peoples_services(api, account, make_user):
    for resource in ("files", "databases", "emails", "domains", "dns", "ssl", "php", "cron", "backups", "usage"):
        assert api.get(url(account, resource)).status_code == 401
    api.force_authenticate(make_user("stranger@example.com"))
    for resource in ("files", "databases", "emails", "domains", "dns", "ssl", "php", "cron", "backups", "usage"):
        assert api.get(url(account, resource)).status_code == 404
    assert api.post(url(account, "databases"), {"name": "x"}, format="json").status_code == 404


def test_the_customer_can_read_every_resource_and_nothing_names_a_provider(me, account):
    for resource in ("files", "databases", "emails", "domains", "dns", "ssl", "php", "cron", "backups", "usage"):
        response = me.get(url(account, resource))
        assert response.status_code == 200, resource
        assert "cpanel" not in response.content.decode().lower() and "whm" not in response.content.decode().lower()
    assert "public_html" in [e["name"] for e in me.get(url(account, "files"), {"path": ""}).json()]
    assert me.get(url(account, "usage")).json()["usage"]["disk_limit_mb"] == 20480
    assert me.get(url(account, "usage"), {"days": 7}).json()["history"].__len__() == 7


# --- Changing things ------------------------------------------------------------------------------------------------------

def test_files_create_a_folder_and_trash_it(me, account):
    assert me.post(url(account, "files"), {"path": "uploads"}, format="json").status_code == 201
    assert "uploads" in [e["name"] for e in me.get(url(account, "files")).json()]
    assert me.delete(url(account, "files") + "?path=uploads").status_code == 204
    assert "uploads" not in [e["name"] for e in me.get(url(account, "files")).json()]
    assert me.post(url(account, "files"), {}, format="json").status_code == 400
    assert me.post(url(account, "files"), {"path": "../escape"}, format="json").status_code == 400


def test_databases_create_list_and_delete(me, account):
    assert me.post(url(account, "databases"), {"name": "shop2"}, format="json").status_code == 201
    names = [d["name"] for d in me.get(url(account, "databases")).json()["databases"]]
    assert any(n.endswith("shop2") for n in names)
    full = next(n for n in names if n.endswith("shop2"))
    assert me.delete(url(account, "databases") + f"{full}/").status_code == 204


def test_mailboxes_create_change_and_delete_and_no_password_is_ever_returned(me, account):
    response = me.post(url(account, "emails"), {"address": "info@demo-shop.example.com", "password": PASSWORD + "-x", "quota_mb": 250}, format="json")
    assert response.status_code == 201 and "-x" not in response.content.decode()
    listing = me.get(url(account, "emails")).json()
    assert {"address": "info@demo-shop.example.com", "quota_mb": 250}.items() <= next(m for m in listing["mailboxes"] if m["address"].startswith("info@")).items()
    assert PASSWORD not in str(listing)
    assert me.patch(url(account, "emails") + "info@demo-shop.example.com/", {"quota_mb": 500}, format="json").status_code == 200
    assert me.delete(url(account, "emails") + "info@demo-shop.example.com/").status_code == 204


def test_domains_and_dns(me, account):
    assert me.post(url(account, "domains"), {"name": "shop-two.com", "kind": "addon"}, format="json").status_code == 201
    assert "shop-two.com" in [d["name"] for d in me.get(url(account, "domains")).json()["domains"]]
    created = me.post(url(account, "dns"), {"type": "A", "name": "api", "value": "203.0.113.20", "ttl": 3600}, format="json")
    assert created.status_code == 201
    record_id = created.json()["record_id"]
    assert me.put(url(account, "dns") + f"{record_id}/", {"type": "A", "name": "api", "value": "203.0.113.21", "ttl": 3600}, format="json").status_code == 200
    assert any(r["value"] == "203.0.113.21" for r in me.get(url(account, "dns")).json())
    assert me.delete(url(account, "dns") + f"{record_id}/").status_code == 204
    assert me.post(url(account, "dns"), {"type": "A", "name": "bad", "value": "nope"}, format="json").status_code == 400
    assert me.delete(url(account, "domains") + "shop-two.com/").status_code == 204


def test_ssl_php_cron_and_backups(me, account):
    assert me.patch(url(account, "ssl") + "demo-shop.example.com/", {"auto_renew": False}, format="json").status_code == 200
    assert me.get(url(account, "ssl") + "demo-shop.example.com/").json()["auto_renew"] is False
    assert me.patch(url(account, "php"), {"version": "8.2", "memory_limit": "512M"}, format="json").status_code == 200
    assert me.get(url(account, "php")).json()["settings"]["version"] == "8.2"
    job = me.post(url(account, "cron"), {"schedule": "0 2 * * *", "command": "php public_html/job.php"}, format="json").json()["job_id"]
    assert me.patch(url(account, "cron") + f"{job}/", {"enabled": False}, format="json").status_code == 200
    assert me.post(url(account, "cron") + f"{job}/run/").json()["last_status"] == "ok"
    assert me.delete(url(account, "cron") + f"{job}/").status_code == 204
    made = me.post(url(account, "backups"), {"kind": "files"}, format="json").json()["backup_id"]
    assert me.post(url(account, "backups") + f"{made}/verify/").json()["verified"] == "ok"
    assert me.post(url(account, "backups") + f"{made}/restore/").json()["restored"] is True
    assert me.post(url(account, "backups") + f"{made}/nonsense/").status_code == 404
    assert me.delete(url(account, "backups") + f"{made}/").status_code == 204


def test_refusals_come_back_as_clear_errors_not_crashes(me, account):
    response = me.post(url(account, "cron"), {"schedule": "* * * * *", "command": "rm -rf /"}, format="json")
    assert response.status_code == 400
    assert "Traceback" not in response.content.decode()
    assert me.post(url(account, "backups"), {"kind": "everything"}, format="json").status_code == 400


# --- Staff ----------------------------------------------------------------------------------------------------------------

def test_staff_must_give_a_reason_and_their_changes_are_audited_with_it(api, staff, account):
    technical = staff(Role.TECHNICAL)
    api.force_authenticate(technical)
    response = api.get(url(account, "databases"))
    assert response.status_code == 403 and "reason" in response.content.decode().lower()
    assert api.get(url(account, "databases"), **{"HTTP_X_ADMIN_ACCESS_REASON": "no"}).status_code == 403
    assert api.get(url(account, "databases"), **REASON).status_code == 200
    assert api.post(url(account, "databases"), {"name": "staffdb"}, format="json", **REASON).status_code == 201
    event = AuditEvent.objects.filter(action="hosting.control", metadata__operation="create_database").latest("id")
    assert event.actor == technical and event.metadata["admin_access"] is True and event.metadata["reason"].startswith("Ticket 55")
    assert event.metadata["client"] == account.client_id


def test_a_view_only_role_can_look_but_not_change_or_read_private_content(api, staff, account):
    agent = staff(Role.SUPPORT_AGENT)
    api.force_authenticate(agent)
    assert api.get(url(account, "dns"), **REASON).status_code == 200
    assert api.post(url(account, "databases"), {"name": "nope"}, format="json", **REASON).status_code == 403
    assert api.post(url(account, "files"), {"path": "x"}, format="json", **REASON).status_code == 403
