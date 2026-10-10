"""The loose ends closed after N10: the node's recurring jobs, every noisy-neighbour scenario's judging and driver, the database probe, and the extra scheduled tasks."""
import datetime
import json

import pytest

from apps.agent import tasks
from apps.agent.node import loadtest, probe
from apps.agent.node.scheduler import DAY, HOUR, Scheduler, standard_jobs
from apps.agent.node.system import FakeSystem
from apps.agent.node.website import Websites
from apps.agent.reference_agent import NativeMachine, ReferenceAgent

pytestmark = pytest.mark.django_db


# --- the node's recurring jobs ---------------------------------------------------------------------------------------------------------------

def test_each_job_runs_when_due_and_not_again_until_it_is_due_again():
    system, clock, calls = FakeSystem(), [1000.0], []
    jobs = [("daily", DAY, lambda: calls.append("daily") or "did it"), ("hourly", HOUR, lambda: calls.append("hourly"))]
    scheduler = Scheduler(system, jobs, now=lambda: clock[0])
    assert scheduler.run_due() == [("daily", "ok: did it"), ("hourly", "ok: ")]
    assert scheduler.run_due() == []
    clock[0] += HOUR + 1
    assert scheduler.run_due() == [("hourly", "ok: ")] and calls == ["daily", "hourly", "hourly"]
    clock[0] += DAY
    assert [n for n, _ in scheduler.run_due()] == ["daily", "hourly"]


def test_a_restart_remembers_when_jobs_last_ran():
    system, calls = FakeSystem(), []
    jobs = [("daily", DAY, lambda: calls.append(1))]
    Scheduler(system, jobs, now=lambda: 5000.0).run_due()
    assert json.loads(system.files["/var/lib/whp/scheduler.json"]) == {"daily": 5000.0}
    assert Scheduler(system, jobs, now=lambda: 5100.0).run_due() == [] and calls == [1]  # a new process, the same memory


def test_a_failing_job_does_not_stop_the_others_and_is_retried_in_an_hour_not_every_loop():
    system, clock = FakeSystem(), [0.0]

    def broken():
        raise RuntimeError("disk gone")

    scheduler = Scheduler(system, [("bad", DAY, broken), ("good", DAY, lambda: "fine")], now=lambda: clock[0])
    done = dict(scheduler.run_due())
    assert done["bad"].startswith("failed: RuntimeError: disk gone") and done["good"] == "ok: fine"
    clock[0] += 60
    assert scheduler.run_due() == []  # not on the very next loop
    clock[0] += HOUR
    assert [n for n, _ in scheduler.run_due()] == ["bad"]


def test_the_agent_runs_the_standard_jobs_against_its_handlers(tmp_path):
    machine = NativeMachine(files_dir=tmp_path)
    machine.system.files["/proc/x"] = ""
    Websites(machine.system).run("CREATE_WEBSITE", {"account": "abcd1234", "domain": "acme.com", "php_version": "8.3"})
    (tmp_path / "abcd1234" / "sites" / "acme.com" / "public_html").mkdir(parents=True)
    clock = datetime.datetime(2026, 10, 10, 12, tzinfo=datetime.timezone.utc).timestamp()
    machine.clock = lambda: clock
    agent = ReferenceAgent.__new__(ReferenceAgent)
    agent.machine = machine
    first = dict(agent.maintenance_once(log_lines=lambda: []))
    assert first["usage"] == "ok: 1 accounts" and first["certificates"].startswith("ok:") and first["mail-abuse"].startswith("ok: blocked []") and "node-config-backup" in first
    assert "/var/lib/whp/usage/abcd1234.json" in machine.system.files  # the history the Usage page draws was really written
    assert agent.maintenance_once(log_lines=lambda: []) == []  # nothing is due a second later
    assert [name for name, _, _ in standard_jobs(machine, lambda: [])] == ["usage", "certificates", "node-config-backup"]


# --- every noisy-neighbour scenario ----------------------------------------------------------------------------------------------------------

LIMITS = {"cpu_percent": 100, "memory_mb": 512, "processes": 50, "disk_mb": 1000, "inodes": 5000, "db_connections": 20}


@pytest.mark.parametrize("scenario,observed,expected", [
    ("cpu_burn", {"cpu_percent_peak": 100}, True), ("cpu_burn", {"cpu_percent_peak": 114}, True), ("cpu_burn", {"cpu_percent_peak": 300}, False), ("cpu_burn", {}, False),
    ("memory_hog", {"memory_killed": True}, True), ("memory_hog", {"memory_mb_peak": 500}, True), ("memory_hog", {"memory_mb_peak": 900}, False),
    ("fork_bomb", {"processes_peak": 50}, True), ("fork_bomb", {"processes_peak": 5000}, False),
    ("disk_fill", {"write_failed": True, "bytes_written_mb": 1000}, True), ("disk_fill", {"write_failed": False, "bytes_written_mb": 1000}, False), ("disk_fill", {"write_failed": True, "bytes_written_mb": 2000}, False),
    ("inode_flood", {"create_failed": True, "files_created": 5000}, True), ("inode_flood", {"create_failed": True, "files_created": 90000}, False), ("inode_flood", {"files_created": 5000}, False),
    ("connection_flood", {"connections_opened": 60, "connections_refused": 40}, True), ("connection_flood", {"connections_opened": 60, "connections_refused": 0}, False),
    ("connection_flood", {"connections_opened": 60, "connections_refused": 10}, False), ("io_flood", {}, True)])
def test_a_noisy_tenant_counts_as_held_only_when_it_really_hit_its_wall(scenario, observed, expected):
    assert loadtest.was_limited(scenario, LIMITS, observed) is expected


def test_every_scenario_has_a_driver_and_a_judge_and_the_drivers_are_valid_programs():
    for name in loadtest.SCENARIOS:
        assert name in loadtest.RealLoad.COMMANDS
        loadtest.was_limited(name, LIMITS, {})
    values = {"account": "abcd1234", "double_disk": 2000, "count": 10, "db_user": "abcd1234_u", "password": "p'w{x}"}
    for name, argv in loadtest.RealLoad.COMMANDS.items():
        formatted = [part.format(**values) for part in argv]
        if formatted[0] == "python3":
            compile(formatted[2], name, "exec")
    with pytest.raises(KeyError):
        loadtest.RealLoad().start("abcd1234", "nonsense")


# --- the database probe ----------------------------------------------------------------------------------------------------------------------------

def test_an_attempt_that_could_not_be_made_is_never_counted_as_a_pass():
    def attempt(attacker, action, **args):
        return None if action == "database" else False

    results = probe.run_probes("evil1234", "vict5678", attempt)
    untested = [r for r in results if not r.passed]
    assert len(untested) == 1 and untested[0].detail == "could not be tested" and not probe.verdict(results)["ok"]


def test_the_database_probe_needs_a_login_and_reads_the_password_from_the_environment(monkeypatch):
    seen = {}

    class Done:
        returncode, stdout = 0, "information_schema\nevil1234_db\nvict5678_secret\n"

    def fake_run(argv, **kw):
        seen["argv"], seen["env"] = argv, kw["env"]
        return Done()

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    assert probe.RealAttempt({"evil1234": (1, 1)}).__call__("evil1234", "database", name_prefix="vict5678_") is None  # no login supplied: not tested
    attempt = probe.RealAttempt({"evil1234": (1, 1)}, db_logins={"evil1234": ("evil1234_user", "s3cret")})
    assert attempt("evil1234", "database", name_prefix="vict5678_") is True  # the attacker's own login can see the victim's database: a failure of isolation
    assert "s3cret" not in " ".join(seen["argv"]) and seen["env"]["MYSQL_PWD"] == "s3cret"
    Done.stdout = "information_schema\nevil1234_db\n"
    assert attempt("evil1234", "database", name_prefix="vict5678_") is False


# --- scheduled tasks ----------------------------------------------------------------------------------------------------------------------------------

def test_the_reputation_and_panel_backup_tasks_exist_and_are_scheduled(tmp_path, settings):
    from config.settings import base

    assert {"native-mail-reputation", "panel-database-backup", "native-monitoring-sweep", "native-scheduled-backups", "native-restore-test"} <= set(base.CELERY_BEAT_SCHEDULE)
    assert tasks.mail_reputation_task() == {}
    settings.PANEL_BACKUP_DIR = str(tmp_path)
    assert tasks.panel_backup_task().endswith(".bak.enc")
