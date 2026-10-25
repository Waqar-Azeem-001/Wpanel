"""The CI file keeps its promises: the deploy waits for the tests, the security scan and an approval, and the notice does not depend on the deploy secret."""
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
CI = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def jobs():
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]


def test_the_deploy_needs_the_tests_and_the_security_scan_and_runs_for_main_only(jobs):
    deploy = jobs["deploy"]
    assert set(deploy["needs"]) == {"test", "security"}
    assert "refs/heads/main" in deploy["if"] and "push" in deploy["if"]


def test_the_deploy_runs_in_the_production_environment_so_a_reviewer_and_the_secret_belong_to_it(jobs):
    assert jobs["deploy"]["environment"]["name"] == "production"


def test_the_notice_does_not_wait_for_the_deploy_and_never_touches_the_deploy_secret(jobs):
    notify = jobs["notify"]
    assert "deploy" not in notify["needs"]
    assert "DEPLOY_SSH_KEY" not in yaml.safe_dump(notify) and "NOTIFY_SSH_KEY" in yaml.safe_dump(notify)


def test_the_test_job_runs_the_postgres_suite_and_the_security_scan_is_its_own_job(jobs):
    steps = [step.get("name", "") for step in jobs["test"]["steps"]]
    assert "Test suite on PostgreSQL" in steps and not any("bandit" in s for s in steps)
    assert any("bandit" in step.get("name", "") for step in jobs["security"]["steps"])
