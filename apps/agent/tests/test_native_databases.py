"""Native databases (N4): the node's database handlers, and the customer's ordinary database screens working through the native provider."""
import json
from datetime import timedelta

import pytest
from django.urls import reverse

from apps.accounts.roles import Role
from apps.agent import catalog, services as agent
from apps.agent.models import AgentJob, AgentNode, JobStatus
from apps.agent.node.databases import Databases
from apps.agent.node.mariadb import FakeMariadb, native_hash
from apps.agent.node.system import FakeSystem
from apps.agent.node.website import HandlerError, Websites
from apps.audit.models import AuditEvent
from apps.hosting import control
from apps.hosting.adapters.base import HostingError
from apps.hosting.models import HostingStatus

from .test_native_website import make_node, order, world  # noqa: F401  (the fixtures and helpers of the website tests)

pytestmark = pytest.mark.django_db
A = "abcd1234"


@pytest.fixture
def node_side():
    system, engine = FakeSystem(), FakeMariadb()
    Websites(system).run("CREATE_WEBSITE", {"account": A, "domain": "acme.com", "php_version": "8.3", "databases": 2})
    return system, engine, Databases(system, engine)


def run(db, command, **p):
    return db.run(command, {"account": A, **p})


# --- the node ---------------------------------------------------------------------------------------------------------------------------

def test_create_list_and_delete_databases_users_and_grants(node_side):
    _, engine, db = node_side
    assert run(db, "CREATE_DATABASE", name=f"{A}_shop")[0] == "created"
    assert run(db, "CREATE_DATABASE", name=f"{A}_shop")[0] == "exists"  # again: the same answer
    run(db, "CREATE_DB_USER", name=f"{A}_bob", password="correct horse battery")
    run(db, "GRANT_DB_ACCESS", database=f"{A}_shop", user=f"{A}_bob", privileges=["select", "insert"])
    inventory = run(db, "DB_INVENTORY")[1]
    assert inventory["limit"] == 2 and inventory["databases"][0]["users"] == [f"{A}_bob"]
    assert inventory["grants"] == [{"database": f"{A}_shop", "user": f"{A}_bob", "privileges": ["insert", "select"]}]
    run(db, "GRANT_DB_ACCESS", database=f"{A}_shop", user=f"{A}_bob", privileges=[])  # none removes access
    assert run(db, "DB_INVENTORY")[1]["grants"] == []
    run(db, "DELETE_DB_USER", name=f"{A}_bob")
    assert run(db, "DELETE_DB_USER", name=f"{A}_bob")[0] == "absent"
    run(db, "DELETE_DATABASE", name=f"{A}_shop")
    assert run(db, "DELETE_DATABASE", name=f"{A}_shop")[0] == "absent" and engine.dbs == {}


def test_passwords_reach_the_engine_only_as_hashes(node_side):
    _, engine, db = node_side
    run(db, "CREATE_DB_USER", name=f"{A}_bob", password="correct horse battery")
    assert engine.users[f"{A}_bob"] == native_hash("correct horse battery") and "correct horse" not in json.dumps(engine.users)
    run(db, "SET_DB_PASSWORD", name=f"{A}_bob", password="another long secret")
    assert engine.users[f"{A}_bob"] == native_hash("another long secret")
    assert native_hash("password") == "*2470C0C06DEE42FD1618BB99005ADCA2EC9D1E19"  # the well-known MySQL value, so the formula is right


def test_the_plan_limit_is_enforced_on_the_node(node_side):
    _, _, db = node_side
    run(db, "CREATE_DATABASE", name=f"{A}_one")
    run(db, "CREATE_DATABASE", name=f"{A}_two")
    with pytest.raises(HandlerError) as caught:
        run(db, "CREATE_DATABASE", name=f"{A}_three")
    assert caught.value.code == "limit_reached"


def test_one_account_can_never_touch_anothers_databases_or_users(node_side):
    system, engine, db = node_side
    Websites(system).run("CREATE_WEBSITE", {"account": "other567", "domain": "other.com", "php_version": "8.3"})
    engine.create_db("other567_secret")
    engine.create_user("other567_user", "*" + "0" * 40)
    for command, extra in (("CREATE_DATABASE", {"name": "other567_new"}), ("DELETE_DATABASE", {"name": "other567_secret"}), ("DB_TABLES", {"database": "other567_secret"}),
                           ("DB_BROWSE", {"database": "other567_secret", "table": "t", "offset": 0, "limit": 10}),
                           ("DELETE_DB_USER", {"name": "other567_user"}), ("SET_DB_PASSWORD", {"name": "other567_user", "password": "x" * 14}),
                           ("GRANT_DB_ACCESS", {"database": "other567_secret", "user": "other567_user", "privileges": ["select"]}),
                           ("BACKUP_DATABASE", {"name": "other567_secret"})):
        with pytest.raises(HandlerError) as caught:
            run(db, command, **extra)
        assert caught.value.code in ("forbidden", "not_found"), command
    assert "other567_secret" in engine.dbs and "other567_user" in engine.users


@pytest.mark.parametrize("command,extra", [("CREATE_DATABASE", {"name": f"{A}_x`; DROP DATABASE mysql; --"}), ("CREATE_DATABASE", {"name": f"{A}_a b"}),
                                           ("CREATE_DB_USER", {"name": f"{A}_u'; --", "password": "x" * 14}), ("CREATE_DB_USER", {"name": f"{A}_u", "password": "short"}),
                                           ("GRANT_DB_ACCESS", {"database": f"{A}_d", "user": f"{A}_u", "privileges": ["all"]}),
                                           ("GRANT_DB_ACCESS", {"database": f"{A}_d", "user": f"{A}_u", "privileges": ["select", "select"]}),
                                           ("GRANT_DB_ACCESS", {"database": f"{A}_d", "user": f"{A}_u", "privileges": "select"}),
                                           ("DB_BROWSE", {"database": f"{A}_d", "table": "t; DROP TABLE x", "offset": 0, "limit": 10}),
                                           ("DB_BROWSE", {"database": f"{A}_d", "table": "t", "offset": -1, "limit": 10}),
                                           ("DB_BROWSE", {"database": f"{A}_d", "table": "t", "offset": 0, "limit": 1000})])
def test_bad_values_never_reach_the_engine(node_side, command, extra):
    system, engine, db = node_side
    with pytest.raises(HandlerError) as caught:
        run(db, command, **extra)
    assert caught.value.code == "invalid" and engine.dbs == {} and engine.users == {}


def test_browsing_is_read_only_paged_and_cuts_long_values(node_side):
    _, engine, db = node_side
    run(db, "CREATE_DATABASE", name=f"{A}_shop")
    engine.add_table(f"{A}_shop", "orders", [("id", "int", False, True, None), ("note", "text", True, False, None)], [[i, "x" * 900 if i == 1 else f"n{i}"] for i in range(1, 8)])
    assert run(db, "DB_TABLES", database=f"{A}_shop")[1]["tables"] == [{"name": "orders", "rows": 7, "size_kb": 16}]
    assert [c["name"] for c in run(db, "DB_DESCRIBE", database=f"{A}_shop", table="orders")[1]["columns"]] == ["id", "note"]
    page = run(db, "DB_BROWSE", database=f"{A}_shop", table="orders", offset=0, limit=3)[1]
    assert page["total"] == 7 and len(page["rows"]) == 3 and len(page["rows"][0][1]) == 500 and page["key_columns"] == ["id"]
    assert run(db, "DB_BROWSE", database=f"{A}_shop", table="orders", offset=6, limit=3)[1]["rows"] == [["7", "n7"]]
    with pytest.raises(HandlerError) as caught:
        run(db, "DB_BROWSE", database=f"{A}_shop", table="nope", offset=0, limit=3)
    assert caught.value.code == "not_found"


def test_backup_and_restore_with_a_safety_backup(node_side):
    system, engine, db = node_side
    run(db, "CREATE_DATABASE", name=f"{A}_shop")
    engine.add_table(f"{A}_shop", "t", [("id", "int", False, True, None)], [[1], [2]])
    backup_id = run(db, "BACKUP_DATABASE", name=f"{A}_shop")[1]["backup_id"]
    engine.dbs[f"{A}_shop"]["t"]["rows"] = [[9]]  # the data changes afterwards
    code, data = run(db, "RESTORE_DATABASE", name=f"{A}_shop", backup_id=backup_id)
    assert code == "restored" and engine.dbs[f"{A}_shop"]["t"]["rows"] == [[1], [2]] and data["safety_backup_id"]
    run(db, "RESTORE_DATABASE", name=f"{A}_shop", backup_id=data["safety_backup_id"])  # the mistake can be undone
    assert engine.dbs[f"{A}_shop"]["t"]["rows"] == [[9]]
    with pytest.raises(HandlerError) as caught:
        run(db, "RESTORE_DATABASE", name=f"{A}_shop", backup_id="20200101T000000Z-deadbeef")
    assert caught.value.code == "not_found"


def test_a_dump_that_did_not_finish_is_never_restored(node_side):
    system, engine, db = node_side
    run(db, "CREATE_DATABASE", name=f"{A}_shop")
    backup_id = run(db, "BACKUP_DATABASE", name=f"{A}_shop")[1]["backup_id"]
    for path in [p for p in system.files if p.endswith(".ok")]:
        del system.files[path]
    with pytest.raises(HandlerError):
        run(db, "RESTORE_DATABASE", name=f"{A}_shop", backup_id=backup_id)


# --- the customer's screens, through the native provider ------------------------------------------------------------------------------

@pytest.fixture
def live(world):  # noqa: F811
    manager, client, product = world
    product.resource_limits = {"disk_mb": 2048, "databases": 2}
    product.save()
    server, ref = make_node(manager)
    account = order(manager, client, product, server)
    ref.poll_once()
    account.refresh_from_db()
    assert account.status == HostingStatus.ACTIVE
    agent.inline_agent = ref.poll_once  # the node answers while the screen waits
    yield manager, client, account, ref
    agent.inline_agent = None


def owner_of(client):
    return client.contacts.get().user


def test_the_database_screens_work_unchanged_through_the_native_provider(live, client):
    manager, c, account, ref = live
    owner = owner_of(c)
    prefix = account.username + "_"
    control.perform(owner, account, "databases", "create_database", "shop")
    control.perform(owner, account, "databases", "create_database_user", "bob", "correct horse battery")
    control.perform(owner, account, "databases", "grant_database_access", prefix + "shop", prefix + "bob", ["select", "update"])
    listing = control.perform(owner, account, "databases", "list_databases")
    assert [d.name for d in listing] == [prefix + "shop"] and listing[0].users == [prefix + "bob"]
    assert control.perform(owner, account, "databases", "list_database_grants")[0].privileges == ["select", "update"]
    assert control.perform(owner, account, "databases", "database_limits").name_prefix == prefix
    assert control.perform(owner, account, "databases", "database_limits").max_databases == 2
    ref.machine.engine.add_table(prefix + "shop", "orders", [("id", "int", False, True, None)], [[1], [2], [3]])
    assert control.perform(owner, account, "databases", "list_tables", prefix + "shop")[0].rows == 3
    page = control.perform(owner, account, "databases", "browse_table", prefix + "shop", "orders", 0, 50)
    assert page.rows == [["1"], ["2"], ["3"]] and page.key_columns == ["id"]
    control.perform(owner, account, "databases", "delete_database", prefix + "shop")
    assert control.perform(owner, account, "databases", "list_databases") == []


def test_the_page_in_the_browser_lists_native_databases(live, client):
    manager, c, account, ref = live
    owner = owner_of(c)
    control.perform(owner, account, "databases", "create_database", "shop")
    client.force_login(owner)
    html = client.get(reverse("hosting_customer:databases", args=[account.pk])).content.decode()
    assert account.username + "_shop" in html


def test_the_plan_limit_message_reaches_the_customer(live):
    manager, c, account, ref = live
    owner = owner_of(c)
    control.perform(owner, account, "databases", "create_database", "one")
    control.perform(owner, account, "databases", "create_database", "two")
    with pytest.raises(Exception, match="limit"):
        control.perform(owner, account, "databases", "create_database", "three")


def test_another_customer_cannot_use_the_database_of_this_account(live, make_user):
    manager, c, account, ref = live
    from apps.core.exceptions import ServiceError

    with pytest.raises(ServiceError):
        control.perform(make_user("stranger@example.com"), account, "databases", "list_databases")


def test_editing_rows_and_free_sql_are_honestly_not_offered(live):
    manager, c, account, ref = live
    adapter = control.get_adapter(account.server)
    for call in (lambda: adapter.run_query(account.username, "x", "select 1"), lambda: adapter.insert_row(account.username, "x", "t", {}),
                 lambda: adapter.update_row(account.username, "x", "t", {}, {}), lambda: adapter.delete_row(account.username, "x", "t", {})):
        with pytest.raises(HostingError, match="not available"):
            call()


def test_row_data_is_not_kept_in_wh_panels_database_after_it_is_read(live):
    manager, c, account, ref = live
    owner = owner_of(c)
    prefix = account.username + "_"
    control.perform(owner, account, "databases", "create_database", "shop")
    ref.machine.engine.add_table(prefix + "shop", "people", [("name", "text", True, False, None)], [["Secret Person"]])
    control.perform(owner, account, "databases", "browse_table", prefix + "shop", "people", 0, 10)
    assert not any("Secret Person" in json.dumps(j.result_data) for j in AgentJob.objects.all())
    assert not any("Secret Person" in json.dumps(e.metadata) for e in AuditEvent.objects.all())


def test_passwords_are_not_kept_in_finished_jobs_or_the_audit_trail(live):
    manager, c, account, ref = live
    owner = owner_of(c)
    control.perform(owner, account, "databases", "create_database_user", "bob", "correct horse battery")
    job = AgentJob.objects.filter(command="CREATE_DB_USER").get()
    assert job.status == JobStatus.SUCCEEDED and job.params["password"] == "***"
    assert "correct horse" not in json.dumps([e.metadata for e in AuditEvent.objects.all()])


def test_when_the_node_does_not_answer_nothing_is_left_queued(live):
    manager, c, account, ref = live
    owner = owner_of(c)
    agent.inline_agent = None
    agent.POLL_INTERVAL = 0.01
    original = agent.call
    try:
        with pytest.raises(Exception, match="did not answer"):
            agent.call(account.server, "DB_INVENTORY", {"account": account.username}, wait=0.05)
    finally:
        agent.POLL_INTERVAL = 0.25
    job = AgentJob.objects.filter(command="DB_INVENTORY").latest("id")
    assert job.status == JobStatus.CANCELLED and original is agent.call


def test_a_slow_job_that_already_started_is_not_cancelled(live):
    manager, c, account, ref = live
    node = AgentNode.objects.get(server=account.server)
    agent.inline_agent = lambda: agent.poll(node, "0.1.0")  # the node takes the job and then never answers
    agent.POLL_INTERVAL = 0.01
    try:
        with pytest.raises(Exception, match="may still be made"):
            agent.call(account.server, "DB_INVENTORY", {"account": account.username}, wait=0.05)
    finally:
        agent.POLL_INTERVAL = 0.25
    assert AgentJob.objects.filter(command="DB_INVENTORY").latest("id").status == JobStatus.LEASED


def test_long_polling_waits_for_work_and_gives_up_politely(live, client):
    manager, c, account, ref = live
    from django.test import Client

    headers = {"HTTP_AUTHORIZATION": "Agent " + ref.token, "HTTP_X_AGENT_VERSION": "0.1.0"}
    reply = Client().post("/api/agent/v1/poll/?wait=1", data="{}", content_type="application/json", **headers)
    assert reply.status_code == 200 and reply.json() == {"job": None}
    assert Client().post("/api/agent/v1/poll/?wait=abc", data="{}", content_type="application/json", **headers).status_code == 200


def test_the_catalog_knows_which_answers_hold_customer_data():
    ephemeral = {n for n, c in catalog.COMMANDS.items() if c.ephemeral}
    assert {"DB_INVENTORY", "DB_TABLES", "DB_DESCRIBE", "DB_BROWSE"} <= ephemeral
    assert ephemeral - {"DB_INVENTORY", "DB_TABLES", "DB_DESCRIBE", "DB_BROWSE"} <= {c for c in catalog.COMMANDS if c.startswith("FILE_")}  # the others are file listings and contents
    assert not catalog.COMMANDS["BACKUP_DATABASE"].idempotent and not catalog.COMMANDS["RESTORE_DATABASE"].idempotent
    _ = timedelta
