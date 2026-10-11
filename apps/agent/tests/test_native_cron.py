"""Scheduled jobs on a native node: the node's rules and files against a pretend machine, and the customer's Cron screen through the whole path."""
import json

import pytest
from django.urls import reverse

from apps.agent import catalog, services as agent
from apps.agent.node.cron import Cron, job_id
from apps.agent.node.system import FakeSystem, SystemError_
from apps.agent.node.website import HandlerError, Websites
from apps.agent.reference_agent import NativeMachine
from apps.hosting import control

from .test_native_website import make_node, order, world  # noqa: F401

pytestmark = pytest.mark.django_db
SITE = {"account": "abcd1234", "domain": "acme.com", "php_version": "8.3"}
A = {"account": "abcd1234"}
ADD = {**A, "schedule": "*/10 * * * *", "command": "php public_html/cron.php --quiet"}


@pytest.fixture
def node():
    system = FakeSystem()
    web = Websites(system)
    web.run("CREATE_WEBSITE", SITE)
    return system, web, Cron(system, web, now=lambda: 1_800_000_000)


def test_a_job_is_stored_and_written_to_cron_as_a_fixed_line(node):
    system, web, cron = node
    code, data = cron.run("CRON_CREATE", ADD)
    assert code == "created" and data["job_id"] == job_id("*/10 * * * *", "php public_html/cron.php --quiet")
    line = [l for l in system.files["/etc/cron.d/whp-abcd1234"].splitlines() if l.startswith("*/10")]
    assert line == [f"*/10 * * * * root /usr/local/bin/wh-agent cron-run abcd1234 {data['job_id']} >/dev/null 2>&1"]
    assert "cron.php" not in system.files["/etc/cron.d/whp-abcd1234"]  # what the customer typed is never in the cron file
    assert cron.run("CRON_CREATE", ADD)[0] == "exists"  # the same job twice is one job


@pytest.mark.parametrize("schedule", ["* * * * *", "*/4 * * * *", "61 * * * *", "0 25 * * *"])
def test_a_schedule_that_is_too_frequent_or_wrong_is_refused(node, schedule):
    system, web, cron = node
    with pytest.raises(HandlerError):
        cron.run("CRON_CREATE", {**ADD, "schedule": schedule})
    assert "/etc/cron.d/whp-abcd1234" not in system.files


@pytest.mark.parametrize("command", ["sh -c id", "php /etc/passwd.php", "php ../x.php", "php a.php; id", "php a.php $(id)", "bash a.php", "php a.txt"])
def test_only_php_inside_the_website_is_accepted(node, command):
    system, web, cron = node
    with pytest.raises((HandlerError, catalog.InvalidCommand)):
        cron.run("CRON_CREATE", {**ADD, "command": command})
    assert "/etc/cron.d/whp-abcd1234" not in system.files


def test_ten_jobs_at_most(node):
    system, web, cron = node
    for i in range(10):
        cron.run("CRON_CREATE", {**ADD, "command": f"php public_html/job{i}.php"})
    with pytest.raises(HandlerError) as err:
        cron.run("CRON_CREATE", {**ADD, "command": "php public_html/eleven.php"})
    assert err.value.code == "limit_reached"


def test_update_disable_and_delete(node):
    system, web, cron = node
    wanted = cron.run("CRON_CREATE", ADD)[1]["job_id"]
    new = cron.run("CRON_UPDATE", {**A, "job_id": wanted, "schedule": "0 2 * * *"})[1]["job_id"]
    assert new != wanted and "0 2 * * * root" in system.files["/etc/cron.d/whp-abcd1234"]
    cron.run("CRON_UPDATE", {**A, "job_id": new, "enabled": "no"})
    assert "/etc/cron.d/whp-abcd1234" not in system.files  # nothing enabled: no file
    assert cron.run("CRON_LIST", A)[1]["jobs"][0]["enabled"] is False
    cron.run("CRON_UPDATE", {**A, "job_id": new, "enabled": "yes"})
    cron.run("CRON_DELETE", {**A, "job_id": new})
    assert cron.run("CRON_LIST", A)[1]["jobs"] == []
    with pytest.raises(HandlerError) as err:
        cron.run("CRON_DELETE", {**A, "job_id": new})
    assert err.value.code == "not_found"


def test_running_a_job_uses_the_accounts_own_slice_and_user_and_never_a_shell(node):
    system, web, cron = node
    wanted = cron.run("CRON_CREATE", {**ADD, "command": "php public_html/cron.php --quiet a=1"})[1]["job_id"]
    code, data = cron.run("CRON_RUN", {**A, "job_id": wanted})
    assert code == "ran" and data["job"]["last_status"] == "ok" and data["job"]["last_run_at"].startswith("2027")
    argv = next(c for c in system.commands if c[:1] == ["systemd-run"])
    assert argv[argv.index("--uid") + 1] == "abcd1234" and "--slice" in argv and "whp-abcd1234.slice" in argv
    assert argv[-4:] == ["/opt/remi/php83/root/usr/bin/php", "public_html/cron.php", "--quiet", "a=1"] and "--working-directory=/home/abcd1234/sites/acme.com" in argv
    assert not any(c[:1] in (["sh"], ["bash"]) for c in system.commands)
    system.fail_on = len(system.commands)
    assert cron.run("CRON_RUN", {**A, "job_id": wanted})[1]["job"]["last_status"] == "failed"


def test_a_suspended_website_runs_no_jobs_and_a_deleted_one_loses_them(node):
    system, web, cron = node
    wanted = cron.run("CRON_CREATE", ADD)[1]["job_id"]
    assert cron.cron_run("abcd1234", wanted) is not None  # what cron itself does every ten minutes
    web.run("SUSPEND_WEBSITE", A)
    assert cron.cron_run("abcd1234", wanted) is None
    with pytest.raises(HandlerError) as err:
        cron.run("CRON_RUN", {**A, "job_id": wanted})
    assert err.value.code == "suspended"
    web.run("UNSUSPEND_WEBSITE", A)
    assert cron.cron_run("abcd1234", "ffffffffff") is None  # an unknown job is ignored quietly
    web.run("DELETE_WEBSITE", {"account": "abcd1234", "domain": "acme.com", "keep_archive": "no"})
    assert "/etc/cron.d/whp-abcd1234" not in system.files and "/var/lib/whp/cron/abcd1234.json" not in system.files
    assert cron.cron_run("abcd1234", wanted) is None


def test_another_accounts_name_or_no_account_is_refused(node):
    system, web, cron = node
    with pytest.raises(HandlerError) as err:
        cron.run("CRON_LIST", {"account": "nobody123"})
    assert err.value.code == "not_found"
    with pytest.raises(HandlerError):
        cron.run("CRON_CREATE", {**ADD, "account": "ABC"})


# --- the customer's screen, all the way through --------------------------------------------------------------------------------------------

@pytest.fixture
def site(world, tmp_path):  # noqa: F811
    manager, client, product = world
    server, ref = make_node(manager, "web-1")
    ref.machine = NativeMachine(files_dir=tmp_path / "web")
    account = order(manager, client, product, server)
    ref.poll_once()
    account.refresh_from_db()
    agent.inline_agent = ref.poll_once
    yield account, ref, client.contacts.get().user
    agent.inline_agent = None


def test_the_customers_cron_screen_works_against_a_native_node(site, client):
    account, ref, owner = site
    client.force_login(owner)
    page = client.get(reverse("hosting_customer:cron", args=[account.pk]))
    assert page.status_code == 200
    job = control.perform(owner, account, "cron", "create_cron_job", "0 3 * * *", "php public_html/backup.php")
    assert control.perform(owner, account, "cron", "list_cron_jobs")[0].job_id == job
    assert "0 3 * * * root" in ref.machine.system.files[f"/etc/cron.d/whp-{account.username}"]
    info = control.perform(owner, account, "cron", "run_cron_job", job)
    assert info.last_status == "ok" and info.last_run_at
    control.perform(owner, account, "cron", "update_cron_job", job, enabled=False)
    assert not control.perform(owner, account, "cron", "list_cron_jobs")[0].enabled
    options = control.perform(owner, account, "cron", "cron_options")
    assert options.max_jobs == 10 and options.min_interval_minutes == 5 and len(options.presets) == 6
    control.perform(owner, account, "cron", "delete_cron_job", job)
    assert control.perform(owner, account, "cron", "list_cron_jobs") == []
    _ = json
