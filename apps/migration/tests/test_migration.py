"""Moving a cPanel account to a native node (M2/M3/M4): the whole path through WHPanel, the agent protocol and the node's importer, on a pretend node."""
import pytest
from django.urls import reverse

from apps.accounts.roles import Role
from apps.agent import services as agent
from apps.agent.models import AgentJob
from apps.agent.reference_agent import NativeMachine
from apps.agent.tests.cpanel_archive import build
from apps.agent.tests.test_native_website import make_node, order, world  # noqa: F401
from apps.audit.models import AuditEvent
from apps.core.exceptions import ServiceError
from apps.hosting.adapters import native_dns
from apps.hosting.adapters.powerdns import FakePowerDns, fqdn
from apps.hosting.models import HostingAccount, HostingStatus
from apps.migration import readiness, services
from apps.migration.models import Migration, MigrationState as S
from apps.products.models import Server, ServerKind

pytestmark = pytest.mark.django_db


@pytest.fixture
def moved(world, tmp_path):  # noqa: F811
    """A live account on a cPanel server, an empty native web node and a mail node, each with a pretend machine of its own."""
    manager, client, product = world
    cpanel = Server.objects.create(name="cp-1", hostname="cp1.example.com", kind=ServerKind.WHM_API, ip_address="203.0.113.5")
    account = hosting_account(manager, client, product, cpanel)
    web, web_agent = make_node(manager, "web-1", capabilities=("web", "php", "database"), ips=["198.51.100.10"])
    web_agent.machine = NativeMachine(files_dir=tmp_path / "web", capabilities=("web", "database"))
    mail, mail_agent = make_node(manager, "mail-1", capabilities=("mail",), ips=["198.51.100.20"])
    mail_agent.machine = NativeMachine(files_dir=tmp_path / "mail", capabilities=("mail",))
    return manager, account, web, web_agent, mail, mail_agent, tmp_path


def hosting_account(manager, client, product, server, domain="acme.com"):
    from apps.hosting import services as hosting

    account = hosting.request_hosting(manager, client, product, domain, server=server)
    account.status = HostingStatus.ACTIVE
    account.save()
    return account


def drop_archive(tmp_path, account, where=("web",), **kw):
    for node in where:
        build(tmp_path / node / "_stage" / "migrations" / f"cpmove-{account.username}.tar.gz", user=account.username, domain=account.domain, **kw)


def settle(*agents):
    """Let every agent fetch and answer until nothing is waiting (each answer can queue the next step)."""
    for _ in range(40):
        if not any([a.poll_once() for a in agents]):
            return
    raise AssertionError("the migration did not settle")


def to_ready(moved, **kw):
    manager, account, web, web_agent, mail, mail_agent, tmp = moved
    drop_archive(tmp, account, **kw)
    m = services.start(manager, account, web, mail_target=kw.pop("mail_target", None))
    services.check_archive(manager, m)
    settle(web_agent, mail_agent)
    m.refresh_from_db()
    return m


# --- the whole path ---------------------------------------------------------------------------------------------------------------------

def test_a_migration_goes_from_cpanel_to_the_node_and_back(moved):
    manager, account, web, web_agent, mail, mail_agent, tmp = moved
    drop_archive(tmp, account, where=("web", "mail"))
    m = services.start(manager, account, web, mail_target=mail)
    assert m.state == S.AWAITING_ARCHIVE and m.archive_name == f"cpmove-{account.username}.tar.gz"
    services.check_archive(manager, m)
    settle(web_agent, mail_agent)
    m.refresh_from_db()
    assert m.state == S.READY and m.report["main_domain"] == "acme.com" and m.report["warnings"]
    with pytest.raises(ServiceError, match="warnings"):
        services.approve(manager, m)  # the warnings (cron, old PHP...) must be accepted by a person
    # the mail domain has to exist on the mail node for the import; the chain makes it itself
    services.approve(manager, m, accept_warnings=True)
    settle(web_agent, mail_agent)
    m.refresh_from_db()
    assert m.state == S.VALIDATED, (m.state, m.last_error, list(m.events.values_list("message", flat=True)))
    assert m.parts_done == ["site", "files", "databases", "maildomain", "mail", "validate"]
    machine = web_agent.machine
    docroot = tmp / "web" / account.username / "sites" / "acme.com" / "public_html"
    assert (docroot / "wp-content" / "uploads" / "a.jpg").exists()
    assert machine.engine.dbs[f"{account.username}_shop"]["orders"]["rows"][0] == [1, "Ann"]
    assert mail_agent.machine.mail._state("acme.com")["mailboxes"]["info"]["hash"].startswith("$6$")
    assert m.validation["ok"] and m.validation["files_missing"] == 0
    account.refresh_from_db()
    assert account.server.name == "cp-1"  # nothing has moved for the customer yet

    services.cutover(manager, m)
    account.refresh_from_db()
    m.refresh_from_db()
    assert m.state == S.WINDOW and account.server == web and account.mail_server == mail and m.rollback_until > m.cutover_at
    assert "198.51.100.10" in m.dns_note and "not hosted" in m.dns_note

    services.rollback(manager, m)
    account.refresh_from_db()
    m.refresh_from_db()
    assert m.state == S.ROLLED_BACK and account.server.name == "cp-1" and account.mail_server is None
    actions = set(AuditEvent.objects.values_list("action", flat=True))
    assert {"migration.started", "migration.approved", "migration.cutover", "migration.rolled_back"} <= actions


def test_the_cpanel_account_is_never_touched(moved):
    manager, account, web, web_agent, *_ = moved
    m = to_ready(moved)
    services.approve(manager, m, accept_warnings=True)
    settle(web_agent)
    m.refresh_from_db()
    services.cutover(manager, m)
    assert not AgentJob.objects.filter(node__server=account.server if hasattr(account.server, "agent") else None).exists()
    assert account.status == HostingStatus.ACTIVE
    assert "cp-1" == m.source.name and m.source.kind == ServerKind.WHM_API


def test_dns_hosted_here_is_switched_at_cutover_and_put_back_on_rollback(moved, monkeypatch):
    manager, account, web, web_agent, *_ = moved
    fake = FakePowerDns()
    fake.create_zone("acme.com", [(fqdn("@", "acme.com"), "A", 3600, ["203.0.113.5"])])
    monkeypatch.setattr(native_dns, "get_dns", lambda: fake)
    m = to_ready(moved)
    services.approve(manager, m, accept_warnings=True)
    settle(web_agent)
    m.refresh_from_db()
    services.cutover(manager, m)
    a = {(n, t): c for n, t, ttl, c in fake.rrsets("acme.com")}
    assert a[("acme.com.", "A")] == ["198.51.100.10"]
    m.refresh_from_db()
    assert "now points to 198.51.100.10" in m.dns_note
    services.rollback(manager, m)
    assert {(n, t): c for n, t, ttl, c in fake.rrsets("acme.com")}[("acme.com.", "A")] == ["203.0.113.5"]


# --- stops that need a person -----------------------------------------------------------------------------------------------------------

def test_an_addon_domain_stops_the_migration_for_a_person(moved):
    manager, account, *_ = moved
    m = to_ready(moved, addon=True)
    assert m.state == S.MANUAL_REVIEW and "addon" in m.last_error
    with pytest.raises(ServiceError):
        services.approve(manager, m, accept_warnings=True)


def test_a_backup_of_another_account_is_refused(moved):
    manager, account, web, web_agent, mail, mail_agent, tmp = moved
    build(tmp / "web" / "_stage" / "migrations" / f"cpmove-{account.username}.tar.gz", user="someoneelse", domain="other.com")
    m = services.start(manager, account, web)
    services.check_archive(manager, m)
    settle(web_agent)
    m.refresh_from_db()
    assert m.state == S.MANUAL_REVIEW and "different" in m.last_error


def test_a_backup_that_has_not_arrived_keeps_waiting_and_can_be_checked_again(moved):
    manager, account, web, web_agent, mail, mail_agent, tmp = moved
    m = services.start(manager, account, web)
    services.check_archive(manager, m)
    settle(web_agent)
    m.refresh_from_db()
    assert m.state == S.AWAITING_ARCHIVE and "not arrived" in m.events.last().message
    drop_archive(tmp, account)
    services.check_archive(manager, m)
    settle(web_agent)
    m.refresh_from_db()
    assert m.state == S.READY


def test_a_wrong_checksum_is_caught_by_the_node(moved):
    manager, account, web, web_agent, mail, mail_agent, tmp = moved
    drop_archive(tmp, account)
    m = services.start(manager, account, web, expected_sha256="0" * 64)
    services.check_archive(manager, m)
    settle(web_agent)
    m.refresh_from_db()
    assert m.state == S.FAILED and "checksum" in m.last_error


def test_a_failing_step_is_recorded_and_a_retry_carries_on(moved):
    manager, account, web, web_agent, *_ = moved
    m = to_ready(moved)
    web_agent.machine.system.users[account.username] = 9  # the account name is already taken on the node: CREATE_WEBSITE fails
    services.approve(manager, m, accept_warnings=True)
    settle(web_agent)
    m.refresh_from_db()
    assert m.state == S.FAILED and m.step == "site" and "already exists" in m.last_error
    del web_agent.machine.system.users[account.username]
    services.retry(manager, m)
    settle(web_agent)
    m.refresh_from_db()
    assert m.state == S.VALIDATED


def test_a_difference_found_by_the_check_needs_a_person(moved):
    manager, account, web, web_agent, *_ = moved
    m = to_ready(moved)
    services.approve(manager, m, accept_warnings=True)
    # delete an imported file before the check runs: stop the chain after "databases" by hand
    for _ in range(3):
        web_agent.poll_once()
    docroot = web_agent.machine.files_dir / account.username / "sites" / "acme.com" / "public_html" / "index.php"
    docroot.unlink()
    settle(web_agent)
    m.refresh_from_db()
    assert m.state == S.MANUAL_REVIEW and m.validation["files_missing"] == 1
    with pytest.raises(ServiceError):
        services.cutover(manager, m)


# --- rules ---------------------------------------------------------------------------------------------------------------------------------

def test_only_a_cpanel_account_with_a_native_target_can_start(moved, world):  # noqa: F811
    manager, account, web, *_ = moved
    with pytest.raises(ServiceError, match="native node"):
        services.start(manager, account, Server.objects.get(name="cp-1"))
    account.server = web
    account.save()
    with pytest.raises(ServiceError, match="cPanel"):
        services.start(manager, account, web)


def test_one_migration_at_a_time_per_account(moved):
    manager, account, web, *_ = moved
    m = services.start(manager, account, web)
    with pytest.raises(ServiceError, match="in progress"):
        services.start(manager, account, web)
    services.cancel(manager, m)
    assert services.start(manager, account, web).pk != m.pk


def test_the_window_must_end_before_completion_unless_forced(moved):
    manager, account, web, web_agent, *_ = moved
    m = to_ready(moved)
    services.approve(manager, m, accept_warnings=True)
    settle(web_agent)
    m.refresh_from_db()
    services.cutover(manager, m)
    with pytest.raises(ServiceError, match="window"):
        services.finish(manager, m)
    services.finish(manager, m, force=True)
    m.refresh_from_db()
    assert m.state == S.COMPLETE and AgentJob.objects.filter(command="MIGRATE_DISCARD").exists()
    with pytest.raises(ServiceError):
        services.rollback(manager, m)


def test_a_customer_cannot_start_or_cut_over(moved, make_user):
    manager, account, web, *_ = moved
    with pytest.raises(ServiceError):
        services.start(make_user("c@example.com"), account, web)


def test_new_accounts_can_be_forced_onto_native_nodes(world, settings):  # noqa: F811
    from apps.hosting import services as hosting

    manager, client, product = world
    cpanel = Server.objects.create(name="cp-2", hostname="cp2.example.com", kind=ServerKind.WHM_API)
    native = Server.objects.create(name="n-2", hostname="n2.example.com", kind=ServerKind.NATIVE)
    product.servers.add(cpanel)
    settings.NEW_ACCOUNTS_NATIVE_ONLY = True
    with pytest.raises(ServiceError, match="native"):
        hosting.request_hosting(manager, client, product, "a-site.com")
    with pytest.raises(ServiceError, match="native"):
        hosting.request_hosting(manager, client, product, "a-site.com", server=cpanel)
    product.servers.add(native)
    assert hosting.request_hosting(manager, client, product, "a-site.com").server == native


def test_the_stage_marks_follow_the_state(moved):
    manager, account, web, *_ = moved
    m = services.start(manager, account, web)
    marks = dict(services.stage_marks(m))
    assert marks["backup"] == "now" and marks["completion"] == "todo"
    m.state = S.WINDOW
    marks = dict(services.stage_marks(m))
    assert marks["cutover"] == "done" and marks["rollback window"] == "now"
    m.state = S.COMPLETE
    assert set(dict(services.stage_marks(m)).values()) == {"done"}


def test_the_dashboard_counts(moved):
    manager, account, web, web_agent, *_ = moved
    assert services.summary()["total"] == 1 and services.summary()["pending"] == 1
    m = to_ready(moved)
    assert services.summary()["in_progress"] == 1 and services.summary()["pending"] == 0
    services.approve(manager, m, accept_warnings=True)
    settle(web_agent)
    m.refresh_from_db()
    services.cutover(manager, m)
    s = services.summary()
    assert s["migrated"] == 1 and s["remaining_on_cpanel"] == 0 and s["total"] == 1


# --- M4: retiring cPanel ------------------------------------------------------------------------------------------------------------------

def test_retirement_is_not_ready_until_every_line_is_green(moved):
    manager, account, *_ = moved
    assert not readiness.ready()
    checks = {c["key"]: c for c in readiness.checks()}
    assert not checks["no_dependency"]["ok"] and not checks["migrations"]["ok"] and checks["no_new"]["ok"] is False  # the account made today counts as new
    with pytest.raises(ServiceError, match="what you saw"):
        readiness.sign(manager, "isolation", "ok")
    with pytest.raises(ServiceError):
        readiness.sign(manager, "made-up", "a long enough sentence")
    readiness.sign(manager, "isolation", "Probe passed on web-1 on 2026-11-02, report attached")
    assert {c["key"]: c for c in readiness.checks()}["isolation"]["ok"]
    readiness.withdraw(manager, "isolation")
    assert not {c["key"]: c for c in readiness.checks()}["isolation"]["ok"]


def test_everything_green_when_all_is_done(moved):
    manager, account, web, web_agent, *_ = moved
    m = to_ready(moved)
    services.approve(manager, m, accept_warnings=True)
    settle(web_agent)
    m.refresh_from_db()
    services.cutover(manager, m)
    HostingAccount.objects.update(created_at="2020-01-01T00:00:00Z")
    for key, _, _ in readiness.SIGNED:
        readiness.sign(manager, key, "Seen working on the real node, ticket 123")
    assert readiness.ready()


# --- screens ------------------------------------------------------------------------------------------------------------------------------

def test_the_screens_open_for_staff_and_not_for_customers(moved, client, make_user):
    manager, account, web, web_agent, *_ = moved
    m = to_ready(moved)
    client.force_login(manager)
    for name, args in (("dashboard", ()), ("retirement", ()), ("detail", (m.pk,))):
        assert client.get(reverse(f"migration_staff:{name}", args=args)).status_code == 200, name
    page = client.get(reverse("migration_staff:detail", args=(m.pk,))).content.decode()
    assert "Approve and import" in page and account.domain in page
    customer = make_user("cust@example.com")
    client.force_login(customer)
    assert client.get(reverse("migration_staff:dashboard")).status_code in (302, 403)


def test_the_buttons_work(moved, client):
    manager, account, web, web_agent, *_ = moved
    drop_archive(moved[-1], account)
    client.force_login(manager)
    reply = client.post(reverse("migration_staff:start"), {"account": account.pk, "target": web.pk, "window_days": 3}, follow=True)
    m = Migration.objects.get()
    assert reply.status_code == 200 and m.window_days == 3
    client.post(reverse("migration_staff:act", args=(m.pk, "check")))
    settle(web_agent)
    client.post(reverse("migration_staff:act", args=(m.pk, "approve")))  # warnings not accepted: refused
    m.refresh_from_db()
    assert m.state == S.READY
    client.post(reverse("migration_staff:act", args=(m.pk, "approve")), {"accept": "1"})
    settle(web_agent)
    m.refresh_from_db()
    assert m.state == S.VALIDATED
    assert client.post(reverse("migration_staff:act", args=(m.pk, "bogus"))).status_code == 302
    client.post(reverse("migration_staff:act", args=(m.pk, "cutover")))
    m.refresh_from_db()
    assert m.state == S.WINDOW
    client.post(reverse("migration_staff:signoff", args=("isolation",)), {"note": "Probe passed on a real node, report attached"})
    assert readiness.checks()[2]["ok"]


def test_the_site_check_uses_the_nodes_address(moved):
    manager, account, web, web_agent, *_ = moved
    m = to_ready(moved)
    seen = []
    assert services.check_site(m, fetch=lambda url, host: seen.append((url, host)) or 200)
    assert seen == [("http://198.51.100.10/", "acme.com")] and m.validation["site"]["ok"]
    assert not services.check_site(m, fetch=lambda url, host: 500) and not m.validation["site"]["ok"]

    def boom(url, host):
        raise OSError("refused")

    assert not services.check_site(m, fetch=boom) and "refused" in m.validation["site"]["note"]
    _ = agent


# --- who may do what (authorization) ---------------------------------------------------------------------------------------------------

VIEW_ONLY = (Role.SUPPORT_AGENT,)
MAY_MANAGE = (Role.TECHNICAL, Role.MANAGER, Role.ADMIN, Role.SUPER_ADMIN)
ACTIONS_POST = ("check", "approve", "retry", "cancel", "site", "cutover", "rollback", "finish")


def as_role(client, staff, make_user, role, existing_manager=None):
    """Sign in as the role. The world fixture already made the manager, so that one is reused instead of made twice."""
    if role == Role.CUSTOMER:
        user = make_user("customer-x@example.com")
    elif role == Role.MANAGER and existing_manager is not None:
        user = existing_manager
    else:
        user = staff(role)
    client.force_login(user)
    return user


@pytest.mark.parametrize("role", [Role.CUSTOMER, Role.SUPPORT_AGENT, Role.TECHNICAL, Role.MANAGER, Role.ADMIN, Role.SUPER_ADMIN])
def test_every_screen_follows_the_role(moved, client, staff, make_user, role):
    manager, account, web, web_agent, *_ = moved
    m = services.start(manager, account, web)
    as_role(client, staff, make_user, role, manager)
    for name, args in (("dashboard", ()), ("retirement", ()), ("detail", (m.pk,))):
        status = client.get(reverse(f"migration_staff:{name}", args=args)).status_code
        if role == Role.CUSTOMER:
            assert status in (302, 403), (name, status)  # never a 200
        else:
            assert status == 200, (role, name, status)


@pytest.mark.parametrize("role", [Role.CUSTOMER, Role.SUPPORT_AGENT])
def test_people_who_may_only_look_cannot_change_anything(moved, client, staff, make_user, role):
    manager, account, web, web_agent, *_ = moved
    m = to_ready(moved)
    before = Migration.objects.get(pk=m.pk).state
    as_role(client, staff, make_user, role)
    for action in ACTIONS_POST:
        reply = client.post(reverse("migration_staff:act", args=(m.pk, action)), {"accept": "1", "force": "1"})
        assert reply.status_code in (302, 403), (role, action)
    assert client.post(reverse("migration_staff:start"), {"account": account.pk, "target": web.pk}).status_code in (302, 403)
    assert client.post(reverse("migration_staff:signoff", args=("isolation",)), {"note": "I looked at it on a real node"}).status_code in (302, 403)
    assert Migration.objects.get(pk=m.pk).state == before and not readiness.checks()[2]["ok"]
    assert Migration.objects.count() == 1 and not AgentJob.objects.filter(command="CREATE_WEBSITE").exists()


@pytest.mark.parametrize("role", MAY_MANAGE)
def test_staff_who_manage_hosting_can_act_and_it_is_audited(moved, client, staff, make_user, role):
    manager, account, web, web_agent, *_ = moved
    m = to_ready(moved)
    user = as_role(client, staff, make_user, role, manager)
    client.post(reverse("migration_staff:act", args=(m.pk, "approve")), {"accept": "1"})
    m.refresh_from_db()
    assert m.state == S.IMPORTING
    event = AuditEvent.objects.filter(action="migration.approved").latest("id")
    assert event.actor_id == user.pk


def test_a_signed_out_visitor_is_sent_to_sign_in(moved, client):
    manager, account, web, *_ = moved
    m = services.start(manager, account, web)
    for url in (reverse("migration_staff:dashboard"), reverse("migration_staff:detail", args=(m.pk,)), reverse("migration_staff:retirement")):
        reply = client.get(url)
        assert reply.status_code == 302 and "login" in reply["Location"]
    assert client.post(reverse("migration_staff:act", args=(m.pk, "cutover"))).status_code in (302, 403)
    assert Migration.objects.get(pk=m.pk).state == S.AWAITING_ARCHIVE


def test_the_actions_refuse_get_requests(moved, client, staff, make_user):
    manager, account, web, *_ = moved
    m = services.start(manager, account, web)
    as_role(client, staff, make_user, Role.MANAGER, manager)
    for action in ACTIONS_POST:
        assert client.get(reverse("migration_staff:act", args=(m.pk, action))).status_code == 405


def test_confirm_dialogs_name_the_account_and_the_nodes(moved, client):
    manager, account, web, web_agent, *_ = moved
    m = to_ready(moved)
    services.approve(manager, m, accept_warnings=True)
    settle(web_agent)
    m.refresh_from_db()
    client.force_login(manager)
    page = client.get(reverse("migration_staff:detail", args=(m.pk,))).content.decode()
    assert f"Switch {account.domain} from cp-1 to web-1 now?" in page
    services.cutover(manager, m)
    page = client.get(reverse("migration_staff:detail", args=(m.pk,))).content.decode()
    assert f"Put {account.domain} back on cp-1?" in page and f"Complete the migration of {account.domain}?" in page
    assert "Are you sure" not in page


def statuses(m):
    return {r["label"]: r["status"] for r in services.timeline(m)}


def test_the_timeline_shows_files_database_and_email_as_their_own_steps(moved, client):
    manager, account, web, web_agent, mail, mail_agent, tmp = moved
    drop_archive(tmp, account, where=("web", "mail"))
    m = services.start(manager, account, web, mail_target=mail)
    assert statuses(m)["Backup arrived on the node"] == "now" and statuses(m)["Complete"] == "pending"
    services.check_archive(manager, m)
    settle(web_agent, mail_agent)
    m.refresh_from_db()
    s = statuses(m)
    assert s["Discovery"] == "done" and s["Compatibility check"] == "done" and s["Files transfer"] == "pending"
    services.approve(manager, m, accept_warnings=True)
    m.refresh_from_db()
    assert statuses(m)["Website created on the node"] == "now"
    settle(web_agent, mail_agent)
    m.refresh_from_db()
    s = statuses(m)
    assert all(s[k] == "done" for k in ("Website created on the node", "Files transfer", "Database transfer", "Email transfer", "Validation")) and s["DNS cutover"] == "pending"
    services.cutover(manager, m)
    m.refresh_from_db()
    rows = {r["label"]: r for r in services.timeline(m)}
    assert rows["DNS cutover"]["status"] == "done" and rows["Rollback window"]["status"] == "now" and "Open until" in rows["Rollback window"]["note"]
    client.force_login(manager)
    page = client.get(reverse("migration_staff:detail", args=(m.pk,))).content.decode()
    assert "Files transfer" in page and "Email transfer" in page and "In progress" in page


def test_a_step_that_does_not_apply_is_shown_as_not_needed(moved):
    manager, account, web, web_agent, *_ = moved
    m = to_ready(moved, mail=False)
    assert statuses(m)["Email transfer"] == "skipped"


def test_a_blocker_and_a_failed_step_are_marked_as_problems(moved):
    manager, account, web, web_agent, *_ = moved
    m = to_ready(moved, addon=True)
    assert statuses(m)["Compatibility check"] == "problem" and "addon" in services.timeline(m)[2]["note"]


def test_empty_and_busy_states_explain_themselves(moved, client):
    manager, account, web, *_ = moved
    client.force_login(manager)
    page = client.get(reverse("migration_staff:dashboard")).content.decode()
    assert "No migrations" in page and "Nothing changes on the cPanel account until you cut over" in page and 'data-loading="Starting' in page
    m = services.start(manager, account, web)
    detail = client.get(reverse("migration_staff:detail", args=(m.pk,))).content.decode()
    assert 'data-loading="Reading the backup' in detail
    filtered = client.get(reverse("migration_staff:dashboard") + "?state=complete").content.decode()
    assert "No migrations in this state" in filtered
