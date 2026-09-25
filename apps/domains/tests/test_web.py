import pytest

from apps.accounts.roles import Role
from apps.clients import services as client_services
from apps.domains import services
from apps.domains.models import RegistrarProvider

pytestmark = pytest.mark.django_db


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def registrar():
    return RegistrarProvider.objects.create(name="Test", kind="manual", is_active=True)


@pytest.fixture
def com_pricing(manager, registrar):
    return services.set_tld_pricing(manager, ".com", register_price="10", renew_price="12", transfer_price="9")


@pytest.fixture
def client_obj(manager):
    return client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test"})


@pytest.fixture
def owner(client_obj):
    return client_obj.contacts.get().user


# --- Public search (no login) ---------------------------------------------------------------

def test_search_page_renders_without_login(client, com_pricing):
    response = client.get("/domains/")
    assert response.status_code == 200 and b"<form" in response.content


def test_search_shows_availability_and_price(client, com_pricing):
    response = client.get("/domains/?domain=example.com&years=1")
    assert response.status_code == 200
    assert b"is available" in response.content and b"10.00" in response.content


def test_search_shows_taken_and_suggests_transfer(client, manager, client_obj, com_pricing):
    services.request_registration(manager, client_obj, "example.com", 1)
    response = client.get("/domains/?domain=example.com&years=1")
    assert b"is taken" in response.content


def test_search_unsupported_tld_shows_form_error(client, registrar):
    response = client.get("/domains/?domain=example.zzz&years=1")
    assert response.status_code == 200 and b"not a supported TLD" in response.content


# --- Customer self-service ------------------------------------------------------------------

def test_customer_domain_pages_require_login(client):
    assert client.get("/account/domains/").status_code == 302
    assert client.get("/account/domains/1/").status_code == 302


def test_the_request_without_paying_pages_are_gone(client, owner, com_pricing):
    from apps.domains.models import Domain

    client.force_login(owner)
    assert client.get("/account/domains/register/").status_code == 404
    assert client.post("/account/domains/register/", {"domain": "example.com", "years": 1}).status_code == 404
    assert client.get("/account/domains/transfer/").status_code == 404
    assert not Domain.objects.exists()
    assert b"/account/domains/register/" not in client.get("/domains/?domain=example.com").content


def test_customer_domain_list_and_detail(client, owner, manager, client_obj, com_pricing):
    services.request_registration(manager, client_obj, "example.com", 1)
    client.force_login(owner)
    listing = client.get("/account/domains/")
    assert b"example.com" in listing.content
    domain = client_obj.domains.get()
    detail = client.get(f"/account/domains/{domain.pk}/")
    assert detail.status_code == 200 and b"example.com" in detail.content


def test_customer_cannot_view_other_clients_domain(client, customer, manager, client_obj, com_pricing):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    client.force_login(customer)
    assert client.get(f"/account/domains/{domain.pk}/").status_code == 404


def test_customer_self_service_actions(client, owner, manager, client_obj, com_pricing):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    client.force_login(owner)

    client.post(f"/account/domains/{domain.pk}/auto-renew/", {"auto_renew": "on"})
    domain.refresh_from_db()
    assert domain.auto_renew is True
    client.post(f"/account/domains/{domain.pk}/auto-renew/", {})  # unchecked box sends nothing
    domain.refresh_from_db()
    assert domain.auto_renew is False

    client.post(f"/account/domains/{domain.pk}/nameservers/",
               {"nameservers": "ns1.example.com\nns2.example.com"})
    domain.refresh_from_db()
    assert domain.nameservers == ["ns1.example.com", "ns2.example.com"]

    client.post(f"/account/domains/{domain.pk}/unlock/")
    domain.refresh_from_db()
    assert domain.is_locked is False

    added = client.post(f"/account/domains/{domain.pk}/dns/",
                        {"record_type": "A", "name": "", "content": "1.2.3.4", "ttl": 3600})
    assert added.status_code == 302
    record = domain.dns_records.get()
    client.post(f"/account/domains/{domain.pk}/dns/{record.pk}/remove/")
    assert domain.dns_records.count() == 0


# --- Staff --------------------------------------------------------------------------------

def test_staff_pages_require_permission(client, customer, manager, client_obj, com_pricing):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    assert client.get("/staff/domains/").status_code == 302
    client.force_login(customer)
    assert client.get("/staff/domains/").status_code == 403
    assert client.get(f"/staff/domains/{domain.pk}/").status_code == 403


def test_support_agent_can_view_but_not_complete(client, staff, manager, client_obj, com_pricing):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    client.force_login(staff(Role.SUPPORT_AGENT))
    assert client.get(f"/staff/domains/{domain.pk}/").status_code == 200
    assert client.post(f"/staff/domains/{domain.pk}/complete/").status_code == 403


def test_staff_full_lifecycle_via_web(client, manager, client_obj, com_pricing):
    client.force_login(manager)
    client.post("/staff/domains/tlds/save/", {"tld": ".net", "register_price": "8", "renew_price": "9",
                                              "transfer_price": "7", "redemption_price": "", "min_years": 1,
                                              "max_years": 10})
    from apps.domains.models import TldPricing

    assert TldPricing.objects.filter(tld=".net").exists()

    domain = services.request_registration(manager, client_obj, "example.com", 1)
    client.post(f"/staff/domains/{domain.pk}/complete/")
    domain.refresh_from_db()
    assert domain.status == "active"

    client.post(f"/staff/domains/{domain.pk}/renew/", {"years": 1})
    client.post(f"/staff/domains/{domain.pk}/nameservers/", {"nameservers": "ns1.wpanel.io\nns2.wpanel.io"})
    domain.refresh_from_db()
    assert domain.nameservers == ["ns1.wpanel.io", "ns2.wpanel.io"]

    client.post(f"/staff/domains/{domain.pk}/dns/", {"record_type": "TXT", "name": "", "content": "v=spf1 -all",
                                                      "ttl": 3600})
    assert domain.dns_records.count() == 1

    client.post(f"/staff/domains/{domain.pk}/sync/")

    listing = client.get("/staff/domains/?q=example")
    assert b"example.com" in listing.content


def test_staff_cancel_pending_request(client, manager, client_obj, com_pricing):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    client.force_login(manager)
    client.post(f"/staff/domains/{domain.pk}/cancel/", {"reason": "duplicate request"})
    domain.refresh_from_db()
    assert domain.status == "cancelled"


def test_staff_tld_list_and_status_toggle(client, manager, com_pricing):
    client.force_login(manager)
    response = client.get("/staff/domains/tlds/")
    assert response.status_code == 200 and b".com" in response.content
    client.post(f"/staff/domains/tlds/{com_pricing.pk}/status/", {})  # unchecked -> inactive
    com_pricing.refresh_from_db()
    assert com_pricing.is_active is False
