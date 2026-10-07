"""Admin Access (H12): staff manage a customer's hosting through the customer's own engine, with a reason, a banner, roles and audit."""
import pytest
from django.core.management import call_command

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.core.exceptions import ServiceError
from apps.hosting import control
from apps.hosting.models import HostingAccount
from apps.products import services as product_services
from apps.products.models import ProductType

pytestmark = pytest.mark.django_db

REASON = "Ticket 4821: customer cannot log in to WordPress"


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def agent(staff):
    return staff(Role.SUPPORT_AGENT)


@pytest.fixture
def technical(staff):
    return staff(Role.TECHNICAL)


@pytest.fixture
def client_obj(manager):
    return client_services.create_client(manager, {"first_name": "Ada", "last_name": "Lovelace", "email": "ada@acme.test"})


@pytest.fixture
def owner(client_obj):
    return client_obj.contacts.get().user


@pytest.fixture
def account(owner, manager):
    product_services.create_product(manager, {"name": "Starter", "type": ProductType.SHARED_HOSTING})
    call_command("demo_hosting_data")
    return HostingAccount.objects.get(domain="demo-shop.example.com")


def start(client, account, reason=REASON):
    return client.post(f"/staff/hosting/{account.pk}/admin-access/", {"reason": reason})


def mgr(account, tail=""):
    return f"/account/hosting/{account.pk}/{tail}"


def test_the_staff_service_page_offers_manage_hosting_with_a_reason_field(client, technical, account):
    client.force_login(technical)
    page = client.get(f"/staff/hosting/{account.pk}/").content.decode()
    assert "Manage this customer" in page and 'name="reason"' in page and "never needed" in page


def test_staff_cannot_open_a_customers_hosting_screens_without_starting_admin_access(client, technical, account):
    client.force_login(technical)
    for tail in ("", "manage/files/", "manage/email/", "manage/security/"):
        response = client.get(mgr(account, tail))
        assert response.status_code == 302 and response.url == f"/staff/hosting/{account.pk}/"
    page = client.get(mgr(account), follow=True).content.decode()
    assert "start Admin Access" in page
    assert not AuditEvent.objects.filter(action="hosting.admin_access_started").exists()


def test_a_reason_is_required_and_the_start_is_audited(client, technical, account):
    client.force_login(technical)
    for bad in ("", "  ", "no", "x" * 300):
        response = start(client, account, bad)
        assert response.status_code == 302 and response.url == f"/staff/hosting/{account.pk}/"
    assert client.get(mgr(account)).status_code == 302
    response = start(client, account)
    assert response.status_code == 302 and response.url == mgr(account)
    event = AuditEvent.objects.get(action="hosting.admin_access_started")
    assert event.actor == technical and event.target_id == str(account.pk)
    assert event.metadata["reason"] == REASON and event.metadata["minutes"] == 60 and event.metadata["can_change"] is True


def test_the_banner_names_the_customer_the_service_and_the_way_back_on_every_screen(client, technical, account):
    client.force_login(technical)
    start(client, account)
    for tail in ("", "manage/files/", "manage/dns/", "manage/security/", "manage/usage/", "manage/email/webmail/?mailbox=x"):
        response = client.get(mgr(account, tail))
        if response.status_code != 200:
            continue
        page = response.content.decode()
        assert "Admin Access" in page and "Ada" in page and "demo-shop.example.com" in page and "demoshop" in page, tail
        assert f"/staff/hosting/{account.pk}/" in page and "/staff/clients/" in page and "End Admin Access" in page
        assert "No customer password is used" in page
    assert "Admin Access" not in client.get(f"/staff/hosting/{account.pk}/").content.decode().split("Manage this customer")[0]


def test_the_customer_sees_no_banner_on_their_own_screens(client, owner, account):
    client.force_login(owner)
    page = client.get(mgr(account)).content.decode()
    assert "aa-banner" not in page and "Admin Access" not in page


def test_staff_use_the_same_engine_and_changes_are_audited_as_admin_access(client, technical, owner, account):
    client.force_login(technical)
    start(client, account)
    client.post(mgr(account, "manage/ftp/do/"), {"action": "add", "name": "support1", "password": "Strong-Pass-2026", "directory": "public_html"})
    assert any(a.login == "support1@demo-shop.example.com" for a in control.perform(owner, account, "ftp", "list_ftp_accounts"))
    event = AuditEvent.objects.get(action="hosting.control", metadata__operation="create_ftp_account")
    assert event.actor == technical and event.metadata["admin_access"] is True and "Strong-Pass" not in str(event.metadata)
    client.force_login(owner)
    client.post(mgr(account, "manage/ftp/do/"), {"action": "add", "name": "mine", "password": "Strong-Pass-2026", "directory": ""})
    mine = AuditEvent.objects.filter(action="hosting.control", actor=owner).last()
    assert "admin_access" not in mine.metadata


def test_the_customers_own_activity_list_marks_what_support_did(client, technical, owner, account):
    client.force_login(technical)
    start(client, account)
    client.post(mgr(account, "manage/security/do/"), {"action": "scan"})
    client.force_login(owner)
    page = client.get(mgr(account, "manage/security/")).content.decode()
    assert "Malware scan run" in page and "Support" in page


def test_a_view_only_role_can_look_but_not_change_and_not_read_private_content(client, agent, owner, account):
    client.force_login(agent)
    start(client, account)
    page = client.get(mgr(account, "manage/files/")).content.decode()
    assert "Your role can look but not change" in page
    assert AuditEvent.objects.get(action="hosting.admin_access_started").metadata["can_change"] is False
    before = len(control.perform(owner, account, "ftp", "list_ftp_accounts"))
    changed = client.post(mgr(account, "manage/ftp/do/"), {"action": "add", "name": "sneaky", "password": "Strong-Pass-2026", "directory": ""},
                          follow=True).content.decode()
    assert "permission" in changed.lower() and len(control.perform(owner, account, "ftp", "list_ftp_accounts")) == before
    with pytest.raises(ServiceError) as denied:
        control.perform(agent, account, "email", "list_messages", "info@demo-shop.example.com", "INBOX")
    assert denied.value.code == "permission_denied"
    with pytest.raises(ServiceError):
        control.perform(agent, account, "databases", "browse_table", "demoshop_main", "users", 0)
    assert control.perform(agent, account, "dns", "list_dns_records", account.domain)  # ordinary reads are fine


def test_reading_private_content_needs_a_managing_role_and_is_audited(client, technical, owner, account):
    address = "info@demo-shop.example.com"
    control.perform(owner, account, "email", "create_mailbox", address, "Strong-Pass-2026-x", quota_mb=100)
    control.perform(technical, account, "email", "message_folders", address)
    event = AuditEvent.objects.get(action="hosting.admin_read")
    assert event.actor == technical and event.metadata["operation"] == "message_folders" and event.metadata["admin_access"] is True
    control.perform(owner, account, "email", "message_folders", address)
    assert AuditEvent.objects.filter(action="hosting.admin_read").count() == 1  # the customer reading their own mail is not an admin read


def test_ending_admin_access_closes_the_door_and_is_audited(client, technical, account):
    client.force_login(technical)
    start(client, account)
    assert client.get(mgr(account)).status_code == 200
    response = client.post(f"/staff/hosting/{account.pk}/admin-access/end/")
    assert response.status_code == 302 and response.url == f"/staff/hosting/{account.pk}/"
    assert AuditEvent.objects.filter(action="hosting.admin_access_ended", actor=technical).count() == 1
    assert client.get(mgr(account)).status_code == 302


def test_admin_access_expires_after_an_hour_and_belongs_to_one_account_only(client, technical, account, monkeypatch):
    from datetime import timedelta

    from django.utils import timezone

    client.force_login(technical)
    start(client, account)
    other = HostingAccount.objects.get(domain="demo-blog.example.com")
    assert client.get(mgr(other)).status_code == 302  # the grant is for the shop only
    session = client.session
    session["hosting_admin_access"] = {str(account.pk): (timezone.now() - timedelta(minutes=1)).isoformat()}
    session.save()
    assert client.get(mgr(account)).status_code == 302


def test_admin_access_needs_a_live_account_and_the_view_permission(client, customer, owner, technical, account):
    client.force_login(customer)
    assert start(client, account).status_code in (302, 403, 404)
    assert not AuditEvent.objects.filter(action="hosting.admin_access_started").exists()
    assert client.get(mgr(account)).status_code == 404
    client.force_login(technical)
    pending = HostingAccount.objects.get(domain="demo-new.example.com")
    response = start(client, pending)
    assert response.status_code == 302 and response.url == f"/staff/hosting/{pending.pk}/"
    assert not AuditEvent.objects.filter(action="hosting.admin_access_started").exists()


def test_a_staff_member_who_is_also_the_customers_contact_is_treated_as_the_customer(client, owner, account):
    from apps.accounts.models import User

    User.objects.filter(pk=owner.pk).update(is_staff=True)
    client.force_login(owner)
    assert client.get(mgr(account)).status_code == 200
    assert "aa-banner" not in client.get(mgr(account)).content.decode()
