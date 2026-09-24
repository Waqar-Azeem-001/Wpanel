import pytest

from apps.accounts.roles import Role
from apps.clients import services as client_services
from apps.clients.models import ContactRole
from apps.domains import services
from apps.domains.models import DomainStatus, RegistrarProvider

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


# --- Availability & TLD pricing (public) ----------------------------------------------------

def test_availability_endpoint_is_public(api, com_pricing):
    response = api.get("/api/v1/domains/availability/?domain=example.com")
    assert response.status_code == 200
    body = response.json()
    assert body == {"domain": "example.com", "available": True, "tld": ".com", "register_price": "10.00",
                    "renew_price": "12.00", "transfer_price": "9.00", "years": 1}


def test_availability_rejects_unsupported_tld(api, registrar):
    response = api.get("/api/v1/domains/availability/?domain=example.zzz")
    assert response.status_code == 400 and response.json()["error"]["code"] == "tld_not_supported"


def test_tld_pricing_public_list_hides_inactive_and_internal_fields(api, manager, com_pricing):
    hidden = services.set_tld_pricing(manager, ".xyz", register_price="1", renew_price="1", transfer_price="1")
    services.set_tld_pricing_active(manager, hidden, False)
    response = api.get("/api/v1/tld-pricing/")
    body = response.json()
    assert body["count"] == 1 and body["results"][0]["tld"] == ".com"
    assert "is_active" not in body["results"][0]

    api.force_authenticate(manager)
    assert api.get("/api/v1/tld-pricing/").json()["count"] == 2


def test_tld_pricing_write_requires_manage_domains(api, customer, manager):
    assert api.post("/api/v1/tld-pricing/", {"tld": ".io", "register_price": "1", "renew_price": "1",
                                             "transfer_price": "1"}).status_code == 401
    api.force_authenticate(customer)
    assert api.post("/api/v1/tld-pricing/", {"tld": ".io", "register_price": "1", "renew_price": "1",
                                             "transfer_price": "1"}).status_code == 403
    api.force_authenticate(manager)
    response = api.post("/api/v1/tld-pricing/", {"tld": ".io", "register_price": "1", "renew_price": "1",
                                                 "transfer_price": "1"})
    assert response.status_code == 201


def test_tld_pricing_status_toggle(api, manager, com_pricing):
    api.force_authenticate(manager)
    response = api.post(f"/api/v1/tld-pricing/{com_pricing.tld}/status/", {"is_active": False})
    assert response.status_code == 200 and response.json()["is_active"] is False


# --- Domain list/retrieve visibility ----------------------------------------------------------

def test_domain_endpoints_require_authentication(api):
    assert api.get("/api/v1/domains/").status_code == 401


def test_customer_sees_only_own_domains(api, manager, client_obj, owner, com_pricing):
    mine = services.request_registration(manager, client_obj, "mine.com", 1)
    other_client = client_services.create_client(manager, {"first_name": "B", "email": "b@x.test"})
    services.request_registration(manager, other_client, "other.com", 1)

    api.force_authenticate(owner)
    body = api.get("/api/v1/domains/").json()
    assert [d["id"] for d in body["results"]] == [mine.pk]
    assert api.get(f"/api/v1/domains/{mine.pk}/").status_code == 200


def test_staff_sees_all_domains(api, manager, client_obj, com_pricing):
    services.request_registration(manager, client_obj, "example.com", 1)
    api.force_authenticate(manager)
    assert api.get("/api/v1/domains/").json()["count"] == 1


def test_customer_cannot_see_other_clients_domain(api, manager, client_obj, com_pricing, make_user):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    stranger = make_user("stranger@example.com")
    api.force_authenticate(stranger)
    assert api.get(f"/api/v1/domains/{domain.pk}/").status_code == 404


# --- Registration / transfer ------------------------------------------------------------------

def test_register_via_api_by_owner_and_by_staff(api, manager, client_obj, owner, com_pricing):
    api.force_authenticate(owner)
    response = api.post("/api/v1/domains/register/", {"client_id": client_obj.pk, "domain": "example.com", "years": 1})
    assert response.status_code == 201 and response.json()["status"] == "pending_registration"

    other = client_services.create_client(manager, {"first_name": "B", "email": "b@x.test"})
    api.force_authenticate(manager)
    response = api.post("/api/v1/domains/register/", {"client_id": other.pk, "domain": "second.com", "years": 1})
    assert response.status_code == 201


def test_register_denied_for_non_contact(api, customer, client_obj, com_pricing):
    api.force_authenticate(customer)
    response = api.post("/api/v1/domains/register/", {"client_id": client_obj.pk, "domain": "example.com", "years": 1})
    assert response.status_code == 403


def test_transfer_via_api_requires_auth_code(api, owner, client_obj, com_pricing):
    api.force_authenticate(owner)
    response = api.post("/api/v1/domains/transfer/", {"client_id": client_obj.pk, "domain": "example.com",
                                                       "auth_code": "", "years": 1})
    assert response.status_code == 400


def test_complete_requires_manage_domains(api, owner, manager, client_obj, com_pricing):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    api.force_authenticate(owner)
    assert api.post(f"/api/v1/domains/{domain.pk}/complete/").status_code == 403
    api.force_authenticate(manager)
    response = api.post(f"/api/v1/domains/{domain.pk}/complete/")
    assert response.status_code == 200 and response.json()["status"] == "active"


def test_cancel_and_renew_via_api(api, manager, client_obj, com_pricing):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    api.force_authenticate(manager)
    assert api.post(f"/api/v1/domains/{domain.pk}/cancel/", {"reason": "test"}).json()["status"] == "cancelled"

    active_domain = services.complete_registration(
        manager, services.request_registration(manager, client_obj, "active.com", 1))
    response = api.post(f"/api/v1/domains/{active_domain.pk}/renew/", {"years": 1})
    assert response.status_code == 200 and response.json()["expires_at"] > active_domain.expires_at.isoformat()


# --- Self-service via API ----------------------------------------------------------------------

def test_auto_renew_nameservers_lock_via_api(api, owner, manager, client_obj, com_pricing):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    api.force_authenticate(owner)

    assert api.post(f"/api/v1/domains/{domain.pk}/auto-renew/", {"auto_renew": False}).json()["auto_renew"] is False
    response = api.post(f"/api/v1/domains/{domain.pk}/nameservers/",
                        {"nameservers": ["ns1.example.com", "ns2.example.com"]})
    assert response.status_code == 200 and len(response.json()["nameservers"]) == 2
    assert api.post(f"/api/v1/domains/{domain.pk}/unlock/").json()["is_locked"] is False
    assert api.post(f"/api/v1/domains/{domain.pk}/lock/").json()["is_locked"] is True


def test_self_service_action_404s_for_a_stranger_to_the_domain(api, customer, manager, client_obj, com_pricing):
    # A customer with no relationship to this domain's client never sees it exist at all
    # (get_queryset() already filters it out), matching the client-visibility precedent.
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    api.force_authenticate(customer)
    assert api.post(f"/api/v1/domains/{domain.pk}/auto-renew/", {"auto_renew": False}).status_code == 404


def test_self_service_denied_for_staff_without_manage_domains(api, staff, manager, client_obj, com_pricing):
    # Support Agent has view_domains (sees the domain via get_queryset) but not manage_domains,
    # and staff can never be a client contact - so the service layer must deny the action itself.
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    api.force_authenticate(staff(Role.SUPPORT_AGENT))
    assert api.get(f"/api/v1/domains/{domain.pk}/").status_code == 200
    assert api.post(f"/api/v1/domains/{domain.pk}/auto-renew/", {"auto_renew": False}).status_code == 403


def test_technical_contact_can_self_service(api, manager, client_obj, com_pricing, make_user):
    tech = make_user("tech@example.com")
    client_services.add_contact(manager, client_obj, email="tech@example.com", role=ContactRole.TECHNICAL)
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    api.force_authenticate(tech)
    assert api.post(f"/api/v1/domains/{domain.pk}/auto-renew/", {"auto_renew": False}).status_code == 200


# --- DNS via API ---------------------------------------------------------------------------------

def test_dns_crud_via_api(api, owner, manager, client_obj, com_pricing):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    api.force_authenticate(owner)
    added = api.post(f"/api/v1/domains/{domain.pk}/dns/", {"record_type": "A", "content": "1.2.3.4"})
    assert added.status_code == 201
    record_id = added.json()["id"]
    assert api.get(f"/api/v1/domains/{domain.pk}/dns/").json()[0]["content"] == "1.2.3.4"

    updated = api.patch(f"/api/v1/domains/{domain.pk}/dns/{record_id}/", {"content": "5.6.7.8"})
    assert updated.status_code == 200 and updated.json()["content"] == "5.6.7.8"
    assert api.delete(f"/api/v1/domains/{domain.pk}/dns/{record_id}/").status_code == 204


def test_dns_mx_requires_priority_via_api(api, owner, manager, client_obj, com_pricing):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    api.force_authenticate(owner)
    response = api.post(f"/api/v1/domains/{domain.pk}/dns/", {"record_type": "MX", "content": "mail.example.com"})
    assert response.status_code == 400


def test_dns_write_404s_for_a_stranger_to_the_domain(api, customer, manager, client_obj, com_pricing):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    api.force_authenticate(customer)
    assert api.post(f"/api/v1/domains/{domain.pk}/dns/", {"record_type": "A", "content": "1.2.3.4"}).status_code == 404


def test_dns_write_denied_for_staff_without_manage_domains(api, staff, manager, client_obj, com_pricing):
    domain = services.request_registration(manager, client_obj, "example.com", 1)
    api.force_authenticate(staff(Role.SUPPORT_AGENT))
    response = api.post(f"/api/v1/domains/{domain.pk}/dns/", {"record_type": "A", "content": "1.2.3.4"})
    assert response.status_code == 403


# --- Sync (staff only) --------------------------------------------------------------------------

def test_sync_requires_manage_domains(api, owner, manager, client_obj, com_pricing):
    domain = services.complete_registration(
        manager, services.request_registration(manager, client_obj, "example.com", 1))
    api.force_authenticate(owner)
    assert api.post(f"/api/v1/domains/{domain.pk}/sync/").status_code == 403
    api.force_authenticate(manager)
    assert api.post(f"/api/v1/domains/{domain.pk}/sync/").status_code == 200
