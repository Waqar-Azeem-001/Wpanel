"""Native website provisioning (N3): the node's handlers against a pretend Linux machine, and the whole path from an order to an active site."""
import json

import pytest
from django.core import mail

from apps.accounts.roles import Role
from apps.agent import catalog, enrollment, envelope, placement, reference_agent
from apps.agent.models import AgentJob, JobStatus, NodeStatus
from apps.agent.node import render
from apps.agent.node.system import FakeSystem, SystemError_, check_path
from apps.agent.node.website import HandlerError, Websites
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.hosting import services as hosting
from apps.hosting.models import HostingAccount, HostingStatus
from apps.products import services as product_services
from apps.products.models import Product, ProductType, Server, ServerKind

pytestmark = pytest.mark.django_db
SITE = {"account": "abcd1234", "domain": "acme.com", "php_version": "8.3"}
GONE = {"account": "abcd1234", "domain": "acme.com"}


@pytest.fixture
def machine():
    system = FakeSystem()
    return system, Websites(system)


# --- the node's handlers ------------------------------------------------------------------------------------------------------------------

def test_creating_a_website_makes_the_user_folders_pool_unit_slice_and_vhost(machine):
    system, web = machine
    code, data = web.run("CREATE_WEBSITE", {**SITE, "disk_mb": 2048, "memory_mb": 512, "processes": 40, "cpu_percent": 50})
    assert code == "created" and data["root"] == "/home/abcd1234/sites/acme.com/public_html" and data["uid"] == 2000
    assert system.users == {"abcd1234": 2000}
    assert "/home/abcd1234/sites/acme.com/public_html/index.html" in system.files
    vhost = system.files["/etc/httpd/conf.d/whp/abcd1234-acme.com.conf"]
    assert "ServerName acme.com" in vhost and "proxy:unix:/run/whp/php/abcd1234.sock" in vhost and "Options -Indexes" in vhost
    pool = system.files["/etc/whp/php/abcd1234.conf"]
    assert "user = abcd1234" in pool and "open_basedir] = /home/abcd1234" in pool and "disable_functions" in pool and "pm.max_children = 10" in pool
    unit = system.files["/etc/systemd/system/whp-php-abcd1234.service"]
    assert "Slice=whp-abcd1234.slice" in unit and "/opt/remi/php83/root/usr/sbin/php-fpm" in unit and "NoNewPrivileges=yes" in unit
    limits = system.files["/etc/systemd/system/whp-abcd1234.slice"]
    assert "MemoryMax=512M" in limits and "CPUQuota=50%" in limits and "TasksMax=40" in limits
    assert ["xfs_quota", "-x", "-c", "limit -p bsoft=1843m bhard=2048m 2000", "/home"] in system.commands
    assert json.loads(system.files["/var/lib/whp/accounts/abcd1234.json"])["limits"]["disk_mb"] == 2048


def test_the_web_server_config_is_tested_before_it_is_reloaded(machine):
    system, web = machine
    web.run("CREATE_WEBSITE", SITE)
    runs = [c for c in system.commands if c and c[0] in ("httpd", "systemctl")]
    assert runs.index(["httpd", "-t"]) < runs.index(["systemctl", "reload", "httpd"])


def test_defaults_apply_when_no_limits_are_given(machine):
    system, web = machine
    web.run("CREATE_WEBSITE", SITE)
    assert json.loads(system.files["/var/lib/whp/accounts/abcd1234.json"])["limits"] == render.DEFAULTS


def test_every_command_is_an_argument_list_never_a_shell_string(machine):
    system, web = machine
    web.run("CREATE_WEBSITE", SITE)
    web.run("SUSPEND_WEBSITE", {"account": "abcd1234"})
    web.run("DELETE_WEBSITE", {**GONE, "keep_archive": "yes"})
    for command in system.commands:
        assert isinstance(command, list) and all(isinstance(part, str) for part in command)
        assert command[0] not in ("sh", "bash", "zsh", "eval", "ssh")
    with pytest.raises(SystemError_):
        system.run("useradd x")


def test_creating_twice_changes_nothing_the_second_time(machine):
    system, web = machine
    web.run("CREATE_WEBSITE", SITE)
    before = len(system.commands)
    assert web.run("CREATE_WEBSITE", SITE)[0] == "exists" and len(system.commands) == before


def test_an_account_cannot_be_reused_for_another_site_or_over_an_existing_system_user(machine):
    system, web = machine
    web.run("CREATE_WEBSITE", SITE)
    with pytest.raises(HandlerError) as caught:
        web.run("CREATE_WEBSITE", {**SITE, "domain": "other.com"})
    assert caught.value.code == "conflict"
    system.users["bobby123"] = 5
    with pytest.raises(HandlerError, match="already exists"):
        web.run("CREATE_WEBSITE", {**SITE, "account": "bobby123", "domain": "b.com"})


@pytest.mark.parametrize("params", [{**SITE, "domain": "a.com\nServerName evil.com"}, {**SITE, "domain": "a.com; touch /x"}, {**SITE, "account": "../root"},
                                    {**SITE, "php_version": "8.3\nfoo"}, {**SITE, "disk_mb": 5}, {**SITE, "memory_mb": "lots"}, {**SITE, "extra": 1}])
def test_bad_values_are_refused_before_anything_is_touched(machine, params):
    system, web = machine
    with pytest.raises(HandlerError) as caught:
        web.run("CREATE_WEBSITE", params)
    assert caught.value.code == "invalid" and system.commands == [] and system.files == {}


def test_a_failure_part_way_undoes_what_was_done(machine):
    system, web = machine
    web.run("CREATE_WEBSITE", {**SITE, "account": "probe123", "domain": "probe.com"})
    failing = next(i for i, c in enumerate(system.commands) if c == ["httpd", "-t"])
    system2 = FakeSystem()
    system2.fail_on = failing
    with pytest.raises(HandlerError) as caught:
        Websites(system2).run("CREATE_WEBSITE", SITE)
    assert caught.value.code == "system_error"
    assert system2.users == {}  # the user was removed again
    assert not [p for p in system2.files if p.startswith("/etc/") or p.startswith("/var/lib/whp")]  # no config or state left behind
    assert ["userdel", "--remove", "abcd1234"] in system2.commands


def test_suspending_and_reactivating(machine):
    system, web = machine
    web.run("CREATE_WEBSITE", SITE)
    assert web.run("SUSPEND_WEBSITE", {"account": "abcd1234"})[0] == "suspended"
    assert "whp-suspended" in system.files["/etc/httpd/conf.d/whp/abcd1234-acme.com.conf"] and ["systemctl", "stop", "whp-php-abcd1234.service"] in system.commands
    n = len(system.commands)
    web.run("SUSPEND_WEBSITE", {"account": "abcd1234"})
    assert len(system.commands) == n  # twice is harmless
    assert web.run("UNSUSPEND_WEBSITE", {"account": "abcd1234"})[0] == "active"
    assert "proxy:unix" in system.files["/etc/httpd/conf.d/whp/abcd1234-acme.com.conf"] and ["systemctl", "start", "whp-php-abcd1234.service"] in system.commands


def test_changing_php_version_and_limits(machine):
    system, web = machine
    web.run("CREATE_WEBSITE", SITE)
    assert web.run("CHANGE_PHP_VERSION", {**SITE, "php_version": "8.1"})[0] == "changed"
    assert "/opt/remi/php81/" in system.files["/etc/systemd/system/whp-php-abcd1234.service"]
    assert web.run("CHANGE_PHP_VERSION", {**SITE, "php_version": "8.1"})[0] == "unchanged"
    web.run("SET_LIMITS", {"account": "abcd1234", "disk_mb": 100, "memory_mb": 256, "processes": 20, "cpu_percent": 25})
    assert "MemoryMax=256M" in system.files["/etc/systemd/system/whp-abcd1234.slice"]
    with pytest.raises(HandlerError) as caught:
        web.run("CHANGE_PHP_VERSION", {"account": "nobody123", "domain": "x.com", "php_version": "8.1"})
    assert caught.value.code == "not_found"
    with pytest.raises(HandlerError):
        web.run("SET_LIMITS", {"account": "abcd1234"})  # on SET_LIMITS every limit is required


def test_deleting_archives_first_then_removes_everything(machine):
    system, web = machine
    web.run("CREATE_WEBSITE", SITE)
    code, data = web.run("DELETE_WEBSITE", {**GONE, "keep_archive": "yes"})
    assert code == "deleted" and data["archive"] == "/var/backups/whp/archive-abcd1234.tar.zst"
    commands = [c for c in system.commands if isinstance(c, list) and c and c[0] in ("tar", "userdel")]
    assert commands[0][0] == "tar" and commands[-1][:2] == ["userdel", "--remove"]
    assert system.users == {} and not [p for p in system.files if "abcd1234" in p]
    assert web.run("DELETE_WEBSITE", {**GONE, "keep_archive": "no"})[0] == "absent"  # deleting twice is not an error


def test_a_failed_archive_stops_the_delete_before_anything_is_removed(machine):
    system, web = machine
    web.run("CREATE_WEBSITE", SITE)
    system.fail_on = len(system.commands)  # the very next command is the tar
    with pytest.raises(HandlerError):
        web.run("DELETE_WEBSITE", {**GONE, "keep_archive": "yes"})
    assert system.users == {"abcd1234": 2000} and "/var/lib/whp/accounts/abcd1234.json" in system.files


def test_deleting_the_wrong_domain_is_refused(machine):
    system, web = machine
    web.run("CREATE_WEBSITE", SITE)
    with pytest.raises(HandlerError, match="different website"):
        web.run("DELETE_WEBSITE", {**GONE, "domain": "other.com", "keep_archive": "no"})
    assert system.users


@pytest.mark.parametrize("path", ["/etc/../etc/passwd", "relative/path", "/root/.ssh/authorized_keys", "/home/../root", "/etc/x\x00", "/usr/bin/x", "/home//x"])
def test_the_agent_refuses_paths_outside_its_places(path):
    with pytest.raises(SystemError_):
        check_path(path)


def test_config_values_with_newlines_or_quotes_cannot_get_into_a_file():
    for bad in ("a.com\nRequire all denied", 'a.com"', "a b", ""):
        with pytest.raises(ValueError):
            render.vhost("abcd1234", bad)


# --- the whole path: order -> job -> node -> active -------------------------------------------------------------------------------------

@pytest.fixture
def world(staff):
    manager = staff(Role.MANAGER)
    client = client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test"})
    product = Product.objects.get(pk=product_services.create_product(manager, {"name": "Native Plan", "type": ProductType.SHARED_HOSTING}).pk)
    product.whm_package_name = "native_basic"
    product.resource_limits = {"disk_mb": 2048, "websites": 1}
    product.save()
    return manager, client, product


def make_node(manager, name="native-1", capabilities=("web", "php", "database"), region="EU", ips=None):
    server = Server.objects.create(name=name, hostname=f"{name}.example.com", kind=ServerKind.NATIVE, region=region)
    node, token = enrollment.invite(manager, server)
    reply = enrollment.enroll(token, reference_agent.make_csr()[1], {**reference_agent.INVENTORY, "capabilities": list(capabilities), **({"ip_addresses": ips} if ips else {})}, "0.1.0")
    from django.test import Client

    agent = reference_agent.ReferenceAgent(Client(), reply["token"], server.pk, envelope.public_key_b64(), machine=reference_agent.NativeMachine())
    return server, agent


def order(manager, client, product, server, domain="acme.com"):
    account = hosting.request_hosting(manager, client, product, domain, server=server)
    hosting.complete_provisioning(manager, account)
    account.refresh_from_db()
    return account


def test_a_paid_order_becomes_an_active_website_on_the_node(world, mailoutbox):
    manager, client, product = world
    server, agent = make_node(manager)
    account = order(manager, client, product, server)
    assert account.status == HostingStatus.PENDING  # queued, not done: only the node can finish it
    job = AgentJob.objects.get()
    assert job.command == "CREATE_WEBSITE" and job.params["disk_mb"] == 2048 and job.params["domain"] == "acme.com" and job.subject == f"hosting:{account.pk}"
    assert agent.poll_once() == job.pk
    account.refresh_from_db()
    assert account.status == HostingStatus.ACTIVE and account.last_error == ""
    system = agent.machine.system
    assert system.users and "/etc/httpd/conf.d/whp/%s-acme.com.conf" % account.username in system.files
    actions = list(AuditEvent.objects.values_list("action", flat=True))
    assert {"hosting.provisioning_started", "hosting.provisioned", "agent.job_succeeded"} <= set(actions)
    assert any("Username: " + account.username in m.body and "Temporary password" not in m.body for m in mailoutbox)


def test_pressing_provision_twice_does_not_queue_a_second_website(world):
    manager, client, product = world
    server, agent = make_node(manager)
    account = hosting.request_hosting(manager, client, product, "acme.com", server=server)
    hosting.complete_provisioning(manager, account)
    hosting.complete_provisioning(manager, account)
    assert AgentJob.objects.count() == 1


def test_a_failure_on_the_node_marks_the_account_failed_and_a_retry_works(world):
    manager, client, product = world
    server, agent = make_node(manager)
    agent.machine.system.users["taken"] = 9  # make the node fail the first attempt cleanly
    account = hosting.request_hosting(manager, client, product, "acme.com", server=server)
    account.username = "taken"
    account.save()
    hosting.complete_provisioning(manager, account)
    agent.poll_once()
    account.refresh_from_db()
    assert account.status == HostingStatus.FAILED and "already exists" in account.last_error
    assert AuditEvent.objects.filter(action="hosting.provisioning_failed").exists()
    del agent.machine.system.users["taken"]
    hosting.complete_provisioning(manager, account)  # the retry is a new job
    agent.poll_once()
    account.refresh_from_db()
    assert account.status == HostingStatus.ACTIVE and AgentJob.objects.count() == 2


def test_suspend_unsuspend_change_plan_and_terminate_reach_the_node(world):
    manager, client, product = world
    server, agent = make_node(manager)
    account = order(manager, client, product, server)
    agent.poll_once()
    account.refresh_from_db()
    system = agent.machine.system

    hosting.suspend_account(manager, account, reason="test")
    agent.poll_once()
    assert "whp-suspended" in system.files[f"/etc/httpd/conf.d/whp/{account.username}-acme.com.conf"]
    hosting.unsuspend_account(manager, account)
    agent.poll_once()
    assert "proxy:unix" in system.files[f"/etc/httpd/conf.d/whp/{account.username}-acme.com.conf"]

    bigger = Product.objects.get(pk=product_services.create_product(manager, {"name": "Bigger", "type": ProductType.SHARED_HOSTING}).pk)
    bigger.whm_package_name, bigger.resource_limits = "native_big", {"disk_mb": 8192, "memory_mb": 2048}
    bigger.save()
    hosting.change_package(manager, account, bigger)
    agent.poll_once()
    assert json.loads(system.files[f"/var/lib/whp/accounts/{account.username}.json"])["limits"]["memory_mb"] == 2048

    hosting.terminate_account(manager, account)
    job = AgentJob.objects.latest("id")
    assert job.command == "DELETE_WEBSITE" and job.params["keep_archive"] == "yes"
    agent.poll_once()
    assert system.users == {} and AgentJob.objects.latest("id").status == JobStatus.SUCCEEDED


def test_a_node_failure_on_a_later_command_is_recorded_on_the_account(world):
    manager, client, product = world
    server, agent = make_node(manager)
    account = order(manager, client, product, server)
    agent.poll_once()
    account.refresh_from_db()
    agent.machine.system.fail_on = len(agent.machine.system.commands) + 1  # the write comes first, the next command run fails
    hosting.suspend_account(manager, account, reason="x")
    agent.poll_once()
    account.refresh_from_db()
    assert "SUSPEND_WEBSITE did not complete" in account.last_error and AuditEvent.objects.filter(action="hosting.native_job_failed").exists()


def test_a_server_without_an_enrolled_agent_cannot_provision(world):
    manager, client, product = world
    server = Server.objects.create(name="bare", hostname="b.example.com", kind=ServerKind.NATIVE)
    account = hosting.request_hosting(manager, client, product, "acme.com", server=server)
    with pytest.raises(Exception, match="no enrolled agent|Provisioning failed"):
        hosting.complete_provisioning(manager, account)
    account.refresh_from_db()
    assert account.status == HostingStatus.FAILED


def test_the_native_server_type_reports_its_agent_state(world):
    manager, client, product = world
    server, agent = make_node(manager)
    info = hosting.get_adapter(server).check_connection()
    assert info["version"] == "0.1.0" and info["state"] in ("healthy", "offline")


# --- choosing a node ------------------------------------------------------------------------------------------------------------------------

def node_of(server):
    from apps.agent.models import AgentNode

    return AgentNode.objects.get(server=server)


def heartbeat(server, state="up"):
    enrollment.heartbeat(node_of(server), {"services": {"web": state}})


def test_placement_picks_the_least_full_healthy_node_with_room(world):
    manager, client, product = world
    a, _ = make_node(manager, "node-a")
    b, _ = make_node(manager, "node-b")
    for s in (a, b):
        heartbeat(s)
        node_of(s).__class__.objects.filter(server=s).update(capacity_sites=10)
    for i in range(3):
        HostingAccount.objects.create(client=client, product=product, server=a, domain=f"a{i}.com", username=f"usera{i}xx", status=HostingStatus.ACTIVE)
    assert placement.pick_node() == b
    for i in range(10):
        HostingAccount.objects.create(client=client, product=product, server=b, domain=f"b{i}.com", username=f"userb{i}xx", status=HostingStatus.ACTIVE)
    assert placement.pick_node() == a  # b is full
    assert placement.pick_node(among=[b]) is None


def test_placement_skips_unhealthy_unfit_and_foreign_region_nodes(world):
    manager, client, product = world
    down, _ = make_node(manager, "node-down")
    heartbeat(down, "down")
    mail_only, _ = make_node(manager, "node-mail", capabilities=("mail",))
    heartbeat(mail_only)
    us, _ = make_node(manager, "node-us", region="US")
    heartbeat(us)
    revoked, _ = make_node(manager, "node-revoked")
    heartbeat(revoked)
    enrollment.revoke(manager, node_of(revoked))
    assert placement.pick_node() == us and placement.pick_node(region="EU") is None and placement.pick_node(region="us") == us


def test_a_product_mapped_to_several_native_nodes_places_the_order_itself(world):
    manager, client, product = world
    a, _ = make_node(manager, "node-a")
    b, _ = make_node(manager, "node-b")
    for s in (a, b):
        heartbeat(s)
        product.servers.add(s)
    HostingAccount.objects.create(client=client, product=product, server=a, domain="x.com", username="userxxxx", status=HostingStatus.ACTIVE)
    assert hosting.request_hosting(manager, client, product, "acme.com").server == b


def test_the_new_commands_are_on_the_list_with_typed_fields():
    for name in ("SET_LIMITS", "SUSPEND_WEBSITE", "UNSUSPEND_WEBSITE"):
        assert name in catalog.COMMANDS
    assert NodeStatus.ENROLLED == "enrolled"
