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
