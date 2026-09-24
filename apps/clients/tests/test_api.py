import pytest
from django.core import mail

from apps.accounts.models import User
from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services
from apps.clients.models import Client, ClientContact, ContactRole
from conftest import PASSWORD

pytestmark = pytest.mark.django_db


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def client_obj(manager):
    return services.create_client(manager, {
        "company_name": "Acme Ltd", "first_name": "Ada", "last_name": "Lovelace", "email": "ada@acme.test",
        "country": "gb", "currency": "gbp",
    })


# --- Registration integration -----------------------------------------------------------

def test_registration_creates_owned_client(api):
    api.post("/api/v1/auth/register/", {"email": "new@example.com", "password": PASSWORD,
                                        "first_name": "Nia", "company_name": "Nia Co"})
    user = User.objects.get(email="new@example.com")
    client = Client.objects.get(contacts__user=user)
    assert client.company_name == "Nia Co" and client.email == "new@example.com"
    assert ClientContact.objects.get(client=client, user=user).role == ContactRole.OWNER


# --- Staff CRUD ----------------------------------------------------------------------------

def test_manager_creates_client_with_new_owner_invited(api, manager):
    api.force_authenticate(manager)
    response = api.post("/api/v1/clients/", {
        "first_name": "Grace", "last_name": "Hopper", "email": "Grace@Navy.test", "country": "us",
        "notes": "VIP",
    })
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["reference"].startswith("C") and body["country"] == "US" and body["email"] == "grace@navy.test"
    assert [c["role"] for c in body["contacts"]] == ["owner"]

    owner = User.objects.get(email="grace@navy.test")
    assert owner.role == Role.CUSTOMER and not owner.has_usable_password()
    assert len(mail.outbox) == 1 and "account is ready" in mail.outbox[0].subject
    assert AuditEvent.objects.filter(action="client.created", metadata__owner_created=True).exists()


def test_create_client_links_existing_customer_without_email(api, manager, customer):
    api.force_authenticate(manager)
    response = api.post("/api/v1/clients/", {"first_name": "C", "email": "billing@corp.test",
                                             "owner_email": customer.email})
    assert response.status_code == 201
    assert Client.objects.get(pk=response.json()["id"]).contacts.get().user == customer
    assert len(mail.outbox) == 0


def test_staff_user_cannot_be_client_contact(api, manager):
    api.force_authenticate(manager)
    response = api.post("/api/v1/clients/", {"first_name": "C", "email": manager.email})
    assert response.status_code == 400 and response.json()["error"]["code"] == "staff_contact"
    assert not Client.objects.exists()  # transaction rolled back


def test_create_validates_country_and_required_fields(api, manager):
    api.force_authenticate(manager)
    response = api.post("/api/v1/clients/", {"first_name": "C", "email": "c@x.test", "country": "USA"})
    assert response.status_code == 400
    assert "country" in response.json()["error"]["details"]
    assert api.post("/api/v1/clients/", {"email": "c@x.test"}).status_code == 400


def test_staff_update_is_audited_with_changed_fields(api, manager, client_obj):
    api.force_authenticate(manager)
    response = api.patch(f"/api/v1/clients/{client_obj.pk}/", {"phone": "+44 20 7946 0000", "notes": "Call first"})
    assert response.status_code == 200 and response.json()["notes"] == "Call first"
    event = AuditEvent.objects.get(action="client.updated")
    assert event.metadata["fields"] == ["notes", "phone"]


def test_status_change(api, manager, client_obj):
    api.force_authenticate(manager)
    response = api.post(f"/api/v1/clients/{client_obj.pk}/status/", {"status": "inactive", "reason": "no reply"})
    assert response.status_code == 200 and response.json()["status"] == "inactive"
    assert AuditEvent.objects.get(action="client.status_changed").metadata["reason"] == "no reply"


def test_list_search_filter_and_pagination(api, manager, client_obj):
    services.create_client(manager, {"first_name": "Bob", "email": "bob@beta.test", "country": "PK"})
    api.force_authenticate(manager)
    assert api.get("/api/v1/clients/").json()["count"] == 2
    assert api.get("/api/v1/clients/?search=acme").json()["count"] == 1
    assert api.get(f"/api/v1/clients/?search={client_obj.reference}").json()["count"] == 1
    assert api.get("/api/v1/clients/?search=ada@acme").json()["count"] == 1  # contact email
    assert api.get("/api/v1/clients/?country=PK").json()["count"] == 1
    assert api.get("/api/v1/clients/?status=closed").json()["count"] == 0
    assert len(api.get("/api/v1/clients/?page_size=1").json()["results"]) == 1


# --- Contacts ---------------------------------------------------------------------------------

def test_contact_add_role_change_remove(api, manager, client_obj):
    api.force_authenticate(manager)
    added = api.post(f"/api/v1/clients/{client_obj.pk}/contacts/", {"email": "tech@acme.test", "role": "technical"})
    assert added.status_code == 201
    contact_id = added.json()["id"]
    dup = api.post(f"/api/v1/clients/{client_obj.pk}/contacts/", {"email": "tech@acme.test"})
    assert dup.json()["error"]["code"] == "duplicate_contact"

    changed = api.patch(f"/api/v1/clients/{client_obj.pk}/contacts/{contact_id}/", {"role": "billing"})
    assert changed.status_code == 200 and changed.json()["role"] == "billing"
    assert api.delete(f"/api/v1/clients/{client_obj.pk}/contacts/{contact_id}/").status_code == 204
    actions = set(AuditEvent.objects.filter(target_id=str(client_obj.pk)).values_list("action", flat=True))
    assert {"client.contact_added", "client.contact_role_changed", "client.contact_removed"} <= actions


def test_last_owner_cannot_be_removed_or_demoted(api, manager, client_obj):
    owner = client_obj.contacts.get(role=ContactRole.OWNER)
    api.force_authenticate(manager)
    url = f"/api/v1/clients/{client_obj.pk}/contacts/{owner.pk}/"
    assert api.delete(url).json()["error"]["code"] == "last_owner"
    assert api.patch(url, {"role": "technical"}).json()["error"]["code"] == "last_owner"


def test_contact_of_other_client_is_not_reachable(api, manager, client_obj):
    other = services.create_client(manager, {"first_name": "O", "email": "o@other.test"})
    other_contact = other.contacts.get()
    api.force_authenticate(manager)
    assert api.delete(f"/api/v1/clients/{client_obj.pk}/contacts/{other_contact.pk}/").status_code == 404


def test_activity_lists_client_events(api, manager, client_obj):
    api.force_authenticate(manager)
    api.post(f"/api/v1/clients/{client_obj.pk}/status/", {"status": "inactive"})
    actions = [e["action"] for e in api.get(f"/api/v1/clients/{client_obj.pk}/activity/").json()["results"]]
    assert actions == ["client.status_changed", "client.created"]


# --- Authorization ---------------------------------------------------------------------------

def test_customer_cannot_use_staff_client_api(api, customer, client_obj):
    api.force_authenticate(customer)
    assert api.get("/api/v1/clients/").status_code == 403
    assert api.get(f"/api/v1/clients/{client_obj.pk}/").status_code == 403
    assert api.post("/api/v1/clients/", {"first_name": "x", "email": "x@x.test"}).status_code == 403


def test_support_agent_reads_but_cannot_modify(api, staff, client_obj):
    api.force_authenticate(staff(Role.SUPPORT_AGENT))
    assert api.get("/api/v1/clients/").status_code == 200
    assert api.get(f"/api/v1/clients/{client_obj.pk}/activity/").status_code == 200
    assert api.patch(f"/api/v1/clients/{client_obj.pk}/", {"phone": "1"}).status_code == 403
    assert api.post(f"/api/v1/clients/{client_obj.pk}/status/", {"status": "closed"}).status_code == 403
    assert api.post(f"/api/v1/clients/{client_obj.pk}/contacts/", {"email": "a@b.test"}).status_code == 403


def test_clients_cannot_be_deleted_via_api(api, staff, client_obj):
    api.force_authenticate(staff(Role.SUPER_ADMIN))
    assert api.delete(f"/api/v1/clients/{client_obj.pk}/").status_code == 405


# --- Customer side ------------------------------------------------------------------------------

def test_customer_sees_only_own_clients_without_notes(api, manager, client_obj):
    services.create_client(manager, {"first_name": "Other", "email": "other@x.test"})
    owner = client_obj.contacts.get().user
    api.force_authenticate(owner)
    results = api.get("/api/v1/me/clients/").json()["results"]
    assert [r["id"] for r in results] == [client_obj.pk]
    assert "notes" not in results[0] and results[0]["my_role"] == "owner"


def test_owner_updates_own_client_but_not_restricted_fields(api, client_obj):
    owner = client_obj.contacts.get().user
    api.force_authenticate(owner)
    response = api.patch(f"/api/v1/me/clients/{client_obj.pk}/", {
        "company_name": "Acme Group", "currency": "USD", "notes": "hack", "status": "closed",
    })
    assert response.status_code == 200
    client_obj.refresh_from_db()
    assert client_obj.company_name == "Acme Group"
    assert (client_obj.currency, client_obj.notes, client_obj.status) == ("GBP", "", "active")


def test_technical_contact_cannot_edit(api, manager, client_obj):
    services.add_contact(manager, client_obj, email="tech@acme.test", role=ContactRole.TECHNICAL)
    tech = User.objects.get(email="tech@acme.test")
    api.force_authenticate(tech)
    assert api.get(f"/api/v1/me/clients/{client_obj.pk}/").status_code == 200
    response = api.patch(f"/api/v1/me/clients/{client_obj.pk}/", {"company_name": "Pwned"})
    assert response.status_code == 403


def test_customer_cannot_reach_other_client(api, manager, client_obj, customer):
    api.force_authenticate(customer)
    assert api.get(f"/api/v1/me/clients/{client_obj.pk}/").status_code == 404
    assert api.patch(f"/api/v1/me/clients/{client_obj.pk}/", {"company_name": "x"}).status_code == 404


def test_closed_client_cannot_be_edited_by_customer(api, manager, client_obj):
    services.set_client_status(manager, client_obj, "closed")
    api.force_authenticate(client_obj.contacts.get().user)
    assert api.patch(f"/api/v1/me/clients/{client_obj.pk}/", {"company_name": "x"}).status_code == 403


def test_invited_contact_can_set_password_from_welcome_email(client, manager):
    import re

    services.create_client(manager, {"first_name": "Inv", "email": "invited@x.test"})
    path = re.search(r"https?://[^/\s]+(/\S+)", mail.outbox[0].body).group(1)
    response = client.post(path, {"new_password": "An0ther-Strong-Pass", "new_password_confirm": "An0ther-Strong-Pass"})
    assert response.status_code == 302
    assert client.login(email="invited@x.test", password="An0ther-Strong-Pass")
