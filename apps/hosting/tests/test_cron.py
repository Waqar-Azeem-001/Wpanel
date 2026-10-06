"""Cron jobs (H8): the demo provider's rules, and the customer screen end to end."""
import pytest
from django.core.management import call_command

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.core.exceptions import ServiceError
from apps.hosting import control
from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.demo_cron import MAX_JOBS, DemoCron
from apps.hosting.models import HostingAccount
from apps.products import services as product_services
from apps.products.models import ProductType

pytestmark = pytest.mark.django_db


@pytest.fixture
def cron(tmp_path):
    return DemoCron(tmp_path / "state")


def test_a_job_is_added_listed_switched_off_edited_and_deleted(cron):
    job = cron.create("0 2 * * *", "php public_html/cron.php  --quiet")
    [row] = cron.jobs()
    assert (row.schedule, row.command, row.enabled, row.last_status) == ("0 2 * * *", "php public_html/cron.php --quiet", True, "")
    cron.update(job, enabled=False)
    cron.update(job, schedule="*/10 * * * *", command="php scripts/mail.php")
    [row] = cron.jobs()
    assert (row.schedule, row.command, row.enabled) == ("*/10 * * * *", "php scripts/mail.php", False)
    cron.delete(job)
    assert cron.jobs() == []
    with pytest.raises(HostingError, match="no longer exists"):
        cron.delete(job)


@pytest.mark.parametrize("schedule", ["", "* * * *", "60 * * * *", "* 24 * * *", "0 0 32 * *", "0 0 * 13 *", "x * * * *", "*/0 * * * *",
                                       "* * * * *", "*/3 * * * *", "1,2 * * * *", "0-10 * * * *"])
def test_bad_or_too_frequent_schedules_are_refused(cron, schedule):
    with pytest.raises(HostingError):
        cron.create(schedule, "php cron.php")


@pytest.mark.parametrize("schedule", ["*/5 * * * *", "0 * * * *", "0,30 9-17 * * 1-5", "15 3 1 * *", "0 2 * * 7", "*/20 * * * *"])
def test_normal_schedules_are_accepted(cron, schedule):
    assert cron.create(schedule, "php cron.php")


@pytest.mark.parametrize("command", ["", "ls", "rm -rf /", "php", "php /etc/cron.php", "php ../cron.php", "php cron.txt", "php a.php; ls",
                                      "php a.php && ls", "php a.php | tee x", "php a.php `id`", "php a.php $(id)", "php a.php > out",
                                      "bash x.php", "x" * 400])
def test_the_command_is_never_a_free_shell(cron, command):
    with pytest.raises(HostingError):
        cron.create("0 2 * * *", command)


def test_limits_and_duplicates(cron):
    cron.create("0 2 * * *", "php a.php")
    with pytest.raises(HostingError, match="already exists"):
        cron.create("0 2 * * *", "php a.php")
    for n in range(MAX_JOBS - 1):
        cron.create("0 3 * * *", f"php job{n}.php")
    with pytest.raises(HostingError, match="allows"):
        cron.create("0 4 * * *", "php more.php")


def test_run_now_records_a_sample_result(cron):
    job = cron.create("0 2 * * *", "php cleanup.php")
    done = cron.run(job)
    assert done.last_status == "ok" and "cleanup.php" in done.last_output and done.last_run_at
    assert cron.jobs()[0].last_status == "ok"


# --- The screen -------------------------------------------------------------------------------------------------------

@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def client_obj(manager):
    return client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test"})


@pytest.fixture
def owner(client_obj):
    return client_obj.contacts.get().user


@pytest.fixture
def account(owner, manager):
    product_services.create_product(manager, {"name": "Starter", "type": ProductType.SHARED_HOSTING})
    call_command("demo_hosting_data")
    return HostingAccount.objects.get(domain="demo-shop.example.com")


def curl(account, tail=""):
    return f"/account/hosting/{account.pk}/manage/cron/{tail}"


def test_the_page_shows_the_sample_jobs_with_names_and_results(client, owner, account):
    client.force_login(owner)
    page = client.get(curl(account)).content.decode()
    assert "Cron jobs" in page and "Every day at 02:00" in page and "Every 15 minutes" in page
    assert "cleanup.php" in page and "sync-stock.php" in page and "sample run" in page and "Not run yet" in page
    assert "2 / 10 jobs" in " ".join(page.split())


def test_add_switch_edit_run_and_delete_from_the_page(client, owner, account):
    client.force_login(owner)
    act = curl(account, "do/")
    client.post(act, {"action": "add", "preset": "0 3 * * 0", "command": "php public_html/weekly.php"})
    client.post(act, {"action": "add", "preset": "custom", "schedule": "30 4 * * *", "command": "php public_html/custom.php"})
    jobs = {j.command: j for j in control.perform(owner, account, "cron", "list_cron_jobs")}
    assert jobs["php public_html/weekly.php"].schedule == "0 3 * * 0" and jobs["php public_html/custom.php"].schedule == "30 4 * * *"
    weekly = jobs["php public_html/weekly.php"].job_id
    client.post(act, {"action": "toggle", "job": weekly, "enabled": "0"})
    client.post(act, {"action": "edit", "job": weekly, "schedule": "0 5 * * 1", "command": "php public_html/weekly2.php"})
    client.post(act, {"action": "run", "job": weekly})
    row = next(j for j in control.perform(owner, account, "cron", "list_cron_jobs") if j.job_id == weekly)
    assert (row.schedule, row.command, row.enabled, row.last_status) == ("0 5 * * 1", "php public_html/weekly2.php", False, "ok")
    client.post(act, {"action": "delete", "job": weekly})
    assert all(j.job_id != weekly for j in control.perform(owner, account, "cron", "list_cron_jobs"))


def test_refusals_are_shown_as_messages(client, owner, account):
    client.force_login(owner)
    act = curl(account, "do/")
    page = client.post(act, {"action": "add", "preset": "custom", "schedule": "* * * * *", "command": "php a.php"}, follow=True).content.decode()
    assert "more often than every 5 minutes" in page
    page = client.post(act, {"action": "add", "preset": "0 2 * * *", "command": "rm -rf /"}, follow=True).content.decode()
    assert "Only PHP scripts can be scheduled" in page
    assert "That action is not available" in client.post(act, {"action": "shell"}, follow=True).content.decode()


def test_audit_records_the_script_never_the_arguments(client, owner, account):
    client.force_login(owner)
    client.post(curl(account, "do/"), {"action": "add", "preset": "0 2 * * *", "command": "php public_html/a.php --token=secret123"})
    events = list(AuditEvent.objects.filter(action="hosting.control").values_list("metadata", flat=True))
    assert any(e.get("operation") == "create_cron_job" and e.get("script") == "public_html/a.php" for e in events)
    assert "secret123" not in str(events)


def test_strangers_get_nothing_and_a_provider_without_cron_shows_the_not_available_page(client, customer, owner, account):
    client.force_login(customer)
    assert client.get(curl(account)).status_code == 404
    assert client.post(curl(account, "do/"), {"action": "delete", "job": "x"}).status_code == 404
    from apps.products.models import Server

    Server.objects.filter(kind="demo").update(kind="manual")
    client.force_login(owner)
    assert "not available for your hosting server yet" in client.get(curl(account)).content.decode()
    with pytest.raises(ServiceError):
        control.perform(owner, account, "cron", "list_cron_jobs")
