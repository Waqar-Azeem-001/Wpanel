"""Audit completeness (roadmap section 05): old and new values for settings changes, client and service for Admin Access, quick actions, the client page link."""
import pytest
from django.core.management import call_command

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.hosting import control, snapshots
from apps.hosting.adapters.capabilities import DnsRecord
from apps.hosting.models import HostingAccount
from apps.products import services as product_services
from apps.products.models import ProductType

pytestmark = pytest.mark.django_db


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


def last(operation):
    return AuditEvent.objects.filter(action="hosting.control", metadata__operation=operation).latest("id").metadata


def test_the_difference_keeps_only_the_fields_that_changed():
    assert snapshots.difference({"a": 1, "b": 2}, {"a": 1, "b": 3}) == ({"b": 2}, {"b": 3})
    assert snapshots.difference({"a": 1}, {}) == ({"a": 1}, {})  # removed
    assert snapshots.difference({}, {"a": 1}) == ({}, {"a": 1})  # appeared
    assert snapshots.difference(None, {"a": 1}) == ({}, {}) and snapshots.difference({"a": 1}, {"a": 1}) == ({}, {})


def test_php_settings_record_what_they_were_and_what_they_became(owner, account):
    control.perform(owner, account, "php", "set_php_settings", account.domain, {"version": "8.2", "memory_limit": "512M"})
    meta = last("set_php_settings")
    assert meta["old"]["memory_limit"] == "256M" and meta["new"]["memory_limit"] == "512M" and meta["new"]["version"] == "8.2"


def test_ssl_backup_and_redirect_changes_record_old_and_new(owner, account):
    control.perform(owner, account, "ssl", "set_ssl_auto_renew", account.domain, False)
    assert last("set_ssl_auto_renew")["old"] == {"auto_renew": True} and last("set_ssl_auto_renew")["new"] == {"auto_renew": False}
    control.perform(owner, account, "ssl", "set_https_redirect", account.domain, False)
    assert last("set_https_redirect")["old"] == {"https_redirect": True}
    control.perform(owner, account, "backups", "set_backup_schedule", "daily")
    assert last("set_backup_schedule")["old"] == {"schedule": "weekly"} and last("set_backup_schedule")["new"] == {"schedule": "daily"}
    control.perform(owner, account, "backups", "set_backup_offsite", False)
    assert last("set_backup_offsite")["old"] == {"offsite": True}
    control.perform(owner, account, "domains", "set_redirect", account.domain, "https://www.example.org/", True)
    assert last("set_redirect")["new"] == {"redirect_to": "https://www.example.org/"}


def test_a_dns_edit_and_delete_record_the_record_before_and_after(owner, account):
    records = control.perform(owner, account, "dns", "list_dns_records", account.domain)
    a = next(r for r in records if r.type == "A" and r.name == "@")
    control.perform(owner, account, "dns", "update_dns_record", account.domain, DnsRecord(a.record_id, "A", "@", "203.0.113.99", a.ttl))
    meta = last("update_dns_record")
    assert meta["old"] == {"value": a.value} and meta["new"] == {"value": "203.0.113.99"}
    a = next(r for r in control.perform(owner, account, "dns", "list_dns_records", account.domain) if r.type == "A" and r.value == "203.0.113.99")
    control.perform(owner, account, "dns", "delete_dns_record", account.domain, a.record_id)
    gone = last("delete_dns_record")
    assert gone["old"]["type"] == "A" and gone["new"] == {}


def test_cron_changes_never_record_the_commands_arguments(owner, account):
    job = control.perform(owner, account, "cron", "create_cron_job", "0 2 * * *", "php public_html/a.php --token=secret123")
    control.perform(owner, account, "cron", "update_cron_job", job, schedule="0 3 * * *")
    meta = last("update_cron_job")
    assert meta["old"] == {"schedule": "0 2 * * *"} and meta["new"] == {"schedule": "0 3 * * *"}
    control.perform(owner, account, "cron", "update_cron_job", job, enabled=False)
    assert "secret123" not in str(list(AuditEvent.objects.values_list("metadata", flat=True)))


def test_secrets_and_contents_record_no_values_at_all(owner, account):
    control.perform(owner, account, "ftp", "create_ftp_account", "dev", "Strong-Pass-2026", "")
    control.perform(owner, account, "ftp", "set_ftp_password", "dev@demo-shop.example.com", "Another-Pass-2027")
    control.perform(owner, account, "files", "write_file", "public_html/secret.txt", b"top secret text")
    for operation in ("create_ftp_account", "set_ftp_password", "write_file"):
        assert "old" not in last(operation) and "new" not in last(operation)
    everything = str(list(AuditEvent.objects.values_list("metadata", flat=True)))
    assert "Strong-Pass" not in everything and "Another-Pass" not in everything and "top secret" not in everything


def test_a_provider_that_cannot_report_a_value_just_records_none(owner, account, monkeypatch):
    from apps.hosting.adapters.base import HostingError
    from apps.hosting.adapters.demo import DemoAdapter

    def broken(self, username):
        raise HostingError("no")

    monkeypatch.setattr(DemoAdapter, "backup_options", broken)
    control.perform(owner, account, "backups", "set_backup_schedule", "off")
    assert "old" not in last("set_backup_schedule")


def test_admin_access_actions_name_the_client_and_the_service(client, staff, account):
    technical = staff(Role.TECHNICAL)
    client.force_login(technical)
    client.post(f"/staff/hosting/{account.pk}/admin-access/", {"reason": "Ticket 90: changing the PHP version"})
    client.post(f"/account/hosting/{account.pk}/manage/php/do/", {"action": "settings", "domain": account.domain, "version": "8.2",
                                                                 "memory_limit": "256M", "upload_max_filesize": "64M", "max_execution_time": "60"})
    meta = last("set_php_settings")
    assert meta["admin_access"] is True and meta["client"] == account.client_id and meta["service"] == account.pk
    assert meta["new"].get("version") == "8.2"
    event = AuditEvent.objects.filter(action="hosting.control", metadata__operation="set_php_settings").latest("id")
    assert event.actor == technical and event.target_id == str(account.pk) and event.created_at and event.ip_address


def test_the_clients_services_tab_has_a_manage_hosting_link_only_for_live_accounts(client, manager, account):
    client.force_login(manager)
    page = client.get(f"/staff/clients/{account.client_id}/tab/services/").content.decode()
    assert f"/staff/hosting/{account.pk}/#manage" in page and "Manage Hosting" in page
    pending = HostingAccount.objects.get(domain="demo-new.example.com")
    assert f"/staff/hosting/{pending.pk}/#manage" not in page


def test_the_website_card_offers_the_tools_people_use_most(client, owner, account):
    client.force_login(owner)
    listing = client.get("/account/hosting/").content.decode()
    page = client.get(f"/account/hosting/{account.pk}/").content.decode()  # the tools are on the website's own dashboard; the list just opens it
    for label in ("File manager", "Email accounts", "MySQL databases", "DNS records", "Backups"):
        assert f'class="tool-label">{label}<' in page, label
    pending = HostingAccount.objects.get(domain="demo-new.example.com")
    assert f"/account/hosting/{pending.pk}/manage/files/" not in listing and f"/account/hosting/{account.pk}/" in listing
