"""
The cPanel adapter's customer tools (H14a: DNS, MySQL, email, PHP), tested against a FAKE WHM that answers the way cPanel's documentation says.

What these tests prove: the right WHM/UAPI function and parameters are sent, answers in the documented shape are read correctly,
ownership of every domain and name is checked first, secrets travel in a POST body, and the tools stay off until staff enable them.
What they cannot prove: that a real cPanel server answers exactly like this fake. That needs a staging server (docs/h14-cpanel-adapter.md).
"""
import pytest
from django.test import override_settings

from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.capabilities import CapabilityNotSupported, DnsRecord, supported_capabilities
from apps.hosting.adapters.cpanel_php import package_of, version_of
from apps.hosting.adapters.whm_api import WhmApiAdapter

USER = "acme"


class FakeWhm:
    """Stands in for ``WhmApiAdapter._call``: records every call and answers from a table of (function, module::function) -> data."""

    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or {}

    def __call__(self, function, params=None, *, sensitive=False, raw=False):
        params = dict(params or {})
        self.calls.append((function, params, sensitive))
        if function == "uapi_cpanel":
            key = (function, f"{params['cpanel.module']}::{params['cpanel.function']}")
        elif function == "cpanel":
            key = (function, f"{params['cpanel_jsonapi_module']}::{params['cpanel_jsonapi_func']}")
        else:
            key = (function, None)
        answer = self.answers.get(key, self.answers.get(function))
        if isinstance(answer, Exception):
            raise answer
        if function == "uapi_cpanel":
            if isinstance(answer, dict) and answer.get("__uapi_error__"):
                return {"data": {"uapi": {"status": 0, "errors": [answer["__uapi_error__"]], "data": None}}}
            return {"data": {"uapi": {"status": 1, "errors": None, "data": answer}}}
        if function == "cpanel":
            return {"cpanelresult": {"data": answer, "event": {"result": 1}}}
        return {"metadata": {"result": 1}, "data": answer}

    def api2_calls(self, module=None, function=None):
        return [(p, s) for f, p, s in self.calls if f == "cpanel" and (module is None or p["cpanel_jsonapi_module"] == module)
                and (function is None or p["cpanel_jsonapi_func"] == function)]

    def uapi_calls(self, module=None, function=None):
        return [(p, s) for f, p, s in self.calls if f == "uapi_cpanel" and (module is None or p["cpanel.module"] == module)
                and (function is None or p["cpanel.function"] == function)]


DOMAINS = {"main_domain": {"domain": "acme.com"}, "addon_domains": [{"domain": "acme-shop.com"}],
           "sub_domains": [{"domain": "blog.acme.com"}], "parked_domains": ["acme.net"]}


def adapter(answers=None):
    answers = {("uapi_cpanel", "DomainInfo::domains_data"): DOMAINS, **(answers or {})}
    fake = FakeWhm(answers)
    real = WhmApiAdapter(host="whm.test", port=2087, credentials={"api_username": "root", "api_token": "t"})
    real._call = fake
    return real, fake


# --- Off until staff turn it on ---------------------------------------------------------------------------------------

def test_no_customer_tool_is_offered_until_staff_enable_it():
    real, _ = adapter()
    assert {c.value for c in supported_capabilities(real)} == {"account", "usage"}


@override_settings(HOSTING_CPANEL_CAPABILITIES=["dns", "php", "nonsense"])
def test_enabled_tools_appear_one_by_one_and_unknown_names_are_ignored():
    real, _ = adapter()
    assert {c.value for c in supported_capabilities(real)} == {"account", "usage", "dns", "php"}


@override_settings(HOSTING_CPANEL_CAPABILITIES=["files", "domains", "dns", "ssl", "databases", "email", "php", "cron", "backups", "cpanel_access"])
def test_every_enabled_cpanel_tool_goes_through_the_normal_gateway_checks():
    from apps.hosting.adapters.capabilities import OPERATIONS

    real, _ = adapter()
    for capability in supported_capabilities(real):
        for operation in OPERATIONS[capability]:
            assert callable(getattr(real, operation, None)), (capability, operation)


# --- The UAPI plumbing ----------------------------------------------------------------------------------------------------

def test_a_uapi_call_runs_as_the_account_through_uapi_cpanel_and_returns_its_data():
    real, fake = adapter({("uapi_cpanel", "Mysql::get_restrictions"): {"prefix": "acme_"}})
    assert real._prefix(USER) == "acme_"
    (params, secret), = fake.uapi_calls("Mysql")
    assert params["cpanel.user"] == USER and params["cpanel.function"] == "get_restrictions" and secret is False


def test_a_uapi_failure_becomes_a_plain_error_and_a_strange_answer_is_refused():
    real, _ = adapter({("uapi_cpanel", "Mysql::get_restrictions"): {"__uapi_error__": "Access   denied\n for  user"}})
    with pytest.raises(HostingError, match="Access denied for user"):
        real._prefix(USER)
    real._call = lambda *a, **k: {"data": {"unexpected": 1}, "metadata": {"result": 1}}
    with pytest.raises(HostingError, match="do not understand"):
        real._prefix(USER)


# --- DNS ------------------------------------------------------------------------------------------------------------------

ZONE = [{"Line": 1, "type": "A", "name": "acme.com.", "ttl": 14400, "address": "203.0.113.5"},
        {"Line": 2, "type": "CNAME", "name": "www.acme.com.", "ttl": 3600, "cname": "acme.com."},
        {"Line": 3, "type": "MX", "name": "acme.com.", "ttl": 14400, "exchange": "mail.acme.com.", "preference": 10},
        {"Line": 4, "type": "TXT", "name": "acme.com.", "ttl": 300, "txtdata": ["v=spf1 ", "+a ~all"]},
        {"Line": 5, "type": "SRV", "name": "_sip._tcp.acme.com.", "ttl": 3600, "priority": 5, "weight": 10, "port": 5060, "target": "sip.acme.com."},
        {"Line": 6, "type": "CAA", "name": "acme.com.", "ttl": 3600, "flag": 0, "tag": "issue", "value": "letsencrypt.org"},
        {"Line": 7, "type": "SOA", "name": "acme.com.", "ttl": 86400}]


def dns():
    return adapter({"dumpzone": {"zone": [{"record": ZONE}]}})


def test_the_zone_is_read_into_neutral_records_and_the_soa_is_left_out():
    real, fake = dns()
    records = {r.type: r for r in real.list_dns_records(USER, "acme.com")}
    assert set(records) == {"A", "CNAME", "MX", "TXT", "SRV", "CAA"}
    assert (records["A"].name, records["A"].value, records["A"].record_id) == ("@", "203.0.113.5", "1:A:@")
    assert (records["CNAME"].name, records["CNAME"].value) == ("www", "acme.com")
    assert (records["MX"].value, records["MX"].priority) == ("mail.acme.com", 10)
    assert records["TXT"].value == "v=spf1 +a ~all" and records["TXT"].ttl == 300
    assert (records["SRV"].name, records["SRV"].value, records["SRV"].priority) == ("_sip._tcp", "10 5060 sip.acme.com", 5)
    assert records["CAA"].value == '0 issue "letsencrypt.org"'
    assert fake.calls[-1][0] == "dumpzone" and fake.calls[-1][1] == {"domain": "acme.com"}


def test_only_zones_the_account_owns_can_be_read_or_changed():
    real, fake = dns()
    for domain in ("victim.com", "blog.acme.com", ""):
        for call in (lambda d: real.list_dns_records(USER, d), lambda d: real.add_dns_record(USER, d, DnsRecord("", "A", "x", "1.2.3.4")),
                     lambda d: real.delete_dns_record(USER, d, "1:A:@")):
            with pytest.raises(HostingError, match="not on this account"):
                call(domain)
    assert not [c for c in fake.calls if c[0] in ("dumpzone", "addzonerecord", "editzonerecord", "removezonerecord")]
    real.list_dns_records(USER, "ACME.NET.")  # a parked domain is fine, case and a trailing dot are tidied


@pytest.mark.parametrize("record,expected", [
    (DnsRecord("", "A", "shop", "203.0.113.9", 3600), {"type": "A", "name": "shop.acme.com.", "address": "203.0.113.9", "ttl": 3600}),
    (DnsRecord("", "CNAME", "www", "acme.com", 3600), {"type": "CNAME", "name": "www.acme.com.", "cname": "acme.com"}),
    (DnsRecord("", "MX", "@", "mail.acme.com", 3600, 20), {"type": "MX", "name": "acme.com.", "exchange": "mail.acme.com", "preference": 20}),
    (DnsRecord("", "TXT", "_dmarc", "v=DMARC1; p=none", 300), {"type": "TXT", "name": "_dmarc.acme.com.", "txtdata": "v=DMARC1; p=none"}),
    (DnsRecord("", "SRV", "_sip._tcp", "5 5060 sip.acme.com", 3600, 10),
     {"type": "SRV", "priority": 10, "weight": "5", "port": "5060", "target": "sip.acme.com"}),
    (DnsRecord("", "CAA", "@", '0 issue "letsencrypt.org"', 3600), {"type": "CAA", "flag": "0", "tag": "issue", "value": "letsencrypt.org"}),
])
def test_adding_a_record_sends_the_right_whm_fields(record, expected):
    real, fake = dns()
    real.add_dns_record(USER, "acme.com", record)
    function, params, _ = fake.calls[-1]
    assert function == "addzonerecord" and params["domain"] == "acme.com" and params["class"] == "IN"
    assert expected.items() <= params.items()


@pytest.mark.parametrize("record", [DnsRecord("", "A", "x", "not-an-ip"), DnsRecord("", "CNAME", "@", "acme.com"), DnsRecord("", "MX", "@", "mail.acme.com"),
                                    DnsRecord("", "SOA", "@", "x"), DnsRecord("", "A", "bad name", "1.2.3.4"), DnsRecord("", "A", "x", "1.2.3.4", 5)])
def test_a_bad_record_is_refused_before_anything_is_sent(record):
    real, fake = dns()
    with pytest.raises(HostingError):
        real.add_dns_record(USER, "acme.com", record)
    assert not [c for c in fake.calls if c[0] == "addzonerecord"]


def test_editing_and_deleting_check_the_line_still_holds_that_record():
    real, fake = dns()
    real.update_dns_record(USER, "acme.com", DnsRecord("1:A:@", "A", "@", "203.0.113.77", 3600))
    function, params, _ = fake.calls[-1]
    assert function == "editzonerecord" and params["line"] == "1" and params["address"] == "203.0.113.77"
    real.delete_dns_record(USER, "acme.com", "2:CNAME:www")
    assert fake.calls[-1][0] == "removezonerecord" and fake.calls[-1][1] == {"domain": "acme.com", "line": "2"}
    for stale in ("2:A:@", "99:A:@", "1:A:other", "", "nonsense"):
        with pytest.raises(HostingError, match="zone changed"):
            real.delete_dns_record(USER, "acme.com", stale)
    with pytest.raises(HostingError, match="type of a record cannot be changed"):
        real.update_dns_record(USER, "acme.com", DnsRecord("1:A:@", "TXT", "@", "text", 300))


# --- MySQL ----------------------------------------------------------------------------------------------------------------

MYSQL = {("uapi_cpanel", "Mysql::get_restrictions"): {"prefix": "acme_"},
         ("uapi_cpanel", "Mysql::list_databases"): [{"database": "acme_shop", "disk_usage": 5242880, "users": ["acme_app"]}],
         ("uapi_cpanel", "Mysql::list_users"): [{"user": "acme_app", "shortuser": "app", "databases": ["acme_shop"]}],
         "accountsummary": {"acct": [{"maxsql": "10", "maxpop": "unlimited", "domain": "acme.com"}]}}


def test_databases_are_listed_with_size_users_and_limits():
    real, _ = adapter(MYSQL)
    assert [(d.name, d.size_mb, d.users) for d in real.list_databases(USER)] == [("acme_shop", 5.0, ["acme_app"])]
    assert [(u.name, u.databases) for u in real.list_database_users(USER)] == [("acme_app", ["acme_shop"])]
    limits = real.database_limits(USER)
    assert limits.name_prefix == "acme_" and limits.max_databases == 10


def test_names_get_the_account_prefix_and_must_be_plain():
    real, fake = adapter(MYSQL)
    real.create_database(USER, "blog")
    real.create_database(USER, "acme_forum")
    assert [p["name"] for p, _ in fake.uapi_calls("Mysql", "create_database")] == ["acme_blog", "acme_forum"]
    for bad in ("", "a b", "x;drop", "a/b", "é"):
        with pytest.raises(HostingError):
            real.create_database(USER, bad)


def test_only_the_accounts_own_databases_and_users_can_be_changed():
    real, fake = adapter(MYSQL)
    for call in (lambda: real.delete_database(USER, "other_db"), lambda: real.delete_database_user(USER, "other_user"),
                 lambda: real.set_database_user_password(USER, "other_user", "Strong-Pass-2026"),
                 lambda: real.grant_database_access(USER, "other_db", "app", ["SELECT"]),
                 lambda: real.grant_database_access(USER, "shop", "other_user", ["SELECT"])):
        with pytest.raises(HostingError, match="does not exist on this account"):
            call()
    assert not fake.uapi_calls("Mysql", "delete_database") and not fake.uapi_calls("Mysql", "delete_user")
    real.delete_database(USER, "shop")
    assert fake.uapi_calls("Mysql", "delete_database")[0][0]["name"] == "acme_shop"


def test_passwords_go_in_a_post_body_and_are_never_in_a_get():
    real, fake = adapter(MYSQL)
    real.create_database_user(USER, "newuser", "Strong-Pass-2026")
    real.set_database_user_password(USER, "app", "Strong-Pass-2026")
    secret_calls = [s for f, p, s in fake.calls if "password" in p]
    assert secret_calls == [True, True]


def test_grants_validate_the_privileges_and_can_revoke():
    real, fake = adapter(MYSQL)
    real.grant_database_access(USER, "shop", "app", ["select", "insert"])
    assert fake.uapi_calls("Mysql", "set_privileges_on_database")[-1][0]["privileges"] == "INSERT,SELECT"
    real.grant_database_access(USER, "shop", "app", ["ALL"])
    assert fake.uapi_calls("Mysql", "set_privileges_on_database")[-1][0]["privileges"] == "ALL PRIVILEGES"
    real.grant_database_access(USER, "shop", "app", [])
    assert fake.uapi_calls("Mysql", "revoke_access_to_database")
    with pytest.raises(HostingError, match="listed privileges"):
        real.grant_database_access(USER, "shop", "app", ["SELECT", "FILE"])


class _Reply:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def _tool(real, monkeypatch, answers):
    """Stand in for the server: writing/deleting the tool file, the address lookup and the HTTP call. ``answers`` are the JSON bodies, in order."""
    import socket

    import requests

    seen = {"written": [], "deleted": [], "calls": [], "configs": []}
    monkeypatch.setattr(real, "write_file", lambda user, path, content, overwrite=False: seen["written"].append((path, content.decode())))
    monkeypatch.setattr(real, "delete_path", lambda user, path: seen["deleted"].append(path))
    monkeypatch.setattr(real, "_account_domains", lambda user: ("acme.com", [], [], []))
    monkeypatch.setattr(socket, "gethostbyname", lambda host: "198.51.100.7")
    queue = list(answers)

    def get(url, params=None, headers=None, timeout=None, allow_redirects=True):
        seen["calls"].append((url, headers))
        import base64
        import json
        import re
        source = seen["written"][-1][1]
        seen["configs"].append(json.loads(base64.b64decode(re.search(r"base64_decode\('([^']+)'\)", source).group(1))))
        return _Reply(queue.pop(0))

    monkeypatch.setattr(requests, "get", get)
    return seen


def test_tables_come_from_the_one_time_tool_with_the_wordpress_credentials(monkeypatch):
    real, _ = adapter(MYSQL)
    seen = _tool(real, monkeypatch, [{"ok": True, "tables": [{"name": "wp_posts", "rows": 12, "size_kb": 96}]}])
    tables = real.list_tables(USER, "acme_shop")
    assert [(t.name, t.rows, t.size_kb) for t in tables] == [("wp_posts", 12, 96)]
    assert seen["calls"] == [("http" + "://198.51.100.7/" + seen["written"][0][0].split("/")[-1], {"Host": "acme.com"})]
    assert seen["configs"][0]["mode"] == "wpconfig" and seen["configs"][0]["db"] == "acme_shop"
    assert "password" not in seen["configs"][0]  # the website's own credentials are read there, never sent
    assert seen["deleted"] == [seen["written"][0][0]]  # tidied up even though the tool removes itself


def test_another_database_uses_a_temporary_user_that_is_always_deleted(monkeypatch):
    real, _ = adapter(MYSQL)
    seen = _tool(real, monkeypatch, [{"ok": False, "code": "no_wpconfig"}, {"ok": True, "columns": [{"name": "id", "type": "int(11)", "nullable": False, "primary_key": True, "default": None}]}])
    made, grants, dropped = [], [], []
    monkeypatch.setattr(real, "create_database_user", lambda user, name, password: made.append((name, password)))
    monkeypatch.setattr(real, "grant_database_access", lambda user, database, name, privileges: grants.append((database, name, privileges)))
    monkeypatch.setattr(real, "delete_database_user", lambda user, name: dropped.append(name))
    columns = real.describe_table(USER, "acme_shop", "orders")
    assert [(c.name, c.type, c.nullable, c.primary_key) for c in columns] == [("id", "int(11)", False, True)]
    assert len(made) == 1 and made[0][0].startswith("whp") and len(made[0][1]) >= 12
    assert grants == [("acme_shop", "acme_" + made[0][0], ["ALL"])] and dropped == ["acme_" + made[0][0]]
    assert seen["configs"][1]["mode"] == "user" and seen["configs"][1]["user"] == "acme_" + made[0][0]


def test_the_temporary_user_is_deleted_even_when_the_tool_fails(monkeypatch):
    real, _ = adapter(MYSQL)
    _tool(real, monkeypatch, [{"ok": False, "code": "no_wpconfig"}, {"ok": False, "code": "connect", "error": "The database refused the connection (1045)."}])
    dropped = []
    monkeypatch.setattr(real, "create_database_user", lambda *a: None)
    monkeypatch.setattr(real, "grant_database_access", lambda *a: None)
    monkeypatch.setattr(real, "delete_database_user", lambda user, name: dropped.append(name))
    with pytest.raises(HostingError, match="refused"):
        real.list_tables(USER, "acme_shop")
    assert len(dropped) == 1


def test_rows_and_typed_sql_are_read_with_limits(monkeypatch):
    real, _ = adapter(MYSQL)
    seen = _tool(real, monkeypatch, [{"ok": True, "columns": ["id", "title"], "rows": [["1", "Hello"]], "total": 41},
                                     {"ok": True, "columns": ["n"], "rows": [["3"]], "truncated": False, "affected": 0},
                                     {"ok": True, "columns": [], "rows": [], "truncated": False, "affected": 2}])
    page = real.browse_table(USER, "acme_shop", "wp_posts", 20, 5000)
    assert page.columns == ["id", "title"] and page.rows == [["1", "Hello"]] and page.total == 41 and page.key_columns == []
    assert seen["configs"][0]["offset"] == 20 and seen["configs"][0]["limit"] == 100  # never more than a page of 100
    result = real.run_query(USER, "acme_shop", "SELECT COUNT(*) AS n FROM wp_posts;")
    assert result.columns == ["n"] and result.rows == [["3"]] and seen["configs"][1]["sql"] == "SELECT COUNT(*) AS n FROM wp_posts"
    changed = real.run_query(USER, "acme_shop", "UPDATE wp_options SET option_value = 'x' WHERE option_id = 0")
    assert changed.affected == 2 and "2 row(s)" in changed.message
    with pytest.raises(HostingError, match="one statement"):
        real.run_query(USER, "acme_shop", "SELECT 1; DROP TABLE wp_posts")
    with pytest.raises(HostingError, match="Type one SQL"):
        real.run_query(USER, "acme_shop", "  ;  ")


def test_the_tool_checks_its_token_removes_itself_and_whitelists_tables():
    from apps.hosting.adapters import cpanel_dbtool

    name, token, source = cpanel_dbtool._build({"mode": "wpconfig", "db": "x", "action": "tables"})
    assert name.startswith("whp-db-") and token not in source  # only the hash is in the file
    assert "hash_equals" in source and "@unlink(__FILE__)" in source and "in_array($table, $names, true)" in source
    assert "str_replace('`', '``'" in source


def test_database_browsing_is_whpanels_own_and_row_editing_is_not_offered(monkeypatch):
    real, _ = adapter(MYSQL)
    assert real.database_admin_access(USER, "acme_shop").kind == "embedded"
    for call in (lambda: real.insert_row(USER, "shop", "t", {}), lambda: real.update_row(USER, "shop", "t", {}, {}), lambda: real.delete_row(USER, "shop", "t", {})):
        with pytest.raises(CapabilityNotSupported):
            call()


# --- Email ----------------------------------------------------------------------------------------------------------------

EMAIL = {("uapi_cpanel", "Email::list_pops_with_disk"): [{"email": "Info@acme.com", "diskused": "12.5", "diskquota": "1024"},
                                                          {"email": "big@acme.com", "diskused": "0", "diskquota": "unlimited"}],
         ("uapi_cpanel", "Email::list_forwarders"): [{"dest": "sales@acme.com", "forward": "me@gmail.test"}],
         "accountsummary": {"acct": [{"maxpop": "25", "domain": "acme.com"}]}}


def test_mailboxes_forwarders_and_limits_are_read():
    real, _ = adapter(EMAIL)
    boxes = {m.address: m for m in real.list_mailboxes(USER)}
    assert boxes["info@acme.com"].quota_mb == 1024 and boxes["info@acme.com"].used_mb == 12.5 and boxes["big@acme.com"].quota_mb is None
    assert {(f.source, f.destination) for f in real.list_forwarders(USER)} == {("sales@acme.com", "me@gmail.test")}
    limits = real.email_limits(USER)
    assert limits.domain == "acme.com" and limits.max_mailboxes == 25


def test_creating_changing_and_deleting_mailboxes_sends_the_right_fields():
    real, fake = adapter(EMAIL)
    real.create_mailbox(USER, "Sales@Acme.com", "Strong-Pass-2026", quota_mb=500)
    real.create_mailbox(USER, "x@acme-shop.com", "Strong-Pass-2026")
    first, second = [p for p, _ in fake.uapi_calls("Email", "add_pop")]
    assert (first["email"], first["domain"], first["quota"]) == ("sales", "acme.com", 500) and second["quota"] == 0
    real.set_mailbox_password(USER, "sales@acme.com", "Another-Pass-2027")
    real.set_mailbox_quota(USER, "sales@acme.com", None)
    real.delete_mailbox(USER, "sales@acme.com")
    assert fake.uapi_calls("Email", "edit_pop_quota")[0][0]["quota"] == 0
    assert [s for f, p, s in fake.calls if "password" in p] == [True, True, True]  # two new mailboxes and the changed password
    assert fake.uapi_calls("Email", "delete_pop")[0][0] == {"cpanel.user": USER, "cpanel.module": "Email", "cpanel.function": "delete_pop",
                                                            "email": "sales", "domain": "acme.com"}


@pytest.mark.parametrize("address", ["", "nodomain", "a@b@c.com", "a b@acme.com", "@acme.com", "x@victim.com"])
def test_other_peoples_domains_and_malformed_addresses_are_refused_before_anything_is_sent(address):
    real, fake = adapter(EMAIL)
    with pytest.raises(HostingError):
        real.create_mailbox(USER, address, "Strong-Pass-2026")
    assert not fake.uapi_calls("Email", "add_pop")


def test_forwarders_are_created_and_deleted_only_on_the_accounts_own_domains():
    real, fake = adapter(EMAIL)
    real.create_forwarder(USER, "sales@acme.com", "me@gmail.test")
    params = fake.uapi_calls("Email", "add_forwarder")[0][0]
    assert (params["domain"], params["email"], params["fwdopt"], params["fwd"]) == ("acme.com", "sales", "fwd", "me@gmail.test")
    real.delete_forwarder(USER, "sales@acme.com", "me@gmail.test")
    assert fake.uapi_calls("Email", "delete_forwarder")[0][0]["address"] == "sales@acme.com"
    with pytest.raises(HostingError):
        real.create_forwarder(USER, "sales@victim.com", "me@gmail.test")
    with pytest.raises(HostingError):
        real.create_forwarder(USER, "sales@acme.com", "not an address")


def test_automatic_replies_use_the_documented_fields_and_off_deletes_it():
    real, fake = adapter({**EMAIL, ("uapi_cpanel", "Email::list_auto_responders"): [{"email": "info@acme.com", "subject": "Away"}]})
    real.set_autoresponder(USER, "info@acme.com", "Away", "Back on Monday", True)
    add = fake.uapi_calls("Email", "add_auto_responder")[0][0]
    assert (add["email"], add["domain"], add["subject"], add["body"], add["from"]) == ("info", "acme.com", "Away", "Back on Monday", "info@acme.com")
    real.set_autoresponder(USER, "info@acme.com", "Away", "x", False)
    assert fake.uapi_calls("Email", "delete_auto_responder")[0][0]["email"] == "info@acme.com"
    assert [a.address for a in real.list_autoresponders(USER)][0] == "info@acme.com"


def test_reading_and_writing_mail_inside_whpanel_is_honestly_not_offered():
    real, _ = adapter(EMAIL)
    for call in (lambda: real.list_messages(USER, "info@acme.com"), lambda: real.webmail_access(USER, "info@acme.com"),
                 lambda: real.send_message(USER, "info@acme.com", "a@b.test", "s", "b"), lambda: real.get_spam_settings(USER),
                 lambda: real.get_mail_routing(USER)):
        with pytest.raises(CapabilityNotSupported):
            call()


# --- PHP ------------------------------------------------------------------------------------------------------------------

PHP = {("uapi_cpanel", "LangPHP::php_get_installed_versions"): {"versions": ["ea-php74", "ea-php81", "ea-php83", "alt-php99"]},
       ("uapi_cpanel", "LangPHP::php_get_vhost_versions"): [{"vhost": "acme.com", "version": "ea-php81"}, {"vhost": "blog.acme.com", "version": "ea-php74"}],
       ("uapi_cpanel", "LangPHP::php_ini_get_user_basic_directives"): {"directives": [{"key": "memory_limit", "value": "256M"},
                                                                                        {"key": "upload_max_filesize", "value": "64M"},
                                                                                        {"key": "max_execution_time", "value": "60"},
                                                                                        {"key": "display_errors", "value": "Off"}]}}


def test_package_names_and_versions_convert_both_ways():
    assert version_of("ea-php81") == "8.1" and version_of("ea-php74") == "7.4" and version_of("alt-php99") == "" and package_of("8.3") == "ea-php83"


def test_php_options_list_installed_versions_newest_first_with_old_ones_flagged():
    real, _ = adapter(PHP)
    options = real.php_options(USER)
    assert options.versions == ["8.3", "8.1", "7.4"] and options.end_of_life == ["7.4"] and options.recommended == "8.3"
    assert "256M" in options.memory_limits and 60 in options.execution_times


def test_php_settings_are_read_per_domain():
    real, _ = adapter(PHP)
    current = real.get_php_settings(USER, "acme.com")
    assert (current.version, current.memory_limit, current.upload_max_filesize, current.max_execution_time, current.display_errors) == ("8.1", "256M", "64M", 60, False)
    assert real.get_php_settings(USER, "blog.acme.com").version == "7.4"
    with pytest.raises(HostingError, match="not on this account"):
        real.get_php_settings(USER, "victim.com")


def test_changing_php_sends_the_version_and_the_allowed_directives_only():
    real, fake = adapter(PHP)
    real.set_php_settings(USER, "acme.com", {"version": "8.3", "memory_limit": "512M", "max_execution_time": "120", "display_errors": True})
    assert fake.uapi_calls("LangPHP", "php_set_vhost_versions")[0][0]["version"] == "ea-php83"
    params = fake.uapi_calls("LangPHP", "php_ini_set_user_basic_directives")[0][0]
    assert params["vhost"] == "acme.com" and params["type"] == "vhost"
    assert sorted(v for k, v in params.items() if k.startswith("directive-")) == ["display_errors:On", "max_execution_time:120", "memory_limit:512M"]


@pytest.mark.parametrize("changes,message", [({"version": "5.6"}, "listed PHP versions"), ({"memory_limit": "999M"}, "memory limits"),
                                              ({"upload_max_filesize": "1G"}, "upload limits"), ({"max_execution_time": "9999"}, "run times"),
                                              ({"auto_prepend_file": "/etc/passwd"}, "cannot be changed"), ({}, "cannot be changed"),
                                              ({"memory_limit": "128M", "upload_max_filesize": "256M"}, "cannot be larger")])
def test_php_changes_outside_the_lists_are_refused_before_anything_is_sent(changes, message):
    real, fake = adapter(PHP)
    with pytest.raises(HostingError, match=message):
        real.set_php_settings(USER, "acme.com", changes)
    assert not fake.uapi_calls("LangPHP", "php_set_vhost_versions") and not fake.uapi_calls("LangPHP", "php_ini_set_user_basic_directives")


def test_directives_may_come_back_as_a_mapping_and_extensions_are_not_offered():
    answers = {**PHP, ("uapi_cpanel", "LangPHP::php_ini_get_user_basic_directives"): {"directives": {"memory_limit": "512M"}}}
    real, _ = adapter(answers)
    assert real.get_php_settings(USER, "acme.com").memory_limit == "512M"
    with pytest.raises(CapabilityNotSupported):
        real.list_php_extensions(USER, "acme.com")


# --- Through the gateway --------------------------------------------------------------------------------------------------

@pytest.mark.django_db
@override_settings(HOSTING_CPANEL_CAPABILITIES=["dns", "databases"])
def test_the_gateway_turns_an_unsupported_cpanel_operation_into_a_clear_501(monkeypatch, staff):
    from apps.accounts.roles import Role
    from apps.clients import services as client_services
    from apps.core.exceptions import ServiceError
    from apps.hosting import control
    from apps.hosting.models import HostingAccount, HostingStatus
    from apps.products import services as product_services
    from apps.products.models import Product, ProductType, Server, ServerKind

    manager = staff(Role.MANAGER)
    client = client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test"})
    product = product_services.create_product(manager, {"name": "Starter", "type": ProductType.SHARED_HOSTING})
    server = Server.objects.create(name="cp", hostname="whm.test", kind=ServerKind.WHM_API)
    account = HostingAccount.objects.create(client=client, product=Product.objects.get(pk=product.pk), server=server, domain="acme.com",
                                            username=USER, status=HostingStatus.ACTIVE)
    fake = FakeWhm({**MYSQL, ("uapi_cpanel", "DomainInfo::domains_data"): DOMAINS})
    monkeypatch.setattr(WhmApiAdapter, "_call", lambda self, function, params=None, **kw: fake(function, params, sensitive=kw.get("sensitive", False)))
    owner = client.contacts.get().user
    assert control.capabilities_for(account) == ["account", "databases", "dns", "usage"] or set(control.capabilities_for(account)) == {"account", "databases", "dns", "usage"}
    assert [d.name for d in control.perform(owner, account, "databases", "list_databases")] == ["acme_shop"]
    with pytest.raises(ServiceError) as unsupported:
        control.perform(owner, account, "databases", "insert_row", "acme_shop", "t", {"a": "1"})
    assert unsupported.value.code == "capability_not_supported" and unsupported.value.status_code == 501
    with pytest.raises(ServiceError):
        control.perform(owner, account, "email", "list_mailboxes")  # not enabled in this test's settings


def test_no_two_cpanel_mixins_share_a_method_name():
    """The mixins are combined into one adapter, so a shared name would silently let one tool override another's helper."""
    import collections
    import glob
    import re

    seen = collections.defaultdict(list)
    for path in glob.glob("apps/hosting/adapters/cpanel_*.py"):
        for match in re.finditer(r"^    def (\w+)\(", open(path, encoding="utf-8").read(), re.M):
            seen[match.group(1)].append(path)
    assert {name: paths for name, paths in seen.items() if len(paths) > 1} == {}
