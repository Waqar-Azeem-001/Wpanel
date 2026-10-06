"""Databases (H3): the demo provider's rules, and the customer screen end to end."""
import pytest
from django.core.management import call_command

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.core.exceptions import ServiceError
from apps.hosting import control
from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.capabilities import DATABASE_PRIVILEGES
from apps.hosting.adapters.demo_databases import DemoDatabases
from apps.hosting.models import HostingAccount
from apps.products import services as product_services
from apps.products.models import ProductType

pytestmark = pytest.mark.django_db

GOOD = "Sup3rSecretPass"


# --- The provider's rules (no web, no database) -----------------------------------------------------------------

@pytest.fixture
def svc(tmp_path):
    return DemoDatabases(tmp_path / "state", "acct")


def test_names_get_the_account_prefix_and_must_be_plain(svc):
    svc.create_database("shop")
    svc.create_database("acct_blog")  # typing the prefix yourself does not double it
    assert [d.name for d in svc.databases()] == ["acct_blog", "acct_shop"]
    for bad in ("", "Shop-1", "a b", "x" * 41, "../x", "sh;op"):
        with pytest.raises(HostingError):
            svc.create_database(bad)
    with pytest.raises(HostingError):
        svc.create_database("shop")  # already exists


def test_the_plan_limits_are_enforced_by_the_provider(svc):
    limits = svc.limits()
    for i in range(limits.max_databases):
        svc.create_database(f"db{i}")
    with pytest.raises(HostingError, match="allows"):
        svc.create_database("onetoomany")
    for i in range(limits.max_users):
        svc.create_user(f"u{i}", GOOD)
    with pytest.raises(HostingError, match="allows"):
        svc.create_user("onetoomany", GOOD)


@pytest.mark.parametrize("password", ["short1A", "nodigitsatallhere", "1234567890123", "has space 1234Ab", "x" * 200 + "1"])
def test_weak_passwords_are_refused(svc, password):
    with pytest.raises(HostingError):
        svc.create_user("web", password)
    assert svc.users() == []


def test_passwords_are_kept_only_as_a_hash(svc):
    svc.create_user("web", GOOD)
    assert svc.password_matches("acct_web", GOOD) and not svc.password_matches("acct_web", "Wrong1234567890")
    assert GOOD not in svc.file.read_text(encoding="utf-8")
    svc.set_password("acct_web", "An0therStrongOne")
    assert svc.password_matches("acct_web", "An0therStrongOne") and not svc.password_matches("acct_web", GOOD)
    with pytest.raises(HostingError):
        svc.set_password("acct_nobody", GOOD)


def test_grants_validate_privileges_and_deleting_cleans_up(svc):
    svc.create_database("shop")
    svc.create_user("web", GOOD)
    svc.grant("acct_shop", "acct_web", ["select", "insert"])
    assert [(g.user, g.database, g.privileges) for g in svc.grants()] == [("acct_web", "acct_shop", ["select", "insert"])]
    assert svc.databases()[0].users == ["acct_web"] and svc.users()[0].databases == ["acct_shop"]
    with pytest.raises(HostingError):
        svc.grant("acct_shop", "acct_web", ["select", "super"])  # not a customer privilege
    with pytest.raises(HostingError):
        svc.grant("acct_missing", "acct_web", ["select"])
    svc.grant("acct_shop", "acct_web", [])  # an empty list takes the access away
    assert svc.grants() == []
    svc.grant("acct_shop", "acct_web", list(DATABASE_PRIVILEGES))
    svc.delete_user("acct_web")
    assert svc.grants() == []
    svc.delete_database("acct_shop")
    assert svc.databases() == []


# --- The screen -------------------------------------------------------------------------------------------------

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


def url(account, tail=""):
    return f"/account/hosting/{account.pk}/manage/databases/{tail}"


def test_the_page_starts_empty_with_counts_and_the_prefix(client, owner, account):
    client.force_login(owner)
    page = client.get(url(account)).content.decode()
    assert "MySQL databases" in page and "demoshop_" in page and "0 / 10 databases" in page.replace("\n", " ")
    assert "You have no databases yet" in page and "No database users yet" in page


def test_create_database_user_grant_change_password_and_clean_up(client, owner, account):
    client.force_login(owner)
    act = url(account, "do/")
    client.post(act, {"action": "create_database", "name": "shop"})
    client.post(act, {"action": "create_user", "name": "webuser", "password": GOOD})
    page = client.get(url(account)).content.decode()
    assert "demoshop_shop" in page and "demoshop_webuser" in page and "No one yet" in page
    client.post(act, {"action": "grant", "database": "demoshop_shop", "user": "demoshop_webuser", "all": "1"})
    page = client.get(url(account)).content.decode()
    assert "All privileges" in page and "Remove access" in page
    client.post(act, {"action": "grant", "database": "demoshop_shop", "user": "demoshop_webuser",
                      "privileges": ["select", "update"]})
    assert "SELECT, UPDATE" in client.get(url(account)).content.decode()
    client.post(act, {"action": "set_password", "user": "demoshop_webuser", "password": "Brand-new-Pass1"})
    client.post(act, {"action": "revoke", "database": "demoshop_shop", "user": "demoshop_webuser"})
    assert "Remove access" not in client.get(url(account)).content.decode()
    client.post(act, {"action": "delete_user", "user": "demoshop_webuser"})
    client.post(act, {"action": "delete_database", "database": "demoshop_shop"})
    page = client.get(url(account)).content.decode()
    assert "demoshop_shop" not in page and "demoshop_webuser" not in page


def test_errors_are_shown_not_raised(client, owner, account):
    client.force_login(owner)
    act = url(account, "do/")
    response = client.post(act, {"action": "create_user", "name": "web", "password": "weak"}, follow=True)
    assert "Use a password of 12 to 128 characters" in response.content.decode()
    response = client.post(act, {"action": "create_database", "name": "Bad Name!"}, follow=True)
    assert "lowercase letters" in response.content.decode()
    response = client.post(act, {"action": "grant", "database": "x", "user": "y"}, follow=True)
    assert "Choose at least one privilege" in response.content.decode()
    assert client.post(act, {"action": "nonsense"}, follow=True).status_code == 200


def test_the_limit_disables_the_form_and_says_why(client, owner, account):
    client.force_login(owner)
    for i in range(10):
        client.post(url(account, "do/"), {"action": "create_database", "name": f"d{i}"})
    page = client.get(url(account)).content.decode()
    assert "reached your plan" in page and "10 / 10 databases" in page.replace("\n", " ")


def test_passwords_never_reach_the_audit_trail_or_the_page(client, owner, account):
    client.force_login(owner)
    secret = "Hunter2-Is-A-Terrible1"
    client.post(url(account, "do/"), {"action": "create_user", "name": "web", "password": secret})
    client.post(url(account, "do/"), {"action": "set_password", "user": "demoshop_web", "password": secret + "x"})
    events = list(AuditEvent.objects.filter(action="hosting.control").values_list("metadata", flat=True))
    assert len(events) == 2 and secret not in str(events)
    assert secret not in client.get(url(account)).content.decode()


def test_a_stranger_gets_nothing_and_changes_nothing(client, customer, account):
    client.force_login(customer)
    assert client.get(url(account)).status_code == 404
    assert client.post(url(account, "do/"), {"action": "create_database", "name": "evil"}).status_code == 404


def test_an_account_without_databases_gets_the_not_available_page(client, owner, account):
    from apps.products.models import Server

    Server.objects.filter(kind="demo").update(kind="manual")
    client.force_login(owner)
    assert "not available for your hosting server yet" in client.get(url(account)).content.decode()
    with pytest.raises(ServiceError):
        control.perform(owner, account, "databases", "list_databases")


# --- Browsing data (phpMyAdmin or its equivalent) and usage ------------------------------------------------------

def test_the_provider_offers_an_embedded_browser_with_sample_tables(owner, account):
    control.perform(owner, account, "databases", "create_database", "shop")
    control.perform(owner, account, "databases", "create_database", "blog_wp")
    access = control.perform(owner, account, "databases", "database_admin_access", "demoshop_shop")
    assert access.kind == "embedded" and access.url == ""
    assert [t.name for t in control.perform(owner, account, "databases", "list_tables", "demoshop_shop")] == [
        "customers", "orders", "products"]
    assert [t.name for t in control.perform(owner, account, "databases", "list_tables", "demoshop_blog_wp")] == [
        "wp_options", "wp_posts", "wp_users"]
    data = control.perform(owner, account, "databases", "browse_table", "demoshop_shop", "customers", 0, 5)
    assert data.columns == ["id", "name", "email", "city"] and len(data.rows) == 5 and data.total >= 12


def test_browsing_refuses_unknown_databases_tables_and_bad_pages(owner, account):
    control.perform(owner, account, "databases", "create_database", "shop")
    for args in (("demoshop_missing", "customers", 0, 5), ("demoshop_shop", "nope", 0, 5),
                 ("demoshop_shop", "customers", -1, 5), ("demoshop_shop", "customers", "x", 5),
                 ("demoshop_shop", "customers; DROP TABLE x", 0, 5)):
        with pytest.raises(ServiceError):
            control.perform(owner, account, "databases", "browse_table", *args)
    with pytest.raises(ServiceError):
        control.perform(owner, account, "databases", "database_admin_access", "demoshop_missing")
    assert len(control.perform(owner, account, "databases", "browse_table", "demoshop_shop", "orders", 0, 9999).rows) <= 100


def test_the_browse_screen_lists_tables_pages_rows_and_stays_read_only(client, owner, account):
    client.force_login(owner)
    client.post(url(account, "do/"), {"action": "create_database", "name": "shop"})
    page = client.get(url(account)).content.decode()
    assert "Browse data" in page and "Storage used by your databases" in page and "MB used" in page
    browse = url(account, "browse/")
    html = client.get(browse, {"database": "demoshop_shop"}).content.decode()
    assert "customers" in html and "orders" in html and "products" in html
    assert "Insert row" in html and "SQL" in html
    second = client.get(browse, {"database": "demoshop_shop", "table": "customers", "page": 2}).content.decode()
    assert "Page 2" in second and "Previous" in second
    assert client.get(browse, {"database": "demoshop_shop", "table": "customers", "page": "abc"}).status_code == 200
    assert client.get(browse, {"database": "nope"}, follow=True).status_code == 200


def test_cell_values_are_escaped_and_long_ones_are_cut(client, owner, account, monkeypatch):
    from apps.hosting.adapters.capabilities import TableData
    from apps.hosting.adapters.demo_sql import SqlDatabase

    client.force_login(owner)
    client.post(url(account, "do/"), {"action": "create_database", "name": "shop"})
    monkeypatch.setattr(SqlDatabase, "browse",
                        lambda self, *a: TableData(columns=["x"], rows=[["<script>alert(1)</script>"], ["y" * 400]], total=2))
    html = client.get(url(account, "browse/"), {"database": "demoshop_shop", "table": "customers"}).content.decode()
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert "y" * 400 not in html and "y" * 120 + "..." in html


def test_an_external_manager_redirects_only_to_a_secure_address(client, owner, account, monkeypatch):
    from apps.hosting.adapters.capabilities import DatabaseAdminAccess
    from apps.hosting.adapters.demo import DemoAdapter

    client.force_login(owner)
    client.post(url(account, "do/"), {"action": "create_database", "name": "shop"})
    monkeypatch.setattr(DemoAdapter, "database_admin_access",
                        lambda self, u, d: DatabaseAdminAccess("external", "https://server.example/pma?once=1"))
    response = client.get(url(account, "browse/"), {"database": "demoshop_shop"})
    assert response.status_code == 302 and response["Location"] == "https://server.example/pma?once=1"
    assert "phpMyAdmin" in client.get(url(account)).content.decode()
    monkeypatch.setattr(DemoAdapter, "database_admin_access",
                        lambda self, u, d: DatabaseAdminAccess("external", "http://plain.example/pma"))
    refused = client.get(url(account, "browse/"), {"database": "demoshop_shop"})
    assert refused.status_code == 302 and refused["Location"].endswith("/manage/databases/")  # never an insecure address


def test_a_stranger_cannot_browse_anything(client, customer, account):
    client.force_login(customer)
    assert client.get(url(account, "browse/"), {"database": "demoshop_shop"}).status_code == 404


# --- The SQL engine and the database manager (edit rows, structure, SQL) ------------------------------------------

@pytest.fixture
def sqldb(tmp_path):
    from apps.hosting.adapters.demo_databases import sample_tables
    from apps.hosting.adapters.demo_sql import SqlDatabase

    db = SqlDatabase(tmp_path / "x.sqlite3")
    db.seed(sample_tables("shop"))
    return db


def test_sql_select_returns_columns_rows_and_a_count(sqldb):
    result = sqldb.run("SELECT id, name FROM customers ORDER BY id LIMIT 3;")
    assert result.columns == ["id", "name"] and len(result.rows) == 3 and not result.truncated
    assert sqldb.run("SELECT COUNT(*) AS n FROM orders").rows[0][0].isdigit()


def test_sql_can_create_change_and_drop_things(sqldb):
    assert "affected" in sqldb.run("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)").message
    assert sqldb.run("INSERT INTO notes (body) VALUES ('hello')").affected == 1
    assert sqldb.run("UPDATE notes SET body = 'bye'").affected == 1
    assert sqldb.run("SELECT body FROM notes").rows == [["bye"]]
    sqldb.run("DROP TABLE notes")
    assert "notes" not in [t.name for t in sqldb.tables()]


def test_sql_results_are_capped_and_binary_is_not_dumped(sqldb):
    from apps.hosting.adapters import demo_sql

    sqldb.run("CREATE TABLE big (id INTEGER PRIMARY KEY, v TEXT)")
    for start in range(0, 300, 100):
        sqldb.run("INSERT INTO big (v) " + " UNION ALL ".join(f"SELECT {n}" for n in range(start, start + 100)))
    capped = sqldb.run("SELECT * FROM big")
    assert len(capped.rows) == demo_sql.MAX_RESULT_ROWS and capped.truncated
    assert sqldb.run("SELECT x'00ff00' AS b").rows == [["[binary 3 bytes]"]]


@pytest.mark.parametrize("sql", ["ATTACH DATABASE 'other.db' AS o", "PRAGMA writable_schema = 1", "PRAGMA table_info(customers)",
                                 "DELETE FROM sqlite_master", "SELECT load_extension('x')", "SELECT readfile('/etc/passwd')",
                                 "CREATE VIRTUAL TABLE v USING fts5(a)", "DETACH DATABASE main"])
def test_dangerous_sql_is_refused(sqldb, sql):
    with pytest.raises(HostingError):
        sqldb.run(sql)
    assert "customers" in [t.name for t in sqldb.tables()]  # nothing was harmed


def test_one_statement_empty_and_oversized_sql_are_refused(sqldb):
    for sql in ("SELECT 1; DROP TABLE customers", "", "   ;  ", "SELECT " + "1," * 20000 + "1"):
        with pytest.raises(HostingError):
            sqldb.run(sql)
    assert "customers" in [t.name for t in sqldb.tables()]


def test_a_runaway_query_is_stopped(sqldb, monkeypatch):
    from apps.hosting.adapters import demo_sql

    monkeypatch.setattr(demo_sql, "TIME_LIMIT_SECONDS", 0.05)
    with pytest.raises(HostingError, match="too long"):
        sqldb.run("WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r) SELECT COUNT(*) FROM r")


def test_the_database_has_a_size_cap(tmp_path, monkeypatch):
    from apps.hosting.adapters import demo_sql

    monkeypatch.setattr(demo_sql, "MAX_DB_BYTES", 40_000)
    db = demo_sql.SqlDatabase(tmp_path / "small.sqlite3")
    db.seed({"t": (["id", "v"], [])})
    with pytest.raises(HostingError, match="size limit"):
        for _ in range(200):
            db.run("INSERT INTO t (v) VALUES (hex(randomblob(2000)))")


def test_row_editing_uses_names_and_values_safely(sqldb):
    columns = [c.name for c in sqldb.describe("customers")]
    assert columns == ["id", "name", "email", "city"] and sqldb.describe("customers")[0].primary_key
    sqldb.insert_row("customers", {"name": "Zed'; DROP TABLE customers;--", "email": "z@example.com", "city": ""})
    assert "customers" in [t.name for t in sqldb.tables()]
    last = sqldb.browse("customers", 0, 100).rows[-1]
    assert last[1] == "Zed'; DROP TABLE customers;--"
    sqldb.update_row("customers", {"id": last[0]}, {"city": "Multan"})
    assert sqldb.browse("customers", 0, 100).rows[-1][3] == "Multan"
    sqldb.delete_row("customers", {"id": last[0]})
    assert sqldb.browse("customers", 0, 100).rows[-1][0] != last[0]
    for bad in (lambda: sqldb.insert_row("customers", {"nope": "x"}), lambda: sqldb.insert_row("nope", {"a": "b"}),
                lambda: sqldb.update_row("customers", {"name": "x"}, {"city": "y"}),  # not the primary key
                lambda: sqldb.update_row("customers", {"id": "999999"}, {"city": "y"}),
                lambda: sqldb.delete_row("customers", {"id": "999999"}),
                lambda: sqldb.insert_row("customers", {})):
        with pytest.raises(HostingError):
            bad()


def test_a_table_without_a_primary_key_has_read_only_rows(sqldb):
    sqldb.run("CREATE TABLE loose (a TEXT)")
    sqldb.run("INSERT INTO loose VALUES ('x')")
    assert sqldb.browse("loose", 0, 10).key_columns == []
    with pytest.raises(HostingError, match="primary key"):
        sqldb.delete_row("loose", {"a": "x"})


def test_null_stays_distinct_from_the_text_null(sqldb):
    sqldb.run("INSERT INTO customers (id, name) VALUES (500, 'NULL')")
    row = [r for r in sqldb.browse("customers", 0, 100).rows if r[0] == "500"][0]
    assert row[1] == "NULL" and row[2] is None


def test_each_database_is_its_own_file_and_deleting_removes_it(svc):
    svc.create_database("one")
    svc.create_database("two")
    svc.sql("acct_one").run("CREATE TABLE only_in_one (id INTEGER PRIMARY KEY)")
    assert "only_in_one" not in [t.name for t in svc.sql("acct_two").tables()]
    path = svc.sql("acct_one").path
    assert path.exists()
    svc.delete_database("acct_one")
    assert not path.exists()


# --- The manager screens -------------------------------------------------------------------------------------------

def manager_url(account, **query):
    from urllib.parse import urlencode

    return url(account, "browse/") + "?" + urlencode(query)


def test_the_manager_has_browse_structure_and_sql_tabs(client, owner, account):
    client.force_login(owner)
    client.post(url(account, "do/"), {"action": "create_database", "name": "shop"})
    browse = client.get(manager_url(account, database="demoshop_shop", table="customers")).content.decode()
    assert "Browse" in browse and "Structure" in browse and "SQL" in browse and "Insert row" in browse
    assert "data-db-edit" in browse and 'data-columns="' in browse
    structure = client.get(manager_url(account, database="demoshop_shop", table="customers", tab="structure")).content.decode()
    assert "Primary" in structure and "INTEGER" in structure and "email" in structure
    sql = client.get(manager_url(account, database="demoshop_shop", table="customers", tab="sql")).content.decode()
    assert "Run SQL" in sql and "<textarea" in sql


def test_running_sql_shows_rows_or_a_message_and_errors_are_shown(client, owner, account):
    client.force_login(owner)
    client.post(url(account, "do/"), {"action": "create_database", "name": "shop"})
    act = url(account, "sql/")
    base = {"database": "demoshop_shop", "table": "customers"}
    page = client.post(act, {**base, "sql": "SELECT name FROM customers LIMIT 2"}).content.decode()
    assert "2 row(s) returned" in page and "Ayesha Khan" in page
    page = client.post(act, {**base, "sql": "UPDATE customers SET city = 'X' WHERE id = 1"}).content.decode()
    assert "1 row(s) affected" in page
    page = client.post(act, {**base, "sql": "SELEKT nonsense"}).content.decode()
    assert "SQL error" in page and "SELEKT nonsense" in page  # the typed statement is kept for fixing
    page = client.post(act, {**base, "sql": "ATTACH DATABASE 'x' AS y"}).content.decode()
    assert "SQL error" in page or "not authorized" in page.lower()
    assert client.post(act, {**base, "sql": "<script>alert(1)</script>"}).status_code == 200


def test_sql_statements_are_audited_by_kind_and_never_by_text(client, owner, account):
    client.force_login(owner)
    client.post(url(account, "do/"), {"action": "create_database", "name": "shop"})
    secret = "INSERT INTO customers (id, name) VALUES (777, 'top-secret-value-xyz')"
    client.post(url(account, "sql/"), {"database": "demoshop_shop", "table": "customers", "sql": secret})
    events = list(AuditEvent.objects.filter(action="hosting.control", metadata__operation="run_query")
                  .values_list("metadata", flat=True))
    assert events and events[0]["kind"] == "INSERT" and "top-secret-value-xyz" not in str(events)


def test_insert_edit_and_delete_a_row_from_the_screen(client, owner, account):
    client.force_login(owner)
    client.post(url(account, "do/"), {"action": "create_database", "name": "shop"})
    act = url(account, "row/")
    base = {"database": "demoshop_shop", "table": "customers", "page": "1"}
    client.post(act, {**base, "action": "insert", "val__id": "900", "val__name": "New Person", "val__email": "n@e.com"})
    page = client.get(manager_url(account, database="demoshop_shop", table="customers", page=2)).content.decode()
    assert "New Person" in page or "New Person" in client.get(manager_url(account, database="demoshop_shop", table="customers", page=1)).content.decode()
    client.post(act, {**base, "action": "update", "key__id": "900", "val__name": "Changed Person"})
    found = client.post(url(account, "sql/"), {"database": "demoshop_shop", "table": "customers",
                                               "sql": "SELECT name FROM customers WHERE id = 900"}).content.decode()
    assert "Changed Person" in found
    client.post(act, {**base, "action": "delete", "key__id": "900"})
    gone = client.post(url(account, "sql/"), {"database": "demoshop_shop", "table": "customers",
                                              "sql": "SELECT name FROM customers WHERE id = 900"}).content.decode()
    assert "0 row(s) returned" in gone


def test_row_errors_are_messages_and_values_are_escaped(client, owner, account):
    client.force_login(owner)
    client.post(url(account, "do/"), {"action": "create_database", "name": "shop"})
    response = client.post(url(account, "row/"), {"database": "demoshop_shop", "table": "customers", "action": "delete",
                                                  "key__id": "424242"}, follow=True)
    assert "no longer exists" in response.content.decode()
    client.post(url(account, "row/"), {"database": "demoshop_shop", "table": "customers", "action": "insert",
                                       "val__id": "901", "val__name": "<script>alert(1)</script>"})
    html = client.post(url(account, "sql/"), {"database": "demoshop_shop", "table": "customers",
                                              "sql": "SELECT name FROM customers WHERE id = 901"}).content.decode()
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_a_stranger_cannot_run_sql_or_edit_rows(client, customer, account):
    client.force_login(customer)
    assert client.post(url(account, "sql/"), {"database": "demoshop_shop", "sql": "SELECT 1"}).status_code == 404
    assert client.post(url(account, "row/"), {"database": "demoshop_shop", "table": "customers", "action": "delete",
                                              "key__id": "1"}).status_code == 404
