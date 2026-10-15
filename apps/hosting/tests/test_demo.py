"""Sample hosting data for design and testing: the demo provider and the demo_hosting_data command."""
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.roles import Role
from apps.clients import services as client_services
from apps.hosting import control
from apps.hosting.models import HostingAccount
from apps.products import services as product_services
from apps.products.models import ProductType, Server

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
def plan(manager):
    return product_services.create_product(manager, {"name": "Starter", "type": ProductType.SHARED_HOSTING})


def test_the_command_needs_a_client_and_a_plan(db):
    with pytest.raises(CommandError, match="client"):
        call_command("demo_hosting_data")


def test_the_command_needs_a_plan(client_obj):
    with pytest.raises(CommandError, match="product"):
        call_command("demo_hosting_data")


def test_the_command_adds_sample_accounts_once_and_removes_them(owner, plan, client_obj):
    call_command("demo_hosting_data")
    call_command("demo_hosting_data")  # running it twice adds nothing more
    assert HostingAccount.objects.filter(client=client_obj).count() == 5
    assert Server.objects.filter(kind="demo").count() == 3  # the hosting server and two extra sample machines for the infrastructure dashboard
    call_command("demo_hosting_data", "--remove")
    assert not HostingAccount.objects.exists() and not Server.objects.filter(kind="demo").exists()


def test_removing_never_touches_real_accounts(owner, plan, client_obj, manager):
    from apps.hosting import services

    plan.whm_package_name = "starter_pkg"
    plan.save(update_fields=["whm_package_name"])
    real_server = product_services.create_server(manager, {"name": "real", "hostname": "real.example.com"})
    product_services.set_product_servers(manager, plan, [real_server.pk])
    real = services.complete_provisioning(manager, services.request_hosting(manager, client_obj, plan, "real.example.com"))
    call_command("demo_hosting_data")
    call_command("demo_hosting_data", "--remove")
    assert list(HostingAccount.objects.all()) == [real]


def test_the_demo_provider_reports_ssl_php_and_usage(owner, plan):
    call_command("demo_hosting_data")
    account = HostingAccount.objects.get(domain="demo-shop.example.com")
    assert control.capabilities_for(account) == ["account", "files", "databases", "email", "domains", "dns", "ssl", "php", "cron", "logs", "ftp", "access", "security", "insights", "cpanel_access", "ssh", "backups", "usage", "apps"]
    assert control.perform(owner, account, "php", "get_php_settings", account.domain).version
    assert control.perform(owner, account, "ssl", "get_ssl_status", account.domain).domain == account.domain
    usage = control.perform(owner, account, "usage", "get_usage")
    assert usage["disk_limit_mb"] == 20480 and usage["bandwidth_limit_mb"] == 102400


def test_the_websites_page_shows_every_state_with_the_sample_data(client, owner, plan):
    call_command("demo_hosting_data")
    client.force_login(owner)
    page = client.get("/account/hosting/").content.decode()
    for domain in ("demo-shop.example.com", "demo-blog.example.com", "demo-agency.example.com",
                   "demo-club.example.com", "demo-new.example.com"):
        assert domain in page
    # one storage meter for each live website that has a limit to draw against
    assert page.count("<progress") == 4
    assert "Being set up" in page and "is-warning" in page and "is-danger" in page
    assert "Visit" in page and 'class="site-card-link"' in page


# --- The website workspace (grouped Manage menu, section pages) ----------------------------------------------

def test_the_manage_menu_links_only_the_sections_the_provider_supports(client, owner, plan):
    call_command("demo_hosting_data")
    account = HostingAccount.objects.get(domain="demo-shop.example.com")
    client.force_login(owner)
    page = client.get(f"/account/hosting/{account.pk}/").content.decode()
    for group in ("Dashboard", "Hosting plan", "Performance", "Security", "Domains", "Files", "Databases", "Advanced"):
        assert f'aria-hidden="true"></i>{group}' in page, group  # the ten-group website menu
    # the demo provider supports files, databases, email, domains, dns, ssl, php and usage; everything else is shown muted and has no address
    for label in ("File manager", "MySQL databases", "Email accounts", "Domains", "DNS records", "SSL certificate", "PHP settings", "Usage"):
        assert f'href="/account/hosting/{account.pk}/manage/' in page and label in page
    assert page.count('sidebar-soon') == 0  # every section of the menu now has a real screen
    assert f"/account/hosting/{account.pk}/manage/backups/" in page


def test_a_supported_section_page_says_what_it_will_do_and_usage_shows_the_meters(client, owner, plan):
    call_command("demo_hosting_data")
    account = HostingAccount.objects.get(domain="demo-shop.example.com")
    client.force_login(owner)
    ssl = client.get(f"/account/hosting/{account.pk}/manage/ssl/")
    assert ssl.status_code == 200 and "Always use HTTPS" in ssl.content.decode()
    usage = client.get(f"/account/hosting/{account.pk}/manage/usage/").content.decode()
    assert "<progress" in usage and "Storage" in usage and "Bandwidth" in usage
    Server.objects.filter(kind="demo").update(kind="manual")  # a provider that supports nothing shows the not-available page
    backups = client.get(f"/account/hosting/{account.pk}/manage/backups/").content.decode()
    assert "not available for your hosting server yet" in backups


def test_section_pages_belong_to_the_owner_only_and_unknown_sections_are_not_found(client, owner, customer, plan):
    call_command("demo_hosting_data")
    account = HostingAccount.objects.get(domain="demo-shop.example.com")
    client.force_login(customer)
    assert client.get(f"/account/hosting/{account.pk}/manage/ssl/").status_code == 404
    client.force_login(owner)
    assert client.get(f"/account/hosting/{account.pk}/manage/shell/").status_code == 404
