"""Monitoring and controlled self-healing (N10): what a node reports, the one place states are decided, settling and alerting, and the strict limits on automatic repair."""
import datetime
from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.agent import catalog, enrollment, monitoring, services as agent
from apps.agent.models import AgentJob, AgentNode, BackupRepository, HealingAction, NodeStateChange
from apps.agent.node.metrics import Metrics
from apps.agent.node.servicectl import ServiceControl
from apps.agent.node.system import FakeSystem
from apps.agent.node.website import HandlerError
from apps.agent.reference_agent import NativeMachine
from apps.audit.models import AuditEvent
from apps.hosting.models import NativeBackup
from apps.products.models import ServerStatus

from .test_native_website import make_node, order, world  # noqa: F401

pytestmark = pytest.mark.django_db

PROC_STAT_1 = "cpu  1000 0 500 8000 500 0 0 0 0 0\n"
PROC_STAT_2 = "cpu  1600 0 700 8200 500 0 0 0 0 0\n"  # 800 busy of 1000 more ticks = 80 percent
MEMINFO = "MemTotal:       16384000 kB\nMemFree:         1000000 kB\nMemAvailable:    4096000 kB\n"
NETDEV = ("Inter-|   Receive                                                |  Transmit\n face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
          "    lo: 1000 10 0 0 0 0 0 0 1000 10 0 0 0 0 0 0\n  eth0: {rx} 100 {e1} {e2} 0 0 0 0 {tx} 90 {e3} {e4} 0 0 0 0\n")


def good_system():
    system = FakeSystem()
    system.files.update({"/proc/stat": PROC_STAT_1, "/proc/loadavg": "0.52 0.40 0.30 1/200 999\n", "/proc/meminfo": MEMINFO, "/proc/net/dev": NETDEV.format(rx=5000, tx=7000, e1=0, e2=0, e3=0, e4=0)})
    system.service_state.update({"httpd": "active", "sshd": "active", "mariadb": "active", "postfix": "active", "dovecot": "active", "rspamd": "active"})
    return system


# --- what a node reports -------------------------------------------------------------------------------------------------------------

def test_the_node_reports_its_figures_and_its_services():
    system = good_system()
    metrics = Metrics(system, ["web", "database", "mail"], disk_usage=lambda path: (200 * 1073741824, 50 * 1073741824, 150 * 1073741824))
    system.probes[("postqueue", "-j")] = '{"a":1}\n{"b":2}\n{"c":3}\n'
    first = metrics.collect()
    assert "cpu_percent" not in first and "net_errors" not in first  # these need two readings
    assert first["load_1m"] == 0.52 and first["memory_used_mb"] == 12000 and first["memory_total_mb"] == 16000 and first["disk_total_gb"] == 200 and first["disk_used_gb"] == 50
    assert first["services"] == {"httpd": "up", "sshd": "up", "mariadb": "up", "postfix": "up", "dovecot": "up", "rspamd": "up"} and first["mail_queue"] == 3
    system.files["/proc/stat"] = PROC_STAT_2
    system.files["/proc/net/dev"] = NETDEV.format(rx=9000, tx=9000, e1=2, e2=1, e3=0, e4=3)
    second = metrics.collect()
    assert second["cpu_percent"] == 80.0 and second["net_errors"] == 6


def test_a_service_that_is_stopped_or_starting_is_reported_as_such_and_unreadable_figures_are_left_out():
    system = FakeSystem()  # no /proc files at all
    system.service_state.update({"httpd": "inactive", "sshd": "activating"})
    report = Metrics(system, ["web"], disk_usage=lambda p: (_ for _ in ()).throw(OSError("no disk"))).collect()
    assert report == {"services": {"httpd": "down", "sshd": "degraded"}}  # nothing invented


def test_only_the_services_a_node_should_run_are_asked_about():
    system = good_system()
    assert set(Metrics(system, ["web"]).collect()["services"]) == {"httpd", "sshd"}
    assert "mail_queue" not in Metrics(system, ["web"]).collect()


# --- the one place that decides --------------------------------------------------------------------------------------------------------

@pytest.fixture
def node(world):  # noqa: F811
    manager, client, product = world
    server, ref = make_node(manager, "web-1")
    node = AgentNode.objects.get(server=server)
    BackupRepository.objects.create(node=node, endpoint="https://s3.example.net", bucket="bkt", prefix="n", applied_ok=True)
    mail.outbox.clear()  # mail from setting the world up is not what these tests are about
    return node, ref


def beat(node, **figures):
    enrollment.heartbeat(node, {"services": {"httpd": "up"}, **figures})
    node.refresh_from_db()
    return monitoring.evaluate(node)


def test_a_quiet_node_is_healthy_and_a_silent_one_is_offline(node):
    node, ref = node
    assert beat(node)["state"] == "healthy"
    later = timezone.now() + timedelta(minutes=10)
    result = monitoring.evaluate(node, later)
    assert result["state"] == "offline" and "last heard" in result["checks"][0]["detail"]


@pytest.mark.parametrize("figures,expected", [({"cpu_percent": 50}, "healthy"), ({"cpu_percent": 91}, "warning"), ({"cpu_percent": 99}, "critical"),
                                              ({"memory_used_mb": 7500, "memory_total_mb": 8000}, "warning"), ({"memory_used_mb": 7900, "memory_total_mb": 8000}, "critical"),
                                              ({"disk_used_gb": 90, "disk_total_gb": 100}, "warning"), ({"disk_used_gb": 96, "disk_total_gb": 100}, "critical"),
                                              ({"load_1m": 12}, "warning"), ({"load_1m": 40}, "critical"),
                                              ({"mail_queue": 300}, "warning"), ({"mail_queue": 5000}, "critical"), ({"certs_min_days": 10}, "warning"), ({"certs_min_days": 2}, "critical"),
                                              ({"certs_min_days": -1}, "critical"), ({"certs_min_days": 40}, "healthy"), ({"net_errors": 500}, "warning")])
def test_each_figure_has_its_warning_and_critical_level(node, figures, expected):
    node, ref = node
    AgentNode.objects.filter(pk=node.pk).update(cpu_cores=8)
    node.refresh_from_db()
    assert beat(node, **figures)["state"] == expected


def test_services_decide_warning_and_critical_and_the_worst_check_wins(node):
    node, ref = node
    enrollment.heartbeat(node, {"services": {"httpd": "up", "mariadb": "degraded"}, "cpu_percent": 10})
    node.refresh_from_db()
    assert monitoring.state_of(node) == "warning"
    enrollment.heartbeat(node, {"services": {"httpd": "down", "mariadb": "degraded"}})
    node.refresh_from_db()
    result = monitoring.evaluate(node)
    assert result["state"] == "critical" and {c["name"] for c in result["checks"] if c["state"] == "critical"} == {"service httpd"}


def test_a_figure_the_node_did_not_report_is_unknown_not_healthy_or_broken(node):
    node, ref = node
    names = {c["name"] for c in beat(node)["checks"]}
    assert names == {"agent contact", "service httpd"}  # no CPU, memory, disk or certificate lines at all


def test_backups_the_queue_and_reputation_are_part_of_the_verdict(node, world):  # noqa: F811
    node, ref = node
    manager, client, product = world
    BackupRepository.objects.filter(node=node).update(applied_ok=False)
    node.refresh_from_db()
    assert any(c["name"] == "backup storage" and c["state"] == "warning" for c in beat(node)["checks"])
    BackupRepository.objects.filter(node=node).update(applied_ok=True)
    node.refresh_from_db()
    from apps.hosting import services as hosting

    account = order(manager, client, product, node.server)
    NativeBackup.objects.create(account=account, node=node, group="g1", part="web", kind="files", requested_kind="files", status="failed")
    assert any(c["name"] == "backups" and "failed" in c["detail"] for c in beat(node)["checks"])
    NativeBackup.objects.create(account=account, node=node, group="g2", part="web", kind="files", requested_kind="files", status="complete", restore_tested_at=timezone.now(), restore_test_ok=False)
    assert any(c["name"] == "restore test" and c["state"] == "critical" for c in beat(node)["checks"])
    AgentNode.objects.filter(pk=node.pk).update(reputation={"ok": False, "listed": {"198.51.100.1": ["zen.spamhaus.org"]}}, reputation_at=timezone.now())
    node.refresh_from_db()
    assert any(c["name"] == "mail reputation" and c["state"] == "critical" for c in monitoring.evaluate(node)["checks"])
    AgentJob.objects.filter(node=node, status="queued").update(created_at=timezone.now() - timedelta(minutes=40))
    assert any(c["name"] == "waiting commands" and c["state"] == "critical" for c in monitoring.evaluate(node)["checks"])
    _ = hosting


# --- settling, history and alerts --------------------------------------------------------------------------------------------------------------

def test_a_change_must_be_seen_twice_before_it_is_settled_and_announced(node, settings):
    node, ref = node
    settings.MONITORING_ALERT_EMAILS = "ops@example.com, boss@example.com"
    beat(node, cpu_percent=99)
    assert monitoring.sweep() == {"web-1": "healthy"} and not mail.outbox  # once: a blip
    beat(node, cpu_percent=10)
    monitoring.sweep()
    beat(node, cpu_percent=99)
    monitoring.sweep()
    assert not mail.outbox and NodeStateChange.objects.count() == 0  # the blip healed itself in between, so the count started again
    monitoring.sweep()
    assert monitoring.sweep() == {"web-1": "critical"}
    node.refresh_from_db()
    assert node.monitor_state == "critical" and NodeStateChange.objects.get().reasons == ["CPU: 99%"]
    assert len(mail.outbox) == 1 and mail.outbox[0].subject == "[WHPanel] web-1 is CRITICAL" and mail.outbox[0].to == ["ops@example.com", "boss@example.com"] and "CPU: 99%" in mail.outbox[0].body
    assert AuditEvent.objects.filter(action="agent.node_state_changed").count() == 1


def test_recovery_and_reminders(node, settings):
    node, ref = node
    settings.MONITORING_ALERT_EMAILS = "ops@example.com"
    beat(node, cpu_percent=99)
    monitoring.sweep()
    monitoring.sweep()
    assert len(mail.outbox) == 1
    def at(hours):
        moment = timezone.now() + timedelta(hours=hours)
        AgentNode.objects.filter(pk=node.pk).update(last_seen_at=moment)  # still reporting at that moment
        return moment

    monitoring.sweep(at(1))
    assert len(mail.outbox) == 1  # no nagging within six hours
    monitoring.sweep(at(7))
    assert len(mail.outbox) == 2 and "still CRITICAL" in mail.outbox[1].subject
    beat(node, cpu_percent=5)
    monitoring.sweep()
    monitoring.sweep()
    assert mail.outbox[-1].subject == "[WHPanel] web-1 has recovered" and NodeStateChange.objects.count() == 2


def test_no_recipients_means_no_mail_but_the_state_is_still_recorded_and_a_mail_failure_does_not_stop_monitoring(node, settings, monkeypatch):
    node, ref = node
    settings.MONITORING_ALERT_EMAILS = ""
    beat(node, cpu_percent=99)
    monitoring.sweep()
    monitoring.sweep()
    assert not mail.outbox and NodeStateChange.objects.count() == 1
    settings.MONITORING_ALERT_EMAILS = "ops@example.com"
    monkeypatch.setattr(monitoring, "send_mail", lambda *a, **k: (_ for _ in ()).throw(OSError("smtp down")))
    beat(node, cpu_percent=5)
    monitoring.sweep()
    monitoring.sweep()
    assert AuditEvent.objects.filter(action="agent.monitoring_alert_failed").exists() and AgentNode.objects.get(pk=node.pk).monitor_state == "healthy"


def test_the_platform_checks_report_dns_and_the_command_queue(node, monkeypatch):
    from apps.hosting.adapters import native_dns
    from apps.hosting.adapters.powerdns import FakePowerDns

    fake = FakePowerDns()
    monkeypatch.setattr(native_dns, "get_dns", lambda: fake)
    assert {c["name"]: c["state"] for c in monitoring.platform_checks()} == {"DNS servers": "healthy", "agent commands": "healthy"}
    fake.down = True
    assert next(c for c in monitoring.platform_checks() if c["name"] == "DNS servers")["state"] == "critical"


# --- the repair --------------------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def broken(node, settings):
    node, ref = node
    settings.SELF_HEALING_ENABLED = True
    ref.machine = NativeMachine()
    ref.machine.system.service_state["httpd"] = "inactive"
    enrollment.heartbeat(node, {"services": {"httpd": "down", "sshd": "up"}})
    node.refresh_from_db()
    node.monitor_state = "critical"
    node.save()
    return node, ref


def settle(node, now=None):
    now = now or timezone.now()
    AgentNode.objects.filter(pk=node.pk).update(last_seen_at=now)  # the node is still reporting at that moment (otherwise it would be offline, not critical)
    node.refresh_from_db()
    before = set(HealingAction.objects.values_list("pk", flat=True))
    monitoring.heal(node, monitoring.evaluate(node, now), now)
    HealingAction.objects.exclude(pk__in=before).update(started_at=now)  # the test lives in simulated time: the record says when, in that time, it happened


def test_a_down_service_is_restarted_and_the_outcome_recorded(broken):
    node, ref = broken
    settle(node)
    action = HealingAction.objects.get()
    assert (action.service, action.status) == ("httpd", "started")
    ref.machine.system.service_state["httpd"] = "active"  # the restart brings it back
    ref.poll_once()
    action.refresh_from_db()
    assert action.status == "ok" and ["httpd", "-t"] in ref.machine.system.commands and ["systemctl", "restart", "httpd"] in ref.machine.system.commands
    assert ref.machine.system.commands.index(["httpd", "-t"]) < ref.machine.system.commands.index(["systemctl", "restart", "httpd"])  # the configuration is tested first
    assert AuditEvent.objects.filter(action="agent.healing_succeeded").exists()


def test_self_healing_does_nothing_unless_switched_on(broken, settings):
    node, ref = broken
    settings.SELF_HEALING_ENABLED = False
    monitoring.sweep()
    monitoring.sweep()
    assert HealingAction.objects.count() == 0 and not AgentJob.objects.filter(command="SERVICE_RESTART").exists()


def test_it_waits_for_a_settled_critical_state_and_leaves_struggling_services_alone(broken):
    node, ref = broken
    node.monitor_state = "healthy"
    node.save()
    monitoring.sweep()  # not settled yet
    assert HealingAction.objects.count() == 0
    enrollment.heartbeat(node, {"services": {"httpd": "degraded"}})
    node.refresh_from_db()
    node.monitor_state = "warning"
    node.save()
    monitoring.sweep()
    assert HealingAction.objects.count() == 0


def test_it_only_ever_restarts_services_on_the_written_list(broken):
    node, ref = broken
    enrollment.heartbeat(node, {"services": {"customer-app": "down", "php-fpm@evil": "down", "web": "down", "httpd": "up"}})
    node.refresh_from_db()
    settle(node)
    assert HealingAction.objects.count() == 0
    assert set(catalog.HEAL_SERVICES) == {"httpd", "mariadb", "postfix", "dovecot", "rspamd", "sshd"}
    with pytest.raises(catalog.InvalidCommand):
        catalog.validate("SERVICE_RESTART", {"service": "sshd; reboot"})
    with pytest.raises(catalog.InvalidCommand):
        catalog.validate("SERVICE_RESTART", {"service": "firewalld"})


def test_it_never_acts_on_a_node_in_maintenance_or_while_a_command_is_running(broken):
    node, ref = broken
    node.server.status = ServerStatus.MAINTENANCE
    node.server.save()
    settle(node)
    assert HealingAction.objects.count() == 0
    node.server.status = ServerStatus.ACTIVE
    node.server.save()
    busy = agent.dispatch(None, node.server, "SERVICE_RESTART", {"service": "sshd"})
    AgentJob.objects.filter(pk=busy.pk).update(status="leased")
    settle(node)
    assert HealingAction.objects.count() == 0


def test_restarts_are_spaced_limited_and_then_it_gives_up_and_asks_for_a_person(broken, settings):
    node, ref = broken
    settings.MONITORING_ALERT_EMAILS = "ops@example.com"
    start = timezone.now()
    for minute in (0, 1):
        settle(node, start + timedelta(minutes=minute * 6 if minute else 0))
        AgentJob.objects.filter(command="SERVICE_RESTART", status="queued").update(status="failed")  # each attempt leaves it down
    assert HealingAction.objects.filter(status="started").count() == 2
    settle(node, start + timedelta(minutes=7))  # less than five minutes after the last: too soon
    assert HealingAction.objects.count() == 2
    settle(node, start + timedelta(minutes=12))
    AgentJob.objects.filter(command="SERVICE_RESTART", status="queued").update(status="failed")
    assert HealingAction.objects.filter(status="started").count() == 3
    settle(node, start + timedelta(minutes=20))  # a fourth in the hour: never
    gave_up = HealingAction.objects.filter(status="gave_up")
    assert gave_up.count() == 1 and HealingAction.objects.filter(status="started").count() == 3
    settle(node, start + timedelta(minutes=25))
    assert HealingAction.objects.filter(status="gave_up").count() == 1  # said once
    assert AuditEvent.objects.filter(action="agent.healing_gave_up").count() == 1 and any("a person is needed" in m.body for m in mail.outbox)


def test_a_broken_web_server_configuration_is_never_restarted(broken):
    node, ref = broken
    ref.machine.system.fail_on = len(ref.machine.system.commands)  # the very next command, the configuration test, fails
    settle(node)
    ref.poll_once()
    action = HealingAction.objects.get()
    assert action.status == "failed" and "does not pass its own test" in action.detail
    assert ["systemctl", "restart", "httpd"] not in ref.machine.system.commands


def test_a_service_that_does_not_come_back_counts_as_a_failed_attempt():
    system = good_system()
    system.service_state["mariadb"] = "inactive"
    with pytest.raises(HandlerError) as caught:
        ServiceControl(system, sleep=lambda s: None).run("SERVICE_RESTART", {"service": "mariadb"})
    assert caught.value.code == "still_down" and system.commands.count(["systemctl", "restart", "mariadb"]) == 1
    system.service_state["mariadb"] = "active"
    assert ServiceControl(system, sleep=lambda s: None).run("SERVICE_RESTART", {"service": "mariadb"}) == ("restarted", {"service": "mariadb"})


def test_the_whole_loop_from_a_real_heartbeat_to_a_restart(node, settings):
    node, ref = node
    settings.SELF_HEALING_ENABLED = True
    ref.machine = NativeMachine()
    ref.machine.system.files.update({"/proc/loadavg": "0.1 0.1 0.1 1/1 1\n"})
    ref.machine.system.service_state.update({"httpd": "inactive", "sshd": "active"})
    ref.heartbeat_once()
    for _ in range(3):
        monitoring.sweep()
        ref.poll_once()
        ref.machine.system.service_state["httpd"] = "active" if HealingAction.objects.filter(status="ok").exists() else ref.machine.system.service_state["httpd"]
    node.refresh_from_db()
    assert node.monitor_state == "critical" and HealingAction.objects.filter(service="httpd").exists()


# --- screens --------------------------------------------------------------------------------------------------------------------------------------

def test_the_monitoring_screen_shows_states_reasons_history_and_the_self_healing_rules(node, client, world, settings):
    node, ref = node

    settings.SELF_HEALING_ENABLED = False
    settings.MONITORING_ALERT_EMAILS = ""
    enrollment.heartbeat(node, {"services": {"httpd": "down"}, "cpu_percent": 50})
    monitoring.sweep()
    monitoring.sweep()
    client.force_login(world[0])
    html = client.get(reverse("agent_staff:monitoring")).content.decode()
    for text in ("Monitoring", "web-1", "Critical", "service httpd", "set MONITORING_ALERT_EMAILS", "Self-healing is <strong>OFF", "httpd, mariadb, postfix, dovecot, rspamd, sshd", "Healthy &rarr; Critical", "DNS servers"):
        assert text in html, text
    assert "Monitoring" in client.get(reverse("agent_staff:nodes")).content.decode()


def test_the_node_page_uses_the_same_states(node, client, world):
    node, ref = node
    enrollment.heartbeat(node, {"services": {"httpd": "down"}})
    client.force_login(world[0])
    assert "Critical" in client.get(reverse("agent_staff:nodes")).content.decode()
    _ = (datetime, mail)
