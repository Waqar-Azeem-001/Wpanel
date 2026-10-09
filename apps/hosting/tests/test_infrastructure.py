"""Infrastructure dashboard (H13): what each kind of server reports, how health is worked out, and the staff screen."""
import pytest
from django.core.management import call_command

from apps.accounts.roles import Role
from apps.hosting import infra_dashboard
from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.capabilities import NodeCapability, NodeService, NodeStatus, supports_node
from apps.hosting.adapters.demo import DemoAdapter
from apps.hosting.adapters.demo_node import demo_node_status
from apps.hosting.adapters.manual import ManualAdapter
from apps.hosting.adapters.whm_api import WhmApiAdapter
from apps.products.models import Server, ServerKind, ServerStatus

pytestmark = pytest.mark.django_db


# --- What each server type reports -------------------------------------------------------------------------------------

def test_which_adapters_can_report_the_machine():
    assert issubclass(DemoAdapter, NodeCapability) and issubclass(WhmApiAdapter, NodeCapability) and not issubclass(ManualAdapter, NodeCapability)
    assert supports_node(DemoAdapter(host="h", port=1, credentials={})) and not supports_node(ManualAdapter(host="h", port=1, credentials={}))


def test_the_demo_machine_reports_everything_inside_sensible_bounds_and_steadily():
    one = demo_node_status("node2.demo.invalid")
    assert one == demo_node_status("node2.demo.invalid")
    assert 0 <= one.cpu_percent <= 100 and 0 < one.memory_used_mb <= one.memory_total_mb and 0 < one.disk_used_gb <= one.disk_total_gb
    assert len(one.load) == 3 and one.network_in_kbps > 0 and one.uptime_days >= 20
    assert [s.key for s in one.services] == ["web", "database", "mail", "dns", "agent", "backups"]
    assert len({demo_node_status(h).cpu_percent for h in ("a.test", "bb.test", "ccc.test", "dddd.test")}) > 1  # machines differ


def whm(responses):
    adapter = WhmApiAdapter(host="whm.test", port=2087, credentials={"api_username": "root", "api_token": "t"})

    def call(function, params=None, *, sensitive=False):
        key = function if function != "servicestatus" else f"servicestatus:{params['service']}"
        answer = responses[key]
        if isinstance(answer, Exception):
            raise answer
        return answer

    adapter._call = call
    return adapter


GOOD = {"version": {"data": {"version": "11.118.0"}}, "systemloadavg": {"one": "0.5", "five": "0.4", "fifteen": "0.3"},
        **{f"servicestatus:{n}": {"data": {"service": [{"name": n, "running": 1}]}} for n in ("httpd", "mysql", "exim", "dovecot", "named")}}


def test_a_cpanel_server_reports_only_what_whm_can():
    status = whm(GOOD).node_status()
    assert status.version == "11.118.0" and status.load == (0.5, 0.4, 0.3)
    assert status.cpu_percent is None and status.memory_used_mb is None and status.disk_used_gb is None and status.network_in_kbps is None
    states = {s.key: s.state for s in status.services}
    assert states == {"web": "running", "database": "running", "mail": "running", "dns": "running", "agent": "unavailable", "backups": "unavailable"}


def test_whm_service_states_down_degraded_and_unavailable():
    responses = {**GOOD, "servicestatus:mysql": {"data": {"service": [{"running": 0}]}}, "servicestatus:dovecot": {"data": {"service": [{"running": 0}]}},
                 "servicestatus:named": HostingError("not allowed")}
    states = {s.key: s.state for s in whm(responses).node_status().services}
    assert states["database"] == "down" and states["mail"] == "degraded" and states["dns"] == "unavailable" and states["web"] == "running"


def test_load_in_whm_data_envelope_and_a_missing_load_are_both_handled():
    assert whm({**GOOD, "systemloadavg": {"data": {"one": "1", "five": "2", "fifteen": "3"}}}).node_status().load == (1.0, 2.0, 3.0)
    assert whm({**GOOD, "systemloadavg": HostingError("no")}).node_status().load is None


def test_an_unreachable_whm_server_raises_so_the_dashboard_can_say_so():
    with pytest.raises(HostingError):
        whm({"version": HostingError("Could not reach the server")}).node_status()


# --- Health ----------------------------------------------------------------------------------------------------------------

def server(**kw):
    return Server(name=kw.pop("name", "S"), hostname="h", kind=kw.pop("kind", ServerKind.DEMO), **kw)


def ok(**kw):
    base = dict(cpu_percent=20.0, memory_used_mb=1000, memory_total_mb=8000, disk_used_gb=10.0, disk_total_gb=100.0,
                services=[NodeService("web", "Web", "running")])
    return NodeStatus(**{**base, **kw})


@pytest.mark.parametrize("status,expected", [
    (ok(), "healthy"),
    (ok(services=[NodeService("web", "Web", "running"), NodeService("mail", "Mail", "down")]), "down"),
    (ok(services=[NodeService("web", "Web", "degraded")]), "degraded"),
    (ok(disk_used_gb=95.0), "degraded"), (ok(cpu_percent=97.0), "degraded"), (ok(memory_used_mb=7500), "degraded"),
    (ok(services=[NodeService("web", "Web", "unavailable")]), "unknown"), (None, "unknown"),
])
def test_health_follows_services_and_resources(status, expected):
    assert infra_dashboard._health(server(), status, "")[0] == expected


def test_staff_status_and_errors_come_before_the_figures():
    assert infra_dashboard._health(server(status=ServerStatus.OFFLINE), ok(), "")[0] == "offline"
    assert infra_dashboard._health(server(status=ServerStatus.MAINTENANCE), ok(), "")[0] == "maintenance"
    assert infra_dashboard._health(server(), None, "Could not reach the server")[0] == "unreachable"


def test_a_cpanel_card_shows_unreported_figures_as_unreported_and_a_broken_one_as_unreachable(monkeypatch):
    real = Server.objects.create(name="Real cPanel", hostname="whm.test", kind=ServerKind.WHM_API)
    monkeypatch.setattr(WhmApiAdapter, "_call", lambda self, function, params=None, **kw: whm(GOOD)._call(function, params))
    card = infra_dashboard.node_card(real, 3, 2)
    assert card["health"] == "healthy" and all(not m["known"] for m in card["meters"]) and card["load"] == (0.5, 0.4, 0.3)

    def fail(self, function, params=None, **kw):
        raise HostingError("Could not reach the server: timed out")

    monkeypatch.setattr(WhmApiAdapter, "_call", fail)
    broken = infra_dashboard.node_card(real, 3, 2)
    assert broken["health"] == "unreachable" and "timed out" in broken["why"] and broken["status"] is None


def test_a_server_in_maintenance_or_manual_is_not_asked_for_figures():
    manual = infra_dashboard.node_card(Server.objects.create(name="M", hostname="m", kind=ServerKind.MANUAL), 0, 0)
    assert manual["health"] == "unknown" and manual["status"] is None
    held = infra_dashboard.node_card(Server.objects.create(name="D", hostname="d", kind=ServerKind.DEMO, status=ServerStatus.MAINTENANCE), 0, 0)
    assert held["health"] == "maintenance" and held["status"] is None


# --- The screen ------------------------------------------------------------------------------------------------------------

@pytest.fixture
def seeded(staff):
    from apps.clients import services as client_services
    from apps.products import services as product_services
    from apps.products.models import ProductType

    manager = staff(Role.MANAGER)
    client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test"})
    product_services.create_product(manager, {"name": "Starter", "type": ProductType.SHARED_HOSTING})
    call_command("demo_hosting_data")
    return manager


def test_the_dashboard_lists_every_node_with_region_provider_counts_and_services(client, seeded, staff):
    client.force_login(staff(Role.TECHNICAL))
    page = client.get("/staff/hosting/infrastructure/").content.decode()
    flat = " ".join(page.split())
    for text in ("Demo (sample data)", "Demo node 2 (sample data)", "Demo node 3 (sample data)", "EU (Finland)", "EU (Germany)", "US (Missouri)",
                 "Contabo", "Hosting accounts", "Web server", "Node agent", "Backups", "Load (1 / 5 / 15 min)", "Services", "Nodes"):
        assert text in page, text
    assert "Maintenance" in page and "Marked as in maintenance by staff" in page and "live)" in page
    assert "<dd>5 / 50 <span" in flat  # the five sample accounts sit on the first node (limit 50)


def test_nodes_are_sorted_with_the_ones_needing_attention_first(seeded):
    cards = infra_dashboard.dashboard()["cards"]
    order = [infra_dashboard.HEALTH_ORDER[c["health"]] for c in cards]
    assert order == sorted(order)


def test_a_cpanel_node_on_the_page_says_not_reported(client, seeded, staff, monkeypatch):
    Server.objects.create(name="Real cPanel", hostname="whm.test", kind=ServerKind.WHM_API, region="EU (Germany)")
    monkeypatch.setattr(WhmApiAdapter, "_call", lambda self, function, params=None, **kw: whm(GOOD)._call(function, params))
    client.force_login(staff(Role.TECHNICAL))
    page = client.get("/staff/hosting/infrastructure/").content.decode()
    assert "Real cPanel" in page and "Not reported by this server type" in page and "11.118.0" in page and "Node agent: not reported" in page


def test_only_staff_with_the_hosting_permission_see_it(client, seeded, staff, customer):
    assert client.get("/staff/hosting/infrastructure/").status_code in (302, 403)
    client.force_login(customer)
    assert client.get("/staff/hosting/infrastructure/").status_code in (302, 403, 404)
    client.force_login(staff(Role.SUPPORT_AGENT))  # view_hosting is enough: the page only reads
    assert client.get("/staff/hosting/infrastructure/").status_code == 200


def test_the_server_list_links_to_it_and_region_can_be_saved(client, seeded):
    client.force_login(seeded)
    assert "/staff/hosting/infrastructure/" in client.get("/staff/servers/").content.decode()
    target = Server.objects.get(name="Demo (sample data)")
    page = client.get(f"/staff/servers/{target.pk}/edit/").content.decode()
    assert "Region" in page
    data = {"name": target.name, "hostname": target.hostname, "ip_address": "203.0.113.10", "region": "EU (Finland) - rack 2", "kind": "demo",
            "api_port": 2087, "use_ssl": "on", "verify_ssl": "on"}
    client.post(f"/staff/servers/{target.pk}/edit/", data)
    target.refresh_from_db()
    assert target.region == "EU (Finland) - rack 2"


def test_removing_the_sample_data_removes_the_extra_nodes_too(seeded):
    assert Server.objects.filter(name__startswith="Demo node").count() == 2
    call_command("demo_hosting_data", "--remove")
    assert not Server.objects.filter(name__contains="(sample data)").exists()
