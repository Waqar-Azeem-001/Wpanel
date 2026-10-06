"""The infrastructure provider login on a server (Contabo): storing it safely, and looking at the machines it owns (read-only)."""
from unittest import mock

import pytest
import requests
from django.conf import settings

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.core.exceptions import ServiceError
from apps.hosting import infra, services
from apps.products import services as product_services

pytestmark = pytest.mark.django_db

SECRET, PASSWORD = "client-secret-VALUE-123", "api-password-VALUE-456"
LOGIN = {"infra_provider": "contabo", "infra_client_id": "INT-12345678", "infra_client_secret": SECRET,
         "infra_api_user": "me@example.com", "infra_api_password": PASSWORD}


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def server(manager):
    return product_services.create_server(manager, {"name": "north", "hostname": "north.example.com",
                                                    "ip_address": "203.0.113.7", **LOGIN})


def response(body, status=200):
    reply = mock.Mock()
    reply.status_code = status
    reply.json.return_value = body
    reply.raise_for_status = mock.Mock()
    if status >= 400:
        reply.raise_for_status.side_effect = requests.HTTPError("error")
    return reply


TOKEN = {"access_token": "short-lived-token"}
MACHINES = {"data": [
    {"instanceId": 101, "name": "vmi1", "displayName": "North Hill", "status": "running", "region": "EU",
     "ipConfig": {"v4": {"ip": "203.0.113.7"}}},
    {"instanceId": 102, "name": "vmi2", "status": "stopped", "region": "US-central", "ipConfig": {"v4": {"ip": "198.51.100.9"}}},
]}


# --- Storing the login --------------------------------------------------------------------------------------------

def test_the_secrets_are_stored_encrypted_and_the_audit_records_only_that_they_were_set(manager, server):
    assert server.infra_provider == "contabo" and server.infra_client_id == "INT-12345678" and server.infra_configured
    assert SECRET not in server.infra_client_secret_encrypted and PASSWORD not in server.infra_api_password_encrypted
    assert server.get_infra_client_secret() == SECRET and server.get_infra_api_password() == PASSWORD
    event = AuditEvent.objects.get(action="server.created")
    assert event.metadata["infra_secrets_set"] == ["client_secret", "api_password"] and SECRET not in str(event.metadata)


def test_leaving_the_secrets_blank_keeps_them_and_new_ones_replace_them(manager, server):
    product_services.update_server(manager, server, {"name": "north", "infra_client_secret": "", "infra_api_password": ""})
    server.refresh_from_db()
    assert server.get_infra_client_secret() == SECRET and server.get_infra_api_password() == PASSWORD
    product_services.update_server(manager, server, {"infra_client_secret": "new-secret-789"})
    server.refresh_from_db()
    assert server.get_infra_client_secret() == "new-secret-789" and server.get_infra_api_password() == PASSWORD
    assert AuditEvent.objects.filter(action="server.updated").last().metadata.get("infra_secrets_changed", True) is not None


def test_the_api_never_echoes_the_provider_secrets(api, manager, server):
    api.force_authenticate(manager)
    body = api.get(f"/api/v1/servers/{server.pk}/").content.decode()
    assert "INT-12345678" in body and SECRET not in body and PASSWORD not in body and "infra_client_secret" not in body


# --- Talking to Contabo (mocked: no network) -----------------------------------------------------------------------------

def test_the_login_is_a_password_grant_in_a_post_body_and_only_reads_the_machines():
    with mock.patch("requests.post", return_value=response(TOKEN)) as post, \
            mock.patch("requests.get", return_value=response(MACHINES)) as get:
        found = infra.contabo_instances("INT-1", SECRET, "me@example.com", PASSWORD)
    sent = post.call_args.kwargs["data"]
    assert sent == {"client_id": "INT-1", "client_secret": SECRET, "username": "me@example.com", "password": PASSWORD,
                    "grant_type": "password"}
    assert post.call_args.args[0] == settings.CONTABO_AUTH_URL and "?" not in post.call_args.args[0]  # nothing secret in the address
    assert get.call_args.args[0].endswith("/v1/compute/instances")
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer short-lived-token"
    assert found == [{"id": "101", "name": "North Hill", "status": "running", "region": "EU", "ip": "203.0.113.7"},
                     {"id": "102", "name": "vmi2", "status": "stopped", "region": "US-central", "ip": "198.51.100.9"}]


@pytest.mark.parametrize("status", [400, 401, 403])
def test_a_refused_login_gets_a_plain_explanation(status):
    with mock.patch("requests.post", return_value=response({"error": "invalid_grant"}, status)):
        with pytest.raises(infra.InfraError, match="refused the login"):
            infra.contabo_instances("INT-1", SECRET, "me@example.com", PASSWORD)


def test_missing_details_unreachable_service_and_odd_answers_are_reported_without_secrets():
    with pytest.raises(infra.InfraError, match="Enter the client ID"):
        infra.contabo_instances("INT-1", "", "me@example.com", PASSWORD)
    with mock.patch("requests.post", side_effect=requests.ConnectionError("no route to host?password=" + PASSWORD)):
        with pytest.raises(infra.InfraError) as exc:
            infra.contabo_instances("INT-1", SECRET, "me@example.com", PASSWORD)
    assert "Could not reach" in str(exc.value) and PASSWORD not in str(exc.value)
    with mock.patch("requests.post", return_value=response({"nothing": 1})):
        with pytest.raises(infra.InfraError, match="unexpected"):
            infra.contabo_instances("INT-1", SECRET, "me@example.com", PASSWORD)
    with mock.patch("requests.post", return_value=response(TOKEN)), mock.patch("requests.get", side_effect=requests.Timeout("slow")):
        with pytest.raises(infra.InfraError, match="did not return"):
            infra.contabo_instances("INT-1", SECRET, "me@example.com", PASSWORD)


# --- The service ----------------------------------------------------------------------------------------------------

def test_the_check_finds_the_machine_with_this_servers_ip(manager, server):
    with mock.patch("requests.post", return_value=response(TOKEN)), mock.patch("requests.get", return_value=response(MACHINES)):
        result = services.check_infrastructure(manager, server)
    assert result["provider"] == "Contabo" and len(result["instances"]) == 2 and result["match"]["name"] == "North Hill"
    event = AuditEvent.objects.get(action="server.infra_tested")
    assert event.metadata == {"provider": "contabo", "machines": 2, "matched": True}


def test_with_no_ip_saved_a_single_machine_is_suggested(manager, server):
    server.ip_address = None
    server.save(update_fields=["ip_address"])
    one = {"data": MACHINES["data"][:1]}
    with mock.patch("requests.post", return_value=response(TOKEN)), mock.patch("requests.get", return_value=response(one)):
        result = services.check_infrastructure(manager, server)
    assert result["match"] is None and result["suggested_ip"] == "203.0.113.7"


def test_failure_is_audited_without_secrets_and_the_permission_is_needed(manager, server, customer):
    with mock.patch("requests.post", return_value=response({}, 401)):
        with pytest.raises(ServiceError) as exc:
            services.check_infrastructure(manager, server)
    assert exc.value.code == "infra_failed"
    failure = AuditEvent.objects.get(action="server.infra_failed")
    assert SECRET not in str(failure.metadata) and PASSWORD not in str(failure.metadata)
    with pytest.raises(ServiceError) as denied:
        services.check_infrastructure(customer, server)
    assert denied.value.status_code == 403
    server.infra_provider = ""
    with pytest.raises(ServiceError, match="Choose an infrastructure provider"):
        services.check_infrastructure(manager, server)


# --- The staff pages -----------------------------------------------------------------------------------------------------

def test_the_server_form_has_the_provider_fields_and_never_shows_the_secrets(client, manager, server):
    client.force_login(manager)
    page = client.get(f"/staff/servers/{server.pk}/edit/").content.decode()
    for label in ("Provider (who rents you this machine)", "Provider client ID", "Provider client secret", "Provider API user",
                  "Provider API password", "Test the provider login"):
        assert label in page, label
    assert "INT-12345678" in page and "me@example.com" in page and SECRET not in page and PASSWORD not in page


def test_saving_the_form_stores_the_login_and_a_blank_secret_keeps_the_old_one(client, manager):
    client.force_login(manager)
    data = {"name": "fresh", "hostname": "fresh.example.com", "kind": "manual", "api_port": "2087", "use_ssl": "on",
            "verify_ssl": "on", "infra_provider": "contabo", "infra_client_id": "INT-1", "infra_client_secret": SECRET,
            "infra_api_user": "me@example.com", "infra_api_password": PASSWORD}
    client.post("/staff/servers/new/", data)
    from apps.products.models import Server

    server = Server.objects.get(name="fresh")
    assert server.infra_configured and server.get_infra_client_secret() == SECRET
    client.post(f"/staff/servers/{server.pk}/edit/", {**data, "infra_client_secret": "", "infra_api_password": ""})
    server.refresh_from_db()
    assert server.get_infra_client_secret() == SECRET and server.get_infra_api_password() == PASSWORD


def test_the_test_button_reports_the_machines_and_the_ip_match(client, manager, server):
    client.force_login(manager)
    with mock.patch("requests.post", return_value=response(TOKEN)), mock.patch("requests.get", return_value=response(MACHINES)):
        page = client.post(f"/staff/servers/{server.pk}/infra-test/", follow=True).content.decode()
    assert "Connected to Contabo. 2 machine(s)" in page and "North Hill (203.0.113.7, running)" in page
    assert "matches &#x27;North Hill&#x27;" in page or "matches 'North Hill'" in page
    server.ip_address = "192.0.2.1"
    server.save(update_fields=["ip_address"])
    with mock.patch("requests.post", return_value=response(TOKEN)), mock.patch("requests.get", return_value=response(MACHINES)):
        page = client.post(f"/staff/servers/{server.pk}/infra-test/", follow=True).content.decode()
    assert "None of those machines has the IP address 192.0.2.1" in page


def test_a_failed_test_shows_why_and_the_page_needs_permission_and_a_post(client, manager, customer, server):
    client.force_login(manager)
    with mock.patch("requests.post", return_value=response({}, 401)):
        page = client.post(f"/staff/servers/{server.pk}/infra-test/", follow=True).content.decode()
    assert "Contabo refused the login" in page and SECRET not in page and PASSWORD not in page
    assert client.get(f"/staff/servers/{server.pk}/infra-test/").status_code == 405
    client.force_login(customer)
    assert client.post(f"/staff/servers/{server.pk}/infra-test/").status_code in (302, 403)
