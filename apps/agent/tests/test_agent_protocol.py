"""The WH Agent protocol (N1): allow-list, signed envelopes, replay, expiry, idempotency, leases, versions, structured results, audit."""
import base64
import json
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.roles import Role
from apps.agent import catalog, envelope, reference_agent, services
from apps.agent.models import AgentJob, JobStatus
from apps.audit.models import AuditEvent
from apps.core.exceptions import ServiceError
from apps.products.models import Server, ServerKind

pytestmark = pytest.mark.django_db

GOOD = {"account": "abcd1234", "domain": "acme.com", "php_version": "8.3"}


@pytest.fixture
def node():
    server = Server.objects.create(name="native-1", hostname="n1.test", kind=ServerKind.MANUAL)
    return services.issue_token(server)


@pytest.fixture
def agent(node):
    agent_node, token = node
    return reference_agent.ReferenceAgent(Client(), token, agent_node.server_id, envelope.public_key_b64())


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


# --- the allow-list ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("command,params", [
    ("RUN_SHELL", {"cmd": "id"}), ("CREATE_WEBSITE", {**GOOD, "extra": "x"}), ("CREATE_WEBSITE", {"account": "abcd1234"}),
    ("CREATE_WEBSITE", {**GOOD, "domain": "acme.com; rm -rf /"}), ("CREATE_WEBSITE", {**GOOD, "domain": "../../etc/passwd"}),
    ("CREATE_WEBSITE", {**GOOD, "account": "root user"}), ("CREATE_WEBSITE", {**GOOD, "php_version": "8.3; id"}),
    ("CREATE_DATABASE", {"account": "abcd1234", "name": "x`id`"}), ("CREATE_DATABASE", {"account": "abcd1234", "name": "db\nname"}),
    ("CREATE_MAILBOX", {"account": "abcd1234", "domain": "acme.com", "local_part": "a", "quota_mb": True, "password": "x" * 12}),
    ("CREATE_MAILBOX", {"account": "abcd1234", "domain": "acme.com", "local_part": "a", "quota_mb": 10, "password": "short"}),
    ("RESTORE_BACKUP", {"account": "abcd1234", "backup_id": "../x", "scope": "full"}), (None, {}), ("CREATE_WEBSITE", "not an object"),
])
def test_anything_off_the_allow_list_is_refused(command, params):
    with pytest.raises(catalog.InvalidCommand):
        catalog.validate(command, params)


def test_a_valid_command_passes_and_secrets_are_hidden_when_stored():
    mailbox = {"account": "abcd1234", "domain": "acme.com", "local_part": "info", "quota_mb": 500, "password": "correct horse battery"}
    assert catalog.validate("CREATE_MAILBOX", mailbox) == mailbox
    assert catalog.scrub("CREATE_MAILBOX", mailbox)["password"] == "***" and catalog.scrub("CREATE_MAILBOX", mailbox)["domain"] == "acme.com"


def test_the_catalog_has_no_free_form_text_field():
    for spec in catalog.COMMANDS.values():
        for rule in spec.fields.values():
            assert rule.kind in ("domain", "account", "db_name", "local_part", "backup_id", "enum", "int", "secret")


def test_dispatch_refuses_off_list_commands_and_servers_without_an_agent(manager):
    bare = Server.objects.create(name="bare", hostname="b.test", kind=ServerKind.MANUAL)
    with pytest.raises(ServiceError, match="no agent"):
        services.dispatch(manager, bare, "CREATE_WEBSITE", GOOD)


def test_dispatch_audits_without_secret_values(manager, node):
    mailbox = {"account": "abcd1234", "domain": "acme.com", "local_part": "info", "quota_mb": 500, "password": "correct horse battery"}
    job = services.dispatch(manager, node[0].server, "CREATE_MAILBOX", mailbox)
    event = AuditEvent.objects.get(action="agent.job_queued")
    assert event.metadata["params"]["password"] == "***" and "correct horse" not in json.dumps(event.metadata)
    assert event.metadata["correlation_id"] == job.correlation_id
    with pytest.raises(ServiceError, match="allow-list"):
        services.dispatch(manager, node[0].server, "RUN_SHELL", {"cmd": "id"})


# --- envelopes ----------------------------------------------------------------------------------------------------------------------------

def _env(**over):
    now = int(timezone.now().timestamp())
    payload = {"v": 1, "job": 1, "correlation_id": "c", "node": 7, "command": "CREATE_DATABASE", "params": {"account": "abcd1234", "name": "shop_db"},
               "issued_at": now, "expires_at": now + 300, "nonce": "n" * 32, "idempotency_key": "k", **over}
    return envelope.sign(payload)


def verify(env, **kw):
    return envelope.verify(env, public_key=envelope.public_key_b64(), node=7, **kw)


def test_a_good_envelope_verifies():
    assert verify(_env())["command"] == "CREATE_DATABASE"


def test_a_tampered_payload_or_signature_is_refused():
    env = _env()
    data = json.loads(base64.b64decode(env["payload"]))
    data["params"]["name"] = "other_db"
    forged = {"payload": base64.b64encode(json.dumps(data).encode()).decode(), "signature": env["signature"]}
    for bad in (forged, {**env, "signature": base64.b64encode(b"x" * 64).decode()}, {"payload": env["payload"]}, {}, None):
        with pytest.raises(envelope.EnvelopeError, match="signature"):
            verify(bad)


def test_another_signing_key_is_refused(settings):
    env = _env()
    settings.AGENT_SIGNING_KEY = envelope.generate_key()
    with pytest.raises(envelope.EnvelopeError, match="signature"):
        verify(env)


@pytest.mark.parametrize("over,message", [({"node": 8}, "another node"), ({"v": 2}, "protocol"), ({"nonce": "short"}, "nonce"),
                                          ({"command": "RUN_SHELL"}, "allow-list"), ({"params": {"account": "abcd1234", "name": "x;y"}}, "valid")])
def test_a_signed_but_wrong_envelope_is_refused(over, message):
    with pytest.raises(envelope.EnvelopeError, match=message):
        verify(_env(**over))


def test_expired_and_future_dated_commands_are_refused():
    now = int(timezone.now().timestamp())
    with pytest.raises(envelope.EnvelopeError, match="expired"):
        verify(_env(issued_at=now - 900, expires_at=now - 600))
    with pytest.raises(envelope.EnvelopeError, match="future"):
        verify(_env(issued_at=now + 3600, expires_at=now + 4000))


def test_a_replayed_envelope_is_refused_the_second_time():
    seen, env = envelope.ReplayGuard(), _env()
    verify(env, seen=seen)
    with pytest.raises(envelope.EnvelopeError, match="replay"):
        verify(env, seen=seen)


# --- the queue, end to end with the reference agent ---------------------------------------------------------------------------------------

def test_a_command_goes_through_the_whole_round_trip(manager, node, agent):
    job = services.dispatch(manager, node[0].server, "CREATE_WEBSITE", GOOD)
    assert agent.poll_once() == job.pk
    job.refresh_from_db()
    assert job.status == JobStatus.SUCCEEDED and job.result_ok and job.result_data["root"].endswith("acme.com")
    assert agent.machine.sites["acme.com"]["php"] == "8.3" and agent.poll_once() is None
    assert {"agent.job_queued", "agent.job_succeeded"} <= set(AuditEvent.objects.values_list("action", flat=True))


def test_a_failure_comes_back_structured(manager, node, agent):
    job = services.dispatch(manager, node[0].server, "CHANGE_PHP_VERSION", GOOD)
    agent.poll_once()
    job.refresh_from_db()
    assert job.status == JobStatus.FAILED and job.result_code == "not_found" and "No such website" in job.result_message


def test_the_same_idempotency_key_returns_the_same_job(manager, node):
    first = services.dispatch(manager, node[0].server, "CREATE_WEBSITE", GOOD, idempotency_key="order-1")
    assert services.dispatch(manager, node[0].server, "CREATE_WEBSITE", GOOD, idempotency_key="order-1").pk == first.pk
    assert AgentJob.objects.count() == 1
    with pytest.raises(ServiceError, match="different command"):
        services.dispatch(manager, node[0].server, "CREATE_WEBSITE", {**GOOD, "domain": "other.com"}, idempotency_key="order-1")


def test_jobs_are_handed_out_once_in_order_and_only_to_their_own_node(manager, node, agent):
    other_server = Server.objects.create(name="native-2", hostname="n2.test", kind=ServerKind.MANUAL)
    other_node, other_token = services.issue_token(other_server)
    a = services.dispatch(manager, node[0].server, "CREATE_DATABASE", {"account": "abcd1234", "name": "first_db"})
    b = services.dispatch(manager, node[0].server, "CREATE_DATABASE", {"account": "abcd1234", "name": "second_db"})
    services.dispatch(manager, other_server, "CREATE_DATABASE", {"account": "zzzz1234", "name": "theirs_db"})
    assert (agent.poll_once(), agent.poll_once(), agent.poll_once()) == (a.pk, b.pk, None)
    assert sorted(agent.machine.databases) == ["first_db", "second_db"]
    assert AgentJob.objects.get(node=other_node).status == JobStatus.QUEUED


def test_an_agent_cannot_report_for_another_nodes_job_or_without_a_token(manager, node, agent):
    other_server = Server.objects.create(name="native-2", hostname="n2.test", kind=ServerKind.MANUAL)
    _, other_token = services.issue_token(other_server)
    job = services.dispatch(manager, node[0].server, "CREATE_DATABASE", {"account": "abcd1234", "name": "first_db"})
    c = Client()
    ok = {"ok": True, "code": "created", "message": "", "data": {}}
    post = lambda token: c.post(f"/api/agent/v1/jobs/{job.pk}/result/", data=ok, content_type="application/json", HTTP_AUTHORIZATION=f"Agent {token}")  # noqa: E731
    assert post("wrong").status_code == 401 and post(other_token).status_code == 404
    assert c.post("/api/agent/v1/poll/", content_type="application/json").status_code == 401


def test_a_result_must_be_structured_and_cannot_be_rewritten(manager, node, agent):
    job = services.dispatch(manager, node[0].server, "CREATE_DATABASE", {"account": "abcd1234", "name": "first_db"})
    c = Client()
    auth = {"HTTP_AUTHORIZATION": "Agent " + agent.token}
    url = f"/api/agent/v1/jobs/{job.pk}/result/"
    assert c.post(url, data={"ok": True, "code": "created"}, content_type="application/json", **auth).status_code == 409  # not leased yet
    agent._post("/api/agent/v1/poll/")
    assert c.post(url, data={"ok": "yes", "code": "x"}, content_type="application/json", **auth).status_code == 400
    assert c.post(url, data="[1]", content_type="application/json", **auth).status_code == 400
    good = {"ok": True, "code": "created", "message": "", "data": {}}
    assert c.post(url, data=good, content_type="application/json", **auth).status_code == 200
    assert c.post(url, data=good, content_type="application/json", **auth).status_code == 200  # a repeated report is harmless
    assert c.post(url, data={**good, "ok": False}, content_type="application/json", **auth).status_code == 409


def test_an_expired_command_is_never_started(manager, node, agent):
    job = services.dispatch(manager, node[0].server, "CREATE_WEBSITE", GOOD)
    AgentJob.objects.filter(pk=job.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert agent.poll_once() is None
    job.refresh_from_db()
    assert job.status == JobStatus.EXPIRED and agent.machine.executed == []


def test_a_lost_idempotent_job_is_handed_out_again_with_a_fresh_nonce(manager, node, agent):
    job = services.dispatch(manager, node[0].server, "CREATE_DATABASE", {"account": "abcd1234", "name": "first_db"})
    services.poll(node[0], "0.1.0")  # leased, and the agent then vanishes
    old_nonce = AgentJob.objects.get(pk=job.pk).nonce
    AgentJob.objects.filter(pk=job.pk).update(leased_until=timezone.now() - timedelta(seconds=1))
    assert agent.poll_once() == job.pk  # the sweep re-queues it and the agent runs it
    job = AgentJob.objects.get(pk=job.pk)
    assert job.status == JobStatus.SUCCEEDED and job.attempts == 2 and job.nonce != old_nonce


def test_a_lost_non_idempotent_job_is_never_run_a_second_time(manager, node, agent):
    job = services.dispatch(manager, node[0].server, "CREATE_BACKUP", {"account": "abcd1234", "scope": "full"})
    services.poll(node[0], "0.1.0")
    AgentJob.objects.filter(pk=job.pk).update(leased_until=timezone.now() - timedelta(seconds=1))
    assert agent.poll_once() is None
    job.refresh_from_db()
    assert job.status == JobStatus.TIMED_OUT and agent.machine.executed == []


def test_an_old_agent_is_told_to_upgrade_and_gets_no_work(manager, node):
    services.dispatch(manager, node[0].server, "CREATE_WEBSITE", GOOD)
    for version in ("0.0.9", "garbage", ""):
        with pytest.raises(ServiceError) as caught:
            services.poll(node[0], version)
        assert caught.value.status_code == 426
    assert AgentJob.objects.get().status == JobStatus.QUEUED


def test_poll_records_the_agent_version_and_last_contact(node):
    services.poll(node[0], "0.2.1")
    node[0].refresh_from_db()
    assert node[0].agent_version == "0.2.1" and node[0].last_seen_at


def test_a_queued_command_can_be_cancelled_but_a_running_one_cannot(manager, node):
    queued = services.dispatch(manager, node[0].server, "CREATE_WEBSITE", GOOD)
    services.cancel(manager, queued)
    assert queued.status == JobStatus.CANCELLED
    running = services.dispatch(manager, node[0].server, "CREATE_DATABASE", {"account": "abcd1234", "name": "first_db"})
    services.poll(node[0], "0.1.0")
    running.refresh_from_db()
    with pytest.raises(ServiceError):
        services.cancel(manager, running)


def test_finished_jobs_do_not_keep_secret_values(manager, node, agent):
    mailbox = {"account": "abcd1234", "domain": "acme.com", "local_part": "info", "quota_mb": 500, "password": "correct horse battery"}
    job = services.dispatch(manager, node[0].server, "CREATE_MAILBOX", mailbox)
    agent.poll_once()
    job.refresh_from_db()
    assert job.status == JobStatus.SUCCEEDED and job.params["password"] == "***"


def test_a_forged_envelope_is_dropped_by_the_agent_and_never_reported(manager, node, agent, monkeypatch):
    services.dispatch(manager, node[0].server, "CREATE_DATABASE", {"account": "abcd1234", "name": "first_db"})
    monkeypatch.setattr(envelope, "_private_key", lambda: __import__("cryptography.hazmat.primitives.asymmetric.ed25519",
                                                                      fromlist=["x"]).Ed25519PrivateKey.generate())
    assert agent.poll_once() is None and agent.machine.executed == []
    assert AgentJob.objects.get().status == JobStatus.LEASED  # nothing was reported; the lease will time out
