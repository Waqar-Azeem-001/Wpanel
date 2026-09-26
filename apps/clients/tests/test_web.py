import pytest

from apps.accounts.models import User
from apps.accounts.roles import Role
from apps.clients import services
from apps.clients.models import Client, ContactRole

pytestmark = pytest.mark.django_db


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def acme(manager):
    return services.create_client(manager, {"company_name": "Acme Ltd", "first_name": "Ada", "email": "ada@acme.test"})


def test_staff_pages_require_login_and_permission(client, customer, acme):
    assert client.get("/staff/clients/").status_code == 302
    client.force_login(customer)
    assert client.get("/staff/clients/").status_code == 403
    assert client.get(f"/staff/clients/{acme.pk}/").status_code == 403
    assert client.post(f"/staff/clients/{acme.pk}/status/", {"status": "closed"}).status_code == 403


def test_support_agent_can_view_but_not_manage(client, staff, acme):
    client.force_login(staff(Role.SUPPORT_AGENT))
    detail = client.get(f"/staff/clients/{acme.pk}/")
    assert detail.status_code == 200 and b"Acme Ltd" in detail.content
    assert b"Add user" not in detail.content
    assert client.get("/staff/clients/new/").status_code == 403
    assert client.get(f"/staff/clients/{acme.pk}/edit/").status_code == 403


def test_list_search_and_htmx_partial(client, manager, acme):
    services.create_client(manager, {"first_name": "Bob", "email": "bob@beta.test"})
    client.force_login(manager)
    full = client.get("/staff/clients/?q=acme")
    assert b"<html" in full.content and b"Acme Ltd" in full.content and b"bob@beta.test" not in full.content
    partial = client.get("/staff/clients/?q=beta", HTTP_HX_REQUEST="true")
    assert b"<html" not in partial.content and b"bob@beta.test" in partial.content
    assert b"Acme" not in client.get("/staff/clients/?status=closed").content


def test_create_edit_status_via_web(client, manager):
    client.force_login(manager)
    response = client.post("/staff/clients/new/", {
        "first_name": "Web", "email": "web@client.test", "currency": "USD", "country": "pk",
    })
    new = Client.objects.get(email="web@client.test")
    assert response.status_code == 302 and response["Location"] == f"/staff/clients/{new.pk}/"
    assert new.country == "PK"

    client.post(f"/staff/clients/{new.pk}/edit/", {
        "first_name": "Web", "email": "web@client.test", "currency": "USD", "company_name": "WebCo",
    })
    new.refresh_from_db()
    assert new.company_name == "WebCo"

    client.post(f"/staff/clients/{new.pk}/status/", {"status": "inactive", "reason": "test"})
    new.refresh_from_db()
    assert new.status == "inactive"
    detail = client.get(f"/staff/clients/{new.pk}/").content
    assert b"client.status_changed" in detail and b"client.updated" in detail


def test_create_shows_validation_errors(client, manager):
    client.force_login(manager)
    response = client.post("/staff/clients/new/", {"first_name": "X", "email": "x@x.test", "currency": "USD",
                                                    "country": "P1"})
    assert response.status_code == 200 and b"two-letter" in response.content
    assert not Client.objects.exists()


def test_contacts_managed_via_web(client, manager, acme):
    client.force_login(manager)
    client.post(f"/staff/clients/{acme.pk}/contacts/", {"email": "tech@acme.test", "role": "technical"})
    contact = acme.contacts.get(user__email="tech@acme.test")
    client.post(f"/staff/clients/{acme.pk}/contacts/{contact.pk}/role/", {"role": "billing"})
    contact.refresh_from_db()
    assert contact.role == ContactRole.BILLING

    owner = acme.contacts.get(role=ContactRole.OWNER)
    response = client.post(f"/staff/clients/{acme.pk}/contacts/{owner.pk}/remove/", follow=True)
    assert b"at least one owner" in response.content
    client.post(f"/staff/clients/{acme.pk}/contacts/{contact.pk}/remove/")
    assert not acme.contacts.filter(pk=contact.pk).exists()


def test_customer_account_page_edit_and_readonly(client, manager, acme):
    owner = acme.contacts.get().user
    owner.set_password("Str0ng-Passw0rd!x")
    owner.save()
    client.force_login(owner)
    response = client.get("/account/client/")
    assert response.status_code == 302 and response["Location"] == f"/account/client/{acme.pk}/"
    page = client.get(f"/account/client/{acme.pk}/")
    assert b"Internal notes" not in page.content and b"name=\"company_name\"" in page.content
    client.post(f"/account/client/{acme.pk}/", {"first_name": "Ada", "email": "ada@acme.test",
                                               "company_name": "Acme Two"})
    acme.refresh_from_db()
    assert acme.company_name == "Acme Two"

    services.add_contact(manager, acme, email="tech@acme.test", role=ContactRole.TECHNICAL)
    client.force_login(User.objects.get(email="tech@acme.test"))
    page = client.get(f"/account/client/{acme.pk}/")
    assert b"name=\"company_name\"" not in page.content
    assert client.post(f"/account/client/{acme.pk}/", {"first_name": "X", "email": "x@x.test"}).status_code == 403


def test_customer_cannot_open_other_client_page(client, customer, acme):
    client.force_login(customer)
    assert client.get(f"/account/client/{acme.pk}/").status_code == 404


def test_nav_links_by_role(client, manager, customer):
    client.force_login(manager)
    assert b"/staff/clients/" in client.get("/account/profile/").content
    services.create_client_for_registration(customer)
    client.force_login(customer)
    content = client.get("/account/profile/").content
    assert b"/staff/clients/" not in content and b"/account/client/" in content
