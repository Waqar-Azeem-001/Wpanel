import pytest

from apps.accounts.roles import Role
from apps.clients import services as client_services
from apps.hosting import services
from apps.products import services as product_services
from apps.products.models import ProductType

pytestmark = pytest.mark.django_db


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def server(manager):
    return product_services.create_server(manager, {"name": "srv1", "hostname": "srv1.example.com"})


@pytest.fixture
def product(manager, server):
    p = product_services.create_product(manager, {"name": "Starter", "type": ProductType.SHARED_HOSTING,
                                                   "whm_package_name": "starter_pkg"})
    product_services.set_product_servers(manager, p, [server.pk])
    product_services.set_product_status(manager, p, "active")
    return p


@pytest.fixture
def client_obj(manager):
    return client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test"})


@pytest.fixture
def owner(client_obj):
    return client_obj.contacts.get().user


def test_customer_pages_require_login(client):
    assert client.get("/account/hosting/").status_code == 302
    assert client.get("/account/hosting/1/").status_code == 302


def test_the_request_without_paying_page_is_gone(client, owner, product):
    from apps.hosting.models import HostingAccount

    client.force_login(owner)
    assert client.get("/account/hosting/request/").status_code == 404
    assert client.post("/account/hosting/request/", {"product": product.pk, "domain": "example.com"}).status_code == 404
    assert not HostingAccount.objects.exists()
    assert b"/account/hosting/request/" not in client.get("/account/hosting/").content


def test_customer_list_and_detail(client, owner, manager, client_obj, product):
    services.request_hosting(manager, client_obj, product, "example.com")
    client.force_login(owner)
    listing = client.get("/account/hosting/")
    assert b"example.com" in listing.content
    account = client_obj.hosting_accounts.get()
    detail = client.get(f"/account/hosting/{account.pk}/")
    assert detail.status_code == 200 and b"example.com" in detail.content


def test_customer_cannot_view_other_clients_account(client, customer, manager, client_obj, product):
    account = services.request_hosting(manager, client_obj, product, "example.com")
    client.force_login(customer)
    assert client.get(f"/account/hosting/{account.pk}/").status_code == 404


def test_staff_pages_require_permission(client, customer, manager, client_obj, product):
    account = services.request_hosting(manager, client_obj, product, "example.com")
    assert client.get("/staff/hosting/").status_code == 302
    client.force_login(customer)
    assert client.get("/staff/hosting/").status_code == 403
    assert client.get(f"/staff/hosting/{account.pk}/").status_code == 403


def test_support_agent_can_view_but_not_complete(client, staff, manager, client_obj, product):
    account = services.request_hosting(manager, client_obj, product, "example.com")
    client.force_login(staff(Role.SUPPORT_AGENT))
    assert client.get(f"/staff/hosting/{account.pk}/").status_code == 200
    assert client.post(f"/staff/hosting/{account.pk}/complete/").status_code == 403


def test_staff_full_lifecycle_via_web(client, manager, client_obj, product):
    client.force_login(manager)
    account = services.request_hosting(manager, client_obj, product, "example.com")

    client.post(f"/staff/hosting/{account.pk}/complete/")
    account.refresh_from_db()
    assert account.status == "active"

    client.post(f"/staff/hosting/{account.pk}/suspend/", {"reason": "non-payment"})
    account.refresh_from_db()
    assert account.status == "suspended"

    client.post(f"/staff/hosting/{account.pk}/unsuspend/")
    account.refresh_from_db()
    assert account.status == "active"

    client.post(f"/staff/hosting/{account.pk}/sync-status/")
    client.post(f"/staff/hosting/{account.pk}/sync-usage/")

    client.post(f"/staff/hosting/{account.pk}/backup/")
    account.refresh_from_db()
    assert account.backup_requested_at is not None
    assert b"Request backup" in client.get(f"/staff/hosting/{account.pk}/").content

    new_product = product_services.create_product(manager, {"name": "Pro", "type": ProductType.SHARED_HOSTING,
                                                            "whm_package_name": "pro_pkg"})
    client.post(f"/staff/hosting/{account.pk}/change-package/", {"product": new_product.pk})
    account.refresh_from_db()
    assert account.package_name == "pro_pkg"

    client.post(f"/staff/hosting/{account.pk}/terminate/", {})
    account.refresh_from_db()
    assert account.status == "terminated"

    listing = client.get("/staff/hosting/?q=example")
    assert b"example.com" in listing.content


def test_staff_cancel_pending_request(client, manager, client_obj, product):
    account = services.request_hosting(manager, client_obj, product, "example.com")
    client.force_login(manager)
    client.post(f"/staff/hosting/{account.pk}/cancel/", {"reason": "duplicate"})
    account.refresh_from_db()
    assert account.status == "cancelled"


def test_staff_assign_server_when_multiple_mapped(client, manager, client_obj, product):
    other_server = product_services.create_server(manager, {"name": "srv2", "hostname": "srv2.example.com"})
    product_services.set_product_servers(manager, product, [product.servers.first().pk, other_server.pk])
    account = services.request_hosting(manager, client_obj, product, "example.com")
    assert account.server is None

    client.force_login(manager)
    detail = client.get(f"/staff/hosting/{account.pk}/")
    assert b"Assign a server" in detail.content
    client.post(f"/staff/hosting/{account.pk}/assign-server/", {"server": other_server.pk})
    account.refresh_from_db()
    assert account.server == other_server


def test_nav_shows_hosting_links_by_role(client, manager, customer):
    client.force_login(manager)
    assert b"/staff/hosting/" in client.get("/account/profile/").content
    client_services.create_client_for_registration(customer)
    client.force_login(customer)
    content = client.get("/account/profile/").content
    assert b"/staff/hosting/" not in content and b"/account/hosting/" in content


# --- The Websites dashboard (H1) ---------------------------------------------------------------

def test_the_usage_meter_handles_unknown_no_limit_and_thresholds():
    from apps.hosting import dashboard

    assert dashboard.format_mb(None) is None and dashboard.format_mb(120) == "120 MB"
    assert dashboard.format_mb(4915) == "4.8 GB" and dashboard.format_mb(2048) == "2 GB"
    unknown = dashboard.meter(None, 1000)
    assert unknown["known"] is False and unknown["percent"] is None
    unlimited = dashboard.meter(500, None)
    assert unlimited["known"] is True and unlimited["percent"] is None and unlimited["limit"] is None
    assert dashboard.meter(500, 1000)["state"] == "ok"
    assert dashboard.meter(850, 1000)["state"] == "warning"
    assert dashboard.meter(990, 1000)["state"] == "danger" and dashboard.meter(5000, 1000)["percent"] == 100


def test_the_websites_page_shows_a_card_per_account_with_plan_usage_and_actions(client, manager, owner, client_obj, product):
    account = services.complete_provisioning(manager, services.request_hosting(manager, client_obj, product,
                                                                              "shop.example.com"))
    account.disk_used_mb, account.disk_limit_mb = 4915, 20480
    account.bandwidth_used_mb, account.bandwidth_limit_mb = 38, None
    account.save(update_fields=["disk_used_mb", "disk_limit_mb", "bandwidth_used_mb", "bandwidth_limit_mb"])
    client.force_login(owner)
    response = client.get("/account/hosting/")
    page = response.content.decode()
    assert response.status_code == 200 and len(response.context["cards"]) == 1
    assert "shop.example.com" in page and "Starter" in page and "<strong>4.8 GB</strong>" in page and "/ 20 GB" in page
    assert "<progress" in page and 'value="24"' in page
    assert "<strong>38 MB</strong>" in page and "/ unlimited" in page
    assert f'href="/account/hosting/{account.pk}/"' in page and "Upgrade" in page
    # the provider reports neither SSL nor PHP yet: nothing is shown for them (never invented values)
    assert "SSL active" not in page and "SSL not active" not in page and "PHP 8" not in page
    assert ">Manage<" in page and "Hosting information" in page


def test_a_pending_account_shows_a_setup_note_instead_of_usage(client, manager, owner, client_obj, product):
    services.request_hosting(manager, client_obj, product, "soon.example.com")
    client.force_login(owner)
    page = client.get("/account/hosting/").content.decode()
    assert "soon.example.com" in page and "Being set up" in page and "<progress" not in page


def test_a_customer_sees_only_their_own_websites(client, manager, owner, customer, client_obj, product):
    services.complete_provisioning(manager, services.request_hosting(manager, client_obj, product, "mine.example.com"))
    client.force_login(customer)
    assert "mine.example.com" not in client.get("/account/hosting/").content.decode()


def test_ssl_and_php_appear_when_the_provider_reports_them(client, manager, owner, client_obj, product, monkeypatch):
    from apps.hosting import control
    from apps.hosting.adapters import capabilities as caps
    from apps.hosting.adapters.manual import ManualAdapter

    class RichProvider(caps.SslCapability, caps.PhpCapability, ManualAdapter):
        def get_ssl_status(self, username, domain):
            return caps.SslStatus(domain=domain, active=True)

        def list_ssl_status(self, username): return []

        def request_ssl(self, username, domain): ...

        def remove_ssl(self, username, domain): ...

        def set_ssl_auto_renew(self, username, domain, enabled): ...

        def set_https_redirect(self, username, domain, enabled): ...

        def php_options(self, username): return caps.PhpOptions()

        def list_php_extensions(self, username, domain): return []

        def set_php_extension(self, username, domain, name, enabled): ...

        def get_php_settings(self, username, domain):
            return caps.PhpSettings(version="8.3")

        def set_php_settings(self, username, domain, settings): ...

    services.complete_provisioning(manager, services.request_hosting(manager, client_obj, product, "rich.example.com"))
    monkeypatch.setattr(control, "get_adapter", lambda server: RichProvider(host="h", port=1, credentials={}))
    client.force_login(owner)
    page = client.get("/account/hosting/").content.decode()
    assert "SSL active" in page and "PHP 8.3" in page


# --- Test connection from the staff server pages -----------------------------------------------------------------

def test_the_server_pages_offer_test_connection_and_show_the_answer(client, manager, server, product, monkeypatch):
    from apps.hosting.adapters.manual import ManualAdapter

    client.force_login(manager)
    assert "Test connection" in client.get("/staff/servers/").content.decode()
    assert "Test connection" in client.get(f"/staff/servers/{server.pk}/edit/").content.decode()
    monkeypatch.setattr(ManualAdapter, "check_connection", lambda self: {"version": "11.1", "packages": ["gold"]})
    page = client.post(f"/staff/servers/{server.pk}/test/", follow=True).content.decode()
    assert "Connected to srv1." in page and "Version 11.1." in page and "gold" in page
    assert "creates package" in page and "starter_pkg" in page  # the product's package is not on the server


def test_a_failed_test_shows_the_reason_and_never_the_token(client, manager, server, monkeypatch):
    from apps.hosting.adapters.base import HostingError
    from apps.hosting.adapters.manual import ManualAdapter

    def boom(self):
        raise HostingError("The server refused the login. Check the API username and the API token.")

    client.force_login(manager)
    monkeypatch.setattr(ManualAdapter, "check_connection", boom)
    page = client.post(f"/staff/servers/{server.pk}/test/", {"next": "/staff/servers/"}, follow=True).content.decode()
    assert "Could not connect to srv1" in page and "refused the login" in page and "Servers" in page


def test_test_connection_needs_permission_a_post_and_never_redirects_off_site(client, manager, customer, server):
    client.force_login(customer)
    assert client.post(f"/staff/servers/{server.pk}/test/").status_code in (302, 403)
    client.force_login(manager)
    assert client.get(f"/staff/servers/{server.pk}/test/").status_code == 405
    response = client.post(f"/staff/servers/{server.pk}/test/", {"next": "https://evil.example/x"})
    assert response.status_code == 302 and "evil.example" not in response["Location"]
