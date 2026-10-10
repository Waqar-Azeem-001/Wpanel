"""Resource isolation and limits (N9): hardened per-account services, quotas incl. files, measured usage and live cgroup figures, the isolation check, the cross-tenant probe and the load-test judge,
and the warnings and bandwidth action in WHPanel."""
import datetime
import json
import os
from datetime import timedelta

import pytest
from django.core import mail as django_mail
from django.urls import reverse
from django.utils import timezone

from apps.agent import catalog, services as agent
from apps.agent.node import loadtest, probe, render
from apps.agent.node.system import FakeSystem, SystemError_, check_path
from apps.agent.node.usage import measure_tree, parse_access_log
from apps.agent.node.website import Websites
from apps.agent.reference_agent import NativeMachine
from apps.audit.models import AuditEvent
from apps.hosting import control, limits, services as hosting
from apps.hosting.models import HostingStatus

from .test_native_website import make_node, order, world  # noqa: F401

pytestmark = pytest.mark.django_db
SITE = {"account": "abcd1234", "domain": "acme.com", "php_version": "8.3"}


def dt(y, m, d, h=0, mi=0, sec=0):
    return datetime.datetime(y, m, d, h, mi, sec, tzinfo=datetime.timezone.utc)


def at(*args):
    return dt(*args).timestamp()


# --- hardened services and quotas -----------------------------------------------------------------------------------------------------------

def test_every_php_service_runs_as_its_own_user_with_a_hardened_sandbox():
    system = FakeSystem()
    Websites(system).run("CREATE_WEBSITE", SITE)
    unit = system.files["/etc/systemd/system/whp-php-abcd1234.service"].splitlines()
    for line in render.UNIT_HARDENING + ("User=abcd1234", "Group=abcd1234", "BindPaths=/home/abcd1234", "ReadWritePaths=/home/abcd1234", "RuntimeDirectory=whp-php/abcd1234"):
        assert line in unit, line
    pool = system.files["/etc/whp/php/abcd1234.conf"]
    assert "user =" not in pool and "group =" not in pool and "listen.acl_users = apache" in pool and "/run/whp-php/abcd1234/php.sock" in pool  # nothing in the service is ever root


def test_the_slice_carries_cpu_memory_process_and_io_limits_with_accounting():
    system = FakeSystem()
    Websites(system).run("CREATE_WEBSITE", {**SITE, "memory_mb": 512, "cpu_percent": 50, "processes": 40, "io_weight": 20})
    text = system.files["/etc/systemd/system/whp-abcd1234.slice"]
    for line in ("MemoryMax=512M", "MemoryHigh=460M", "MemorySwapMax=0", "CPUQuota=50%", "TasksMax=40", "IOWeight=20", "CPUAccounting=yes", "MemoryAccounting=yes", "TasksAccounting=yes", "IOAccounting=yes"):
        assert line in text.splitlines(), line


def test_the_quota_limits_files_as_well_as_space():
    system = FakeSystem()
    Websites(system).run("CREATE_WEBSITE", {**SITE, "disk_mb": 1000, "inodes": 100000})
    assert ["xfs_quota", "-x", "-c", "limit -p bsoft=900m bhard=1000m isoft=90000 ihard=100000 2000", "/home"] in system.commands
    Websites(system).run("SET_LIMITS", {"account": "abcd1234", "disk_mb": 2000, "memory_mb": 512, "processes": 40, "cpu_percent": 50, "inodes": 5000})
    assert ["xfs_quota", "-x", "-c", "limit -p bsoft=1800m bhard=2000m isoft=4500 ihard=5000 2000", "/home"] in system.commands


def test_the_folders_keep_other_tenants_out_but_let_the_web_server_in():
    system = FakeSystem()
    Websites(system).run("CREATE_WEBSITE", SITE)
    made = {c[1]: c[2:] for c in system.commands if c[0] == "mkdir"}
    assert made["/home/abcd1234"] == ["0o710", "root", "apache"]  # root's (for the SFTP chroot); only the web server's group may pass
    assert made["/home/abcd1234/sites/acme.com/public_html"] == ["0o2750", "abcd1234", "apache"]  # setgid: what PHP creates stays readable by the web server only
    assert made["/home/abcd1234/tmp"] == ["0o700", "abcd1234"] and made["/home/abcd1234/logs"] == ["0o750", "abcd1234"]
    assert ["write", "/home/abcd1234/sites/acme.com/public_html/index.html", "0o640", "abcd1234", "apache"] in system.commands  # nobody else can read it


def test_a_database_user_may_hold_only_a_few_connections():
    from apps.agent.node.databases import DB_CONNECTIONS, Databases
    from apps.agent.node.mariadb import FakeMariadb

    system, engine = FakeSystem(), FakeMariadb()
    Websites(system).run("CREATE_WEBSITE", SITE)
    Databases(system, engine).run("CREATE_DB_USER", {"account": "abcd1234", "name": "abcd1234_bob", "password": "Kq7vRt2mXzLp"})
    assert engine.max_connections["abcd1234_bob"] == DB_CONNECTIONS == 20


def test_the_agent_may_read_proc_and_cgroups_but_never_write_them():
    check_path("/proc/mounts", read_only=True)
    check_path("/sys/fs/cgroup/cgroup.controllers", read_only=True)
    for path in ("/proc/mounts", "/sys/fs/cgroup/x"):
        with pytest.raises(SystemError_):
            check_path(path)
    with pytest.raises(SystemError_):
        FakeSystem().write("/proc/sys/kernel/x", "1")


# --- measuring ------------------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def measured(world, tmp_path):  # noqa: F811
    manager, client, product = world
    product.resource_limits = {"disk_mb": 100, "bandwidth_mb": 50, "inodes": 5000, "io_weight": 30, "databases": 2, "memory_mb": 256, "processes": 50, "cpu_percent": 80}
    product.save()
    server, ref = make_node(manager, "web-1")
    ref.machine = NativeMachine(files_dir=tmp_path / "web", cgroup_dir=tmp_path / "cgroup")
    account = order(manager, client, product, server)
    ref.poll_once()
    account.refresh_from_db()
    root = tmp_path / "web" / account.username / "sites"
    docroot = root / "acme.com" / "public_html"
    docroot.mkdir(parents=True)
    (docroot / "a.bin").write_bytes(b"x" * 2_097_152)  # 2 MB
    (docroot / "b.txt").write_text("hello")
    (tmp_path / "web" / account.username / "access.log").write_text(
        '1.2.3.4 - - [09/Oct/2026:10:00:00 +0000] "GET / HTTP/1.1" 200 1048576 "-" "x"\n1.2.3.4 - - [10/Oct/2026:10:00:00 +0000] "GET /a HTTP/1.1" 200 2097152 "-" "x"\n'
        '1.2.3.4 - - [10/Oct/2026:11:00:00 +0000] "GET /b HTTP/1.1" 304 - "-" "x"\nnot a log line\n')
    ref.machine.clock = lambda: at(2026, 10, 10, 12, 0, 0)
    agent.inline_agent = ref.poll_once
    yield manager, account, ref, client.contacts.get().user, tmp_path, product
    agent.inline_agent = None


def test_folder_sizes_count_files_and_never_follow_links(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f").write_bytes(b"12345")
    outside = tmp_path.parent / "outside_big.bin"
    outside.write_bytes(b"x" * 10_000)
    try:
        os.symlink(outside, tmp_path / "link")
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot make symbolic links")
    size, inodes = measure_tree(tmp_path)
    assert size == 5 and inodes == 3  # the folder, the file and the link itself; not what the link points to


def test_the_access_log_gives_bytes_sent_per_day():
    lines = ['1.1.1.1 - - [09/Oct/2026:10:00:00 +0000] "GET / HTTP/1.1" 200 100 "-" "-"', '1.1.1.1 - - [09/Oct/2026:10:00:01 +0000] "GET / HTTP/1.1" 200 50 "-" "-"',
             '1.1.1.1 - - [10/Oct/2026:10:00:00 +0000] "GET / HTTP/1.1" 304 - "-" "-"', "rubbish", '1.1.1.1 - - [10/Xxx/2026:10:00:00 +0000] "GET / HTTP/1.1" 200 5 "-" "-"']
    assert parse_access_log(lines) == {"2026-10-09": 150, "2026-10-10": 0}


def test_the_usage_report_gives_use_limits_history_and_bandwidth(measured):
    manager, account, ref, owner, tmp, product = measured
    data = agent.call(account.server, "USAGE_REPORT", {"account": account.username, "days": 7})
    assert data["now"]["disk_mb"] == pytest.approx(2.0, abs=0.1) and data["now"]["inodes"] >= 4 and data["now"]["bandwidth_month_mb"] == 3.0 and data["now"]["bandwidth_today_mb"] == 2.0
    assert data["limits"]["disk_mb"] == 100 and data["limits"]["inodes"] == 5000 and data["limits"]["io_weight"] == 30
    assert [r["day"] for r in data["history"]] == ["2026-10-09", "2026-10-10"] and data["history"][0]["bandwidth_mb"] == 1
    assert data["live"] is None  # no cgroup yet


def test_a_daily_record_builds_the_history(measured):
    manager, account, ref, owner, tmp, product = measured
    assert ref.machine.usage.record_day([account.username, "nobody123"]) == [account.username]
    stored = json.loads(ref.machine.system.files[f"/var/lib/whp/usage/{account.username}.json"])
    assert stored["2026-10-10"]["disk_mb"] == pytest.approx(2.0, abs=0.1) and stored["2026-10-09"]["bandwidth_mb"] == 1.0


def test_live_figures_come_from_the_cgroup_and_cpu_is_a_rate(measured):
    manager, account, ref, owner, tmp, product = measured
    group = tmp / "cgroup" / f"whp-{account.username}.slice"
    group.mkdir(parents=True)
    (group / "memory.current").write_text(str(100 * 1048576))
    (group / "memory.max").write_text(str(256 * 1048576))
    (group / "pids.current").write_text("12")
    (group / "pids.max").write_text("50")
    (group / "cpu.stat").write_text("usage_usec 1000000\nuser_usec 900000\n")
    (group / "io.stat").write_text("8:0 rbytes=1048576 wbytes=1048576 rios=1 wios=1\n")
    first = agent.call(account.server, "USAGE_REPORT", {"account": account.username, "days": 1})["live"]
    assert first["memory_mb"] == 100 and first["memory_limit_mb"] == 256 and first["processes"] == 12 and first["processes_limit"] == 50 and first["cpu_percent"] is None
    ref.machine.clock = lambda: at(2026, 10, 10, 12, 0, 10)
    (group / "cpu.stat").write_text("usage_usec 6000000\n")  # 5 CPU seconds in 10 seconds
    (group / "io.stat").write_text("8:0 rbytes=3145728 wbytes=1048576\n")
    second = agent.call(account.server, "USAGE_REPORT", {"account": account.username, "days": 1})["live"]
    assert second["cpu_percent"] == 50.0 and second["io_kb_s"] == 204.8


def test_the_customers_usage_screens_work_through_the_native_provider(measured, client):
    manager, account, ref, owner, tmp, product = measured
    history = control.perform(owner, account, "insights", "usage_history", 30)
    assert [(p.day, p.bandwidth_mb) for p in history] == [("2026-10-09", 1), ("2026-10-10", 2)]
    group = tmp / "cgroup" / f"whp-{account.username}.slice"
    group.mkdir(parents=True)
    for name, value in (("memory.current", str(64 * 1048576)), ("memory.max", "max"), ("pids.current", "3"), ("pids.max", "50"), ("cpu.stat", "usage_usec 100\n")):
        (group / name).write_text(value)
    ref.machine.clock = lambda: at(2026, 10, 10, 12, 0, 5)
    agent.call(account.server, "USAGE_REPORT", {"account": account.username, "days": 1})
    stats = {s.key: s for s in control.perform(owner, account, "insights", "performance_stats")}
    assert stats["memory"].used == 64 and stats["memory"].limit == 256 and stats["processes"].limit == 50 and "io" not in stats
    client.force_login(owner)
    assert client.get(reverse("hosting_customer:usage", args=[account.pk])).status_code == 200


def test_the_plan_numbers_reach_the_node(measured):
    manager, account, ref, owner, tmp, product = measured
    from apps.agent.models import AgentJob

    params = AgentJob.objects.get(command="CREATE_WEBSITE").params
    assert (params["inodes"], params["io_weight"], params["disk_mb"], params["memory_mb"], params["processes"], params["cpu_percent"]) == (5000, 30, 100, 256, 50, 80)


# --- the isolation check --------------------------------------------------------------------------------------------------------------------

GOOD = {"/sys/fs/cgroup/cgroup.controllers": "cpuset cpu io memory hugetlb pids rdma misc", "/proc/mounts": "proc /proc proc rw,nosuid,hidepid=2 0 0\n/dev/sdb1 /home xfs rw,prjquota 0 0\n",
        "/proc/sys/fs/protected_symlinks": "1", "/proc/sys/fs/protected_hardlinks": "1", "/proc/sys/kernel/yama/ptrace_scope": "1"}


@pytest.fixture
def checked():
    system = FakeSystem()
    system.files.update(GOOD)
    Websites(system).run("CREATE_WEBSITE", SITE)
    from apps.agent.node.isolation import Isolation

    return system, Isolation(system, Websites(system))


def test_a_correct_node_and_account_pass(checked):
    system, iso = checked
    code, data = iso.run("ISOLATION_CHECK", {})
    assert code == "checked" and data["ok"] and data["accounts"] == {} and all(c["ok"] for c in data["baseline"])


@pytest.mark.parametrize("path,value,name", [("/proc/mounts", "proc /proc proc rw 0 0\n/dev/sdb1 /home xfs rw,prjquota 0 0\n", "hidepid"), ("/proc/mounts", "proc /proc proc rw,hidepid=2 0 0\n/dev/sdb1 /home ext4 rw 0 0\n", "XFS"),
                                             ("/sys/fs/cgroup/cgroup.controllers", "cpu memory", "cgroup"), ("/proc/sys/fs/protected_symlinks", "0", "symlink"),
                                             ("/proc/sys/kernel/yama/ptrace_scope", "0", "ptrace")])
def test_each_missing_protection_is_reported(checked, path, value, name):
    system, iso = checked
    system.files[path] = value
    data = iso.run("ISOLATION_CHECK", {})[1]
    assert not data["ok"] and any(name in c["name"] and not c["ok"] for c in data["baseline"])


def test_a_file_that_cannot_be_read_is_a_failure_not_a_pass(checked):
    system, iso = checked
    del system.files["/proc/sys/fs/protected_hardlinks"]
    assert any("hardlink" in c["name"] and not c["ok"] and "None" in c["detail"] for c in iso.run("ISOLATION_CHECK", {})[1]["baseline"])


def test_an_account_that_drifted_from_its_plan_or_lost_its_hardening_is_found(checked):
    system, iso = checked
    unit_path = "/etc/systemd/system/whp-php-abcd1234.service"
    system.files[unit_path] = system.files[unit_path].replace("ProtectHome=tmpfs\n", "").replace("User=abcd1234\n", "")
    system.files["/etc/systemd/system/whp-abcd1234.slice"] = system.files["/etc/systemd/system/whp-abcd1234.slice"].replace("MemoryMax=1024M", "MemoryMax=99999M")
    del system.files["/etc/httpd/conf.d/whp/abcd1234-acme.com.conf"]
    issues = iso.run("ISOLATION_CHECK", {})[1]["accounts"]["abcd1234"]
    assert any("ProtectHome=tmpfs" in i for i in issues) and any("own user" in i for i in issues) and any("resource limits" in i for i in issues) and any("web server settings are missing" in i for i in issues)


def test_the_staff_isolation_page_shows_the_nodes_report(measured, client):
    manager, account, ref, owner, tmp, product = measured
    ref.machine.system.files.update(GOOD)
    client.force_login(manager)
    html = client.get(reverse("agent_staff:isolation", args=[account.server.agent.pk])).content.decode()
    assert "hidepid" in html and "necessary but not enough" in html and "No problems found" in html and "Every account matches" in html


# --- the cross-tenant probe and the load test ---------------------------------------------------------------------------------------------

def test_the_probe_passes_only_when_every_attempt_fails():
    results = probe.run_probes("evil1234", "vict5678", lambda who, action, **a: False)
    assert probe.verdict(results) == {"ok": True, "failed": [], "checked": len(results)} and len(results) >= 12
    names = [r.name for r in results]
    assert any("password hashes" in n for n in names) and any("PHP socket" in n for n in names) and any("processes" in n for n in names) and any("backup keys" in n for n in names)


def test_one_successful_attack_fails_the_node_and_says_which():
    def attempt(who, action, **args):
        return action == "list" and args["path"].endswith("/sites")

    verdict = probe.verdict(probe.run_probes("evil1234", "vict5678", attempt))
    assert not verdict["ok"] and verdict["failed"] == ["list the victim's website folder: the attacker got in"]


def test_an_attempt_that_blows_up_counts_as_refused_and_signals_are_probed_when_a_process_is_known():
    def attempt(who, action, **args):
        raise PermissionError("denied")

    assert probe.verdict(probe.run_probes("a", "b", attempt))["ok"]
    assert any("signal" in r.name for r in probe.run_probes("a", "b", lambda *a, **k: False, victim_pid=4242))


def test_the_load_test_fails_a_node_where_the_quiet_tenant_suffers():
    ok = loadtest.judge("cpu_burn", 100, 120, 0, True)
    assert ok["ok"]
    bad = loadtest.judge("cpu_burn", 100, 400, 3, False)
    assert not bad["ok"] and len(bad["problems"]) == 3
    assert not loadtest.judge("memory_hog", 0, 10, 0, True)["ok"]  # an unmeasurable baseline proves nothing
    assert loadtest.judge("x", 100, 150, 0, True)["ok"] and not loadtest.judge("x", 100, 151, 0, True)["ok"]


def test_every_scenario_is_run_and_the_load_is_always_stopped():
    started, stopped = [], []
    state = {"loaded": False}

    def measure():
        return (300, 2) if state["loaded"] else (100, 0)

    def start(name):
        started.append(name)
        state["loaded"] = True

    def stop(name):
        stopped.append(name)
        state["loaded"] = False

    results = loadtest.run_all(measure, start, stop, lambda name: True)
    assert started == stopped == list(loadtest.SCENARIOS) and all(not r["ok"] for r in results)
    with pytest.raises(RuntimeError):
        loadtest.run_all(measure, start, stop, lambda name: (_ for _ in ()).throw(RuntimeError("boom")), scenarios=["cpu_burn"])
    assert stopped[-1] == "cpu_burn" and state["loaded"] is False


# --- warnings and bandwidth in WHPanel -------------------------------------------------------------------------------------------------------

@pytest.fixture
def usage_account(measured, settings, mailoutbox):
    manager, account, ref, owner, tmp, product = measured
    account.disk_limit_mb, account.bandwidth_limit_mb = 100, 50
    account.disk_used_mb, account.bandwidth_used_mb = 10, 10
    account.save()
    return account, mailoutbox, ref


def note(account, **fields):
    for k, v in fields.items():
        setattr(account, k, v)
    account.save()
    return limits.check(account, now=dt(2026, 10, 10))


def test_warnings_go_out_once_at_ninety_percent_and_once_when_over(usage_account):
    account, outbox, ref = usage_account
    assert note(account, disk_used_mb=50) == []
    assert note(account, disk_used_mb=91) == ["disk:warn"]
    assert note(account, disk_used_mb=95) == []  # still the same state: no second email
    assert note(account, disk_used_mb=100) == ["disk:over"]
    assert note(account, disk_used_mb=100) == []
    assert note(account, disk_used_mb=60) == []  # recovered, quietly
    assert note(account, disk_used_mb=92) == ["disk:warn"]  # and a new rise is a new warning
    assert sum("disk space limit" in m.subject for m in outbox) == 3
    assert "Your website cannot save new files" in next(m.body for m in outbox if "over its disk" in m.subject)


def test_dropping_from_over_to_close_says_nothing_new(usage_account):
    account, outbox, ref = usage_account
    note(account, disk_used_mb=100)
    assert note(account, disk_used_mb=93) == [] and account.status == HostingStatus.ACTIVE


def test_bandwidth_over_only_notifies_by_default(usage_account):
    account, outbox, ref = usage_account
    assert note(account, bandwidth_used_mb=60) == ["bandwidth:over"]
    account.refresh_from_db()
    assert account.status == HostingStatus.ACTIVE and "Please upgrade your plan" in outbox[-1].body


def test_bandwidth_over_can_suspend_and_lifts_by_itself_when_usage_is_back_under(usage_account, settings):
    account, outbox, ref = usage_account
    settings.NATIVE_BANDWIDTH_OVER_ACTION = "suspend"
    assert note(account, bandwidth_used_mb=51) == ["suspended", "bandwidth:over"]
    ref.poll_once()
    account.refresh_from_db()
    assert account.status == HostingStatus.SUSPENDED and "taken offline" in outbox[-1].body and account.limit_alerts["suspended_for"] == "bandwidth"
    assert note(account, bandwidth_used_mb=5) == ["unsuspended"]
    account.refresh_from_db()
    assert account.status == HostingStatus.ACTIVE and "suspended_for" not in account.limit_alerts


def test_a_suspension_made_by_staff_is_never_lifted_by_the_limit_check(usage_account, settings):
    account, outbox, ref = usage_account
    hosting.suspend_account(__import__("apps.core.system", fromlist=["SYSTEM"]).SYSTEM, account, reason="abuse")
    account.refresh_from_db()
    assert note(account, bandwidth_used_mb=1) == [] and account.status == HostingStatus.SUSPENDED


def test_a_new_month_starts_the_bandwidth_warnings_again(usage_account):
    account, outbox, ref = usage_account
    assert note(account, bandwidth_used_mb=60) == ["bandwidth:over"]
    assert limits.check(account, now=dt(2026, 11, 2)) == ["bandwidth:over"]  # still over in the new month's first reading: told again


def test_the_usage_sync_runs_the_check_for_native_accounts_only(measured, mailoutbox, settings):
    manager, account, ref, owner, tmp, product = measured
    (tmp / "web" / account.username / "sites" / "acme.com" / "public_html" / "big.bin").write_bytes(b"x" * (95 * 1048576))  # 95 of 100 MB with the database
    hosting.sync_usage(manager, account)
    account.refresh_from_db()
    assert account.disk_used_mb >= 95 and account.disk_limit_mb == 100 and account.bandwidth_limit_mb == 50 and account.bandwidth_used_mb == 3
    assert any("disk space limit" in m.subject for m in mailoutbox) and account.limit_alerts["disk"] == "warn"


def test_limit_values_are_typed_on_the_node_side():
    for bad in ({"inodes": 10}, {"io_weight": 0}, {"io_weight": 100000}, {"inodes": "many"}):
        with pytest.raises(catalog.InvalidCommand):
            catalog.validate("CREATE_WEBSITE", {**SITE, **bad})
    assert catalog.validate("USAGE_REPORT", {"account": "abcd1234", "days": 30})["days"] == 30
    with pytest.raises(catalog.InvalidCommand):
        catalog.validate("USAGE_REPORT", {"account": "abcd1234", "days": 500})
    _ = (timedelta, AuditEvent, django_mail)
