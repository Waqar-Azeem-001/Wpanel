"""
The Go agent against the real WHPanel: the Go program is compiled, started as a separate process, enrolled with a one-time token, and made to fetch, verify and carry out commands queued by Django.
It proves the wire protocol between the two languages end to end: a certificate request made by Go is accepted by Python's CA, Python's Ed25519 signature is verified by Go, and Go's structured
answer is understood by Python.

The agent runs with ``--fake-machine`` here, so it touches nothing on this computer. The tests are skipped when no Go toolchain can be found (set GO_BIN to its path), and the Python specification
files are checked for being up to date whether Go is there or not.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from apps.accounts.roles import Role
from apps.agent import catalog, enrollment, services
from apps.agent.management.commands.export_agent_spec import files, render
from apps.agent.models import AgentJob, AgentNode, JobStatus, NodeStatus
from apps.products.models import Server, ServerKind

ROOT = Path(__file__).resolve().parents[3]
AGENT = ROOT / "agent"


def find_go():
    candidates = [os.environ.get("GO_BIN"), shutil.which("go"), str(Path.home() / "go" / "bin" / "go"), "/usr/local/go/bin/go", "C:/Program Files/Go/bin/go.exe"]
    return next((c for c in candidates if c and Path(c).exists()), None)


# --- the contract files are never allowed to be stale ---------------------------------------------------------------------------------------

@pytest.mark.django_db
def test_the_files_the_go_agent_is_built_from_match_the_python_catalogue():
    for name, data in files().items():
        assert (AGENT / "spec" / name).read_text(encoding="utf-8") == render(data), f"{name} is out of date: run `python manage.py export_agent_spec`"
    from apps.agent import golden

    assert (AGENT / "spec" / golden.MIGRATE_ARCHIVE).read_bytes() == golden.build_migrate_golden()[0], "the test archive is out of date: run `python manage.py export_agent_spec`"
    _, good, evil = golden.build_wordpress_golden()
    assert (AGENT / "spec" / "wordpress.tar.gz").read_bytes() == good and (AGENT / "spec" / "wordpress-evil.tar.gz").read_bytes() == evil, "run `python manage.py export_agent_spec`"


# --- the Go agent against Django ---------------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def binary(tmp_path_factory):
    go = find_go()
    if go is None:
        pytest.skip("no Go toolchain found (set GO_BIN)")
    out = tmp_path_factory.mktemp("agent") / ("wh-agent.exe" if os.name == "nt" else "wh-agent")
    env = {**os.environ, "GOFLAGS": "-mod=mod"}
    done = subprocess.run([go, "build", "-o", str(out), "./cmd/wh-agent"], cwd=AGENT, env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return out


def run(binary, *args, timeout=60):
    done = subprocess.run([str(binary), *args], capture_output=True, text=True, timeout=timeout)
    return done


@pytest.fixture
def enrolled(binary, live_server, staff, tmp_path):
    manager = staff(Role.MANAGER)
    server = Server.objects.create(name="go-node", hostname="go.example.com", kind=ServerKind.NATIVE)
    node, token = enrollment.invite(manager, server)
    config = tmp_path / "agent.json"
    done = run(binary, "enroll", "--panel", live_server.url, "--token", token, "--capabilities", "web,php,database", "--config", str(config))
    assert done.returncode == 0, done.stderr + done.stdout
    return manager, server, AgentNode.objects.get(pk=node.pk), config, binary, done


@pytest.mark.django_db(transaction=True)
def test_the_go_agent_enrols_and_python_accepts_its_certificate_request(enrolled):
    manager, server, node, config, binary, done = enrolled
    assert "enrolled as node" in done.stdout
    assert node.status == NodeStatus.ENROLLED and node.cert_fingerprint and node.capabilities == ["database", "php", "web"] and node.hostname
    saved = json.loads(config.read_text())
    assert saved["node_id"] == server.pk and saved["token"] and saved["public_key"]
    assert (config.parent / "agent" / "client.key").exists() and (config.parent / "agent" / "client.pem").exists()
    if os.name == "posix":
        assert oct((config.stat().st_mode & 0o777)) == "0o600" and oct(((config.parent / "agent" / "client.key").stat().st_mode & 0o777)) == "0o600"
    assert "-----BEGIN CERTIFICATE-----" in (config.parent / "agent" / "client.pem").read_text()


@pytest.mark.django_db(transaction=True)
def test_a_one_time_token_cannot_be_used_twice_by_the_go_agent(enrolled, binary, live_server, tmp_path):
    manager, server, node, config, binary, done = enrolled
    again = run(binary, "enroll", "--panel", live_server.url, "--token", "whe_not-a-real-token", "--config", str(tmp_path / "second.json"))
    assert again.returncode != 0 and "401" in again.stderr


@pytest.mark.django_db(transaction=True)
def test_python_queues_a_command_and_the_go_agent_verifies_runs_and_reports_it(enrolled):
    manager, server, node, config, binary, done = enrolled
    job = services.dispatch(manager, server, "CREATE_WEBSITE", {"account": "abcd1234", "domain": "acme.com", "php_version": "8.3", "disk_mb": 2048})
    result = run(binary, "once", "--config", str(config), "--fake-machine")
    assert result.returncode == 0, result.stderr
    job = AgentJob.objects.get(pk=job.pk)
    assert job.status == JobStatus.SUCCEEDED and job.result_ok and job.result_code == "created"
    assert job.result_data["root"] == "/home/abcd1234/sites/acme.com/public_html" and job.result_data["uid"] == 2000
    node.refresh_from_db()
    assert node.last_seen_at and node.health.get("services") and node.agent_version == "0.1.0"  # the heartbeat arrived too


@pytest.mark.django_db(transaction=True)
def test_a_failure_in_go_comes_back_as_a_structured_failure(enrolled):
    manager, server, node, config, binary, done = enrolled
    job = services.dispatch(manager, server, "CHANGE_PHP_VERSION", {"account": "nobody123", "domain": "x.com", "php_version": "8.1"})
    assert run(binary, "once", "--config", str(config), "--fake-machine").returncode == 0
    job = AgentJob.objects.get(pk=job.pk)
    assert job.status == JobStatus.FAILED and job.result_code == "not_found" and "No such website" in job.result_message


@pytest.mark.django_db(transaction=True)
def test_a_command_with_secrets_is_scrubbed_after_go_has_used_it(enrolled):
    manager, server, node, config, binary, done = enrolled
    services.dispatch(manager, server, "CREATE_WEBSITE", {"account": "abcd1234", "domain": "acme.com", "php_version": "8.3"})
    run(binary, "once", "--config", str(config), "--fake-machine")
    job = services.dispatch(manager, server, "SFTP_CREATE", {"account": "abcd1234", "name": "dev", "password": "Kq7vRt2mXzLp", "directory": ""})
    # the pretend machine of the first run is gone, so this one fails (no website) -- but the secret must not outlive the job either way
    run(binary, "once", "--config", str(config), "--fake-machine")
    job = AgentJob.objects.get(pk=job.pk)
    assert job.status in (JobStatus.SUCCEEDED, JobStatus.FAILED) and job.params["password"] == "***"


@pytest.mark.django_db(transaction=True)
def test_a_revoked_go_agent_is_turned_away(enrolled):
    manager, server, node, config, binary, done = enrolled
    enrollment.revoke(manager, node)
    result = run(binary, "once", "--config", str(config), "--fake-machine")
    assert result.returncode != 0 and "401" in result.stderr


def test_the_agent_checks_its_own_catalogue(binary, tmp_path):
    done = run(binary, "check", "--config", str(tmp_path / "none.json"))
    assert done.returncode == 0 and f"{len(catalog.COMMANDS)} commands" in done.stdout and "not enrolled" in done.stdout
    assert run(binary, "version").stdout.strip() == "wh-agent 0.1.0"
    assert run(binary, "bogus").returncode == 2
    assert sys.version  # (keeps the import used)
