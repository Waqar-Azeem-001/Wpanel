"""Node enrolment (N2): one-time token, own CA, certificate request, inventory, heartbeat, renewal, revocation, mutual-auth switch, staff screen."""
import json
from datetime import timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.roles import Role
from apps.agent import enrollment, envelope, reference_agent, services
from apps.agent.models import AgentNode, CertificateAuthority, EnrollmentToken, NodeStatus
from apps.audit.models import AuditEvent
from apps.core.exceptions import ServiceError
from apps.products.models import Server, ServerKind

pytestmark = pytest.mark.django_db
URL = "/api/agent/v1/"


@pytest.fixture
def server():
    return Server.objects.create(name="native-1", hostname="n1.example.com", kind=ServerKind.NATIVE)


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


def post(path, body, token=None, **extra):
    headers = {"HTTP_AUTHORIZATION": f"Agent {token}"} if token else {}
    return Client().post(URL + path, data=json.dumps(body), content_type="application/json", HTTP_X_AGENT_VERSION="0.1.0", **headers, **extra)


def enroll(token, csr=None, inventory=None):
    return post("enroll/", {"token": token, "csr": csr or reference_agent.make_csr()[1], "node": inventory or reference_agent.INVENTORY, "agent_version": "0.1.0"})


@pytest.fixture
def enrolled(server, manager):
    node, token = enrollment.invite(manager, server)
    reply = enroll(token).json()
    return AgentNode.objects.get(pk=node.pk), reply


def test_a_good_enrolment_returns_identity_and_records_the_node(server, manager):
    node, token = enrollment.invite(manager, server)
    assert node.status == NodeStatus.PENDING and token.startswith("whe_")
    assert EnrollmentToken.objects.get().token_hash != token  # only a hash is stored
    reply = enroll(token)
    assert reply.status_code == 200
    body = reply.json()
    node.refresh_from_db()
    assert node.status == NodeStatus.ENROLLED and body["node_id"] == server.pk and body["signing_public_key"] == envelope.public_key_b64()
    assert (node.hostname, node.cpu_cores, node.ram_mb, node.disk_gb) == ("node1.example.com", 8, 32768, 500)
    assert node.ip_addresses == ["203.0.113.10", "2001:db8::10"] and node.capabilities == ["database", "php", "web"]
    server.refresh_from_db()
    assert server.ip_address == "203.0.113.10"
    assert {"agent.node_invited", "agent.node_enrolled"} <= set(AuditEvent.objects.values_list("action", flat=True))
    assert token not in json.dumps(list(AuditEvent.objects.values_list("metadata", flat=True)))


def test_the_certificate_is_signed_by_our_ca_for_client_auth_and_names_the_node_itself(enrolled):
    node, body = enrolled
    cert = x509.load_pem_x509_certificate(body["certificate"].encode())
    ca = x509.load_pem_x509_certificate(body["ca_certificate"].encode())
    cert.verify_directly_issued_by(ca)
    assert cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value == f"node-{node.pk}"  # not the name the agent asked for
    assert x509.ExtendedKeyUsageOID.CLIENT_AUTH in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert node.cert_fingerprint == cert.fingerprint(hashes.SHA256()).hex()
    assert CertificateAuthority.objects.count() == 1 and "PRIVATE" not in CertificateAuthority.objects.get().key_encrypted


def test_the_token_works_once_only(server, manager):
    _, token = enrollment.invite(manager, server)
    assert enroll(token).status_code == 200
    assert enroll(token).status_code == 401


def test_unknown_and_expired_tokens_are_refused_and_audited(server, manager):
    assert enroll("whe_nonsense").status_code == 401 and enroll(None).status_code == 401
    node, token = enrollment.invite(manager, server)
    EnrollmentToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert enroll(token).status_code == 401
    assert AuditEvent.objects.filter(action="agent.enroll_refused").count() == 3


def test_a_bad_request_does_not_burn_the_token(server, manager):
    _, token = enrollment.invite(manager, server)
    assert enroll(token, csr="garbage").status_code == 400
    assert enroll(token, csr=reference_agent.make_csr("weak")[1]).status_code == 400
    assert enroll(token, inventory={**reference_agent.INVENTORY, "cpu_cores": 0}).status_code == 400
    assert enroll(token).status_code == 200


@pytest.mark.parametrize("change", [{"ip_addresses": ["not-an-ip"]}, {"ip_addresses": []}, {"capabilities": ["root-shell"]}, {"capabilities": []}, {"hostname": ""},
                                     {"hostname": "a\nb"}, {"ram_mb": True}, {"disk_gb": "big"}, {"cpu_cores": 10 ** 9}])
def test_the_inventory_is_validated(server, manager, change):
    _, token = enrollment.invite(manager, server)
    assert enroll(token, inventory={**reference_agent.INVENTORY, **change}).status_code == 400


def test_a_new_invitation_replaces_the_old_one(server, manager):
    _, first = enrollment.invite(manager, server)
    _, second = enrollment.invite(manager, server)
    assert enroll(first).status_code == 401 and enroll(second).status_code == 200


def test_an_enrolled_node_cannot_be_invited_again_until_revoked(enrolled, manager):
    node, _ = enrolled
    with pytest.raises(ServiceError, match="Revoke"):
        enrollment.invite(manager, node.server)
    enrollment.revoke(manager, node)
    _, token = enrollment.invite(manager, node.server)
    assert enroll(token).status_code == 200 and AgentNode.objects.get(pk=node.pk).status == NodeStatus.ENROLLED


def test_the_issued_token_works_for_poll_and_heartbeat_and_a_wrong_one_does_not(enrolled):
    node, body = enrolled
    assert post("poll/", {}, body["token"]).status_code == 200
    assert post("heartbeat/", {"services": {"web": "up"}}, body["token"]).status_code == 200
    assert post("poll/", {}, "nope").status_code == 401 and post("heartbeat/", {}).status_code == 401


def test_heartbeat_stores_figures_and_sets_health(enrolled):
    node, body = enrolled
    post("heartbeat/", {"cpu_percent": 12.5, "memory_used_mb": 2048, "disk_used_gb": 40, "sites": 3, "load_1m": 0.4, "evil": "x",
                        "services": {"web": "up", "database": "degraded"}}, body["token"])
    node.refresh_from_db()
    assert node.health["sites"] == 3 and "evil" not in node.health and enrollment.health_state(node) == "warning"
    assert any("backup storage" in r for r in __import__("apps.agent.monitoring", fromlist=["x"]).evaluate(node)["checks"][-1:][0].values())  # a web node without backup storage is a warning
    from apps.agent.models import BackupRepository

    BackupRepository.objects.create(node=node, endpoint="https://s.example.net", bucket="bkt", prefix="n", applied_ok=True)
    node.health = {"services": {"web": "up"}}
    assert enrollment.health_state(node) == "healthy"
    node.health = {"services": {"web": "down"}}
    assert enrollment.health_state(node) == "critical"
    assert enrollment.health_state(node, now=timezone.now() + timedelta(minutes=10)) == "offline"


@pytest.mark.parametrize("data", [{"cpu_percent": 500}, {"sites": True}, {"cpu_percent": "high"}, {"services": {"web": "great"}}, {"services": []}])
def test_a_silly_heartbeat_is_refused(enrolled, data):
    assert post("heartbeat/", data, enrolled[1]["token"]).status_code == 400


def test_revocation_cuts_the_node_off_and_cancels_its_queued_work(enrolled, manager):
    node, body = enrolled
    job = services.dispatch(manager, node.server, "CREATE_DATABASE", {"account": "abcd1234", "name": "first_db"})
    enrollment.revoke(manager, node)
    job.refresh_from_db()
    assert job.status == "cancelled" and post("poll/", {}, body["token"]).status_code == 401
    with pytest.raises(ServiceError, match="no enrolled agent"):
        services.dispatch(manager, node.server, "CREATE_DATABASE", {"account": "abcd1234", "name": "other_db"})
    assert AuditEvent.objects.filter(action="agent.node_revoked").exists()


def test_renewal_replaces_the_certificate(enrolled):
    node, body = enrolled
    old = node.cert_fingerprint
    reply = post("renew/", {"csr": reference_agent.make_csr()[1]}, body["token"])
    node.refresh_from_db()
    assert reply.status_code == 200 and node.cert_fingerprint != old
    assert post("renew/", {"csr": "x"}, body["token"]).status_code == 400 and post("renew/", {"csr": "x"}).status_code == 401


def test_when_client_certificates_are_required_the_fingerprint_must_match(enrolled, settings):
    node, body = enrolled
    settings.AGENT_REQUIRE_CLIENT_CERT = True
    assert post("poll/", {}, body["token"]).status_code == 401
    assert post("poll/", {}, body["token"], HTTP_X_CLIENT_CERT_FINGERPRINT="00" * 32).status_code == 401
    assert post("poll/", {}, body["token"], HTTP_X_CLIENT_CERT_FINGERPRINT=node.cert_fingerprint.upper()).status_code == 200


def test_a_pending_node_cannot_use_the_api_with_a_guessed_token(server, manager):
    enrollment.invite(manager, server)
    assert services.authenticate("") is None and services.authenticate("x") is None


def test_the_native_server_type_has_its_own_adapter():
    from apps.hosting.adapters.native import NativeAdapter
    from apps.hosting.adapters.registry import ADAPTERS

    assert ADAPTERS["native"] is NativeAdapter


# --- the staff screen ---------------------------------------------------------------------------------------------------------------------

def test_staff_can_add_a_node_and_see_the_token_once(client, manager):
    client.force_login(manager)
    page = client.post(reverse("agent_staff:add"), {"name": "Native A", "hostname": "a.example.com", "region": "EU"})
    assert page.status_code == 200 and b"wh-agent enroll" in page.content and b"shown once" in page.content
    assert Server.objects.get(name="Native A").kind == ServerKind.NATIVE
    again = client.get(reverse("agent_staff:nodes"))
    assert b"whe_" not in again.content and b"Waiting for the agent" in again.content


def test_staff_screen_shows_an_enrolled_node_and_allows_revoke_and_capacity(client, manager, enrolled):
    node, _ = enrolled
    client.force_login(manager)
    html = client.get(reverse("agent_staff:nodes")).content.decode()
    assert "node1.example.com" in html and "AlmaLinux" in html and "32768" in html
    client.post(reverse("agent_staff:capacity", args=[node.pk]), {"capacity_sites": "150"})
    client.post(reverse("agent_staff:capacity", args=[node.pk]), {"capacity_sites": "abc"})
    node.refresh_from_db()
    assert node.capacity_sites == 150
    client.post(reverse("agent_staff:revoke", args=[node.pk]))
    node.refresh_from_db()
    assert node.status == NodeStatus.REVOKED


def test_the_staff_screen_needs_the_right_permission(client, customer):
    assert client.get(reverse("agent_staff:nodes")).status_code == 302
    client.force_login(customer)
    assert client.get(reverse("agent_staff:nodes")).status_code == 403
    assert client.post(reverse("agent_staff:add"), {"name": "x", "hostname": "x.com"}).status_code == 403
