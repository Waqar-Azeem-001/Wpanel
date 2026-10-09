"""Sync plans: compare a server's real packages with the catalog, repair products that point at a missing package, and add new ones hidden and unpriced."""
import pytest

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.core.exceptions import ServiceError
from apps.hosting.adapters.whm_api import WhmApiAdapter
from apps.products import services
from apps.products.models import CatalogStatus, Product, ProductType, Server, ServerKind

pytestmark = pytest.mark.django_db

PACKAGES = ["Shared Starter", "WordPress-Starter Startup", "WordPress-Best Value Grow", "Business-Recommended Digital", "Reseller-BASIC WEB-1",
            "resellerwhe_Basic", "default", "DMA14500"]


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def server():
    return Server.objects.create(name="live", hostname="whm.test", kind=ServerKind.WHM_API)


@pytest.fixture
def server_answers(monkeypatch):
    monkeypatch.setattr(WhmApiAdapter, "check_connection", lambda self: {"version": "11.138.0", "packages": list(PACKAGES)})


def product(manager, name, kind, package, server=None):
    item = services.create_product(manager, {"name": name, "type": kind, "whm_package_name": package})
    if server:
        item.servers.add(server)
    return Product.objects.get(pk=item.pk)


@pytest.mark.parametrize("name,expected", [("WordPress-Starter Startup", ProductType.WORDPRESS_HOSTING), ("Business-Powerful Business", ProductType.BUSINESS_HOSTING),
                                           ("Shared Grow", ProductType.SHARED_HOSTING), ("Reseller MAXIMIZED WEB-3", ProductType.RESELLER_HOSTING),
                                           ("Reseller-BASIC WEB-1", ProductType.RESELLER_HOSTING), ("resellerwhe_Basic", ""), ("default", ""), ("DMA14500", ""),
                                           ("Professional Plan", "")])
def test_the_kind_of_a_package_is_guessed_from_its_name_and_a_resellers_own_package_never_is(name, expected):
    assert services.type_for_package(name) == expected


def test_the_report_finds_broken_unlinked_and_linked(manager, server):
    broken = product(manager, "WordPress Hosting", ProductType.WORDPRESS_HOSTING, "wordpress_hosting", server)
    fine = product(manager, "Shared Starter", ProductType.SHARED_HOSTING, "Shared Starter", server)
    elsewhere = product(manager, "Other server plan", ProductType.SHARED_HOSTING, "gone_pkg")  # not mapped to this server: not this server's problem
    report = services.plans_report(server, PACKAGES)
    assert report["broken"] == [broken] and elsewhere not in report["broken"]
    assert [u["package"] for u in report["unlinked"]][:2] == ["WordPress-Starter Startup", "WordPress-Best Value Grow"]
    assert report["linked"] == [{"package": "Shared Starter", "products": [fine]}]
    assert {u["package"]: u["type"] for u in report["unlinked"]}["default"] == ""


def test_a_broken_product_is_pointed_at_a_real_package_and_the_change_is_audited(manager, server):
    broken = product(manager, "WordPress Hosting", ProductType.WORDPRESS_HOSTING, "wordpress_hosting", server)
    remapped, created = services.apply_server_plans(manager, server, PACKAGES, remap={broken.pk: "WordPress-Starter Startup"})
    broken.refresh_from_db()
    assert remapped == [broken] and created == [] and broken.whm_package_name == "WordPress-Starter Startup" and server in broken.servers.all()
    assert AuditEvent.objects.get(action="product.package_remapped").metadata == {"from": "wordpress_hosting", "to": "WordPress-Starter Startup", "server": "live"}


def test_new_products_are_hidden_unpriced_typed_and_mapped_to_the_server(manager, server):
    remapped, created = services.apply_server_plans(manager, server, PACKAGES, create=["WordPress-Best Value Grow", "Business-Recommended Digital"])
    assert remapped == [] and [p.name for p in created] == ["WordPress-Best Value Grow", "Business-Recommended Digital"]
    wordpress, business = created
    assert wordpress.type == ProductType.WORDPRESS_HOSTING and business.type == ProductType.BUSINESS_HOSTING
    for item in created:
        assert item.status == CatalogStatus.HIDDEN and not item.prices.exists() and item.servers.filter(pk=server.pk).exists()
        assert item.whm_package_name == item.name
    assert AuditEvent.objects.filter(action="product.created_from_server").count() == 2


def test_an_unclear_package_becomes_shared_hosting_only_when_ticked_and_a_twin_is_never_made(manager, server):
    _, created = services.apply_server_plans(manager, server, PACKAGES, create=["default"])
    assert created[0].type == ProductType.SHARED_HOSTING
    again = services.apply_server_plans(manager, server, PACKAGES, create=["default", "Shared Starter"])
    assert again[1][0].name == "Shared Starter" and Product.objects.filter(whm_package_name="default").count() == 1


def test_a_package_the_server_does_not_have_is_refused_and_nothing_is_half_applied(manager, server):
    broken = product(manager, "WordPress Hosting", ProductType.WORDPRESS_HOSTING, "wordpress_hosting", server)
    with pytest.raises(ServiceError) as refused:
        services.apply_server_plans(manager, server, PACKAGES, remap={broken.pk: "WordPress-Starter Startup"}, create=["Imaginary Plan"])
    assert refused.value.code == "unknown_package"
    broken.refresh_from_db()
    assert broken.whm_package_name == "wordpress_hosting" and not Product.objects.filter(name="Imaginary Plan").exists()
    with pytest.raises(ServiceError):
        services.apply_server_plans(manager, server, PACKAGES, remap={broken.pk: "not a package"})


def test_only_staff_who_manage_products_can_apply(staff, server):
    with pytest.raises(ServiceError):
        services.apply_server_plans(staff(Role.SUPPORT_AGENT), server, PACKAGES, create=["Shared Starter"])


def test_the_page_lists_what_to_fix_and_applying_it_works(client, manager, server, server_answers):
    broken = product(manager, "WordPress Hosting", ProductType.WORDPRESS_HOSTING, "wordpress_hosting", server)
    client.force_login(manager)
    page = client.get(f"/staff/servers/{server.pk}/plans/").content.decode()
    assert "WordPress Hosting" in page and "wordpress_hosting" in page and "WordPress-Best Value Grow" in page and "hidden" in page and "11.138.0" in page
    assert f'name="remap_{broken.pk}"' in page and 'name="create"' in page
    response = client.post(f"/staff/servers/{server.pk}/plans/", {f"remap_{broken.pk}": "WordPress-Starter Startup", "create": ["Business-Recommended Digital"]},
                           follow=True)
    text = response.content.decode()
    assert "1 product(s) now use a package the server has" in text and "1 product(s) created" in text
    broken.refresh_from_db()
    assert broken.whm_package_name == "WordPress-Starter Startup" and Product.objects.filter(name="Business-Recommended Digital", status=CatalogStatus.HIDDEN).exists()
    again = client.get(f"/staff/servers/{server.pk}/plans/").content.decode()
    assert "Every product on this server points at a package it has" in again


def test_a_failed_post_says_why_and_a_nothing_post_changes_nothing(client, manager, server, server_answers):
    client.force_login(manager)
    bad = client.post(f"/staff/servers/{server.pk}/plans/", {"create": ["Imaginary Plan"]}, follow=True).content.decode()
    assert "has no package called" in bad and not Product.objects.filter(name="Imaginary Plan").exists()
    assert "Nothing was changed" in client.post(f"/staff/servers/{server.pk}/plans/", {}, follow=True).content.decode()


def test_an_unreachable_server_is_explained_not_a_crash(client, manager, server, monkeypatch):
    from apps.hosting.adapters.base import HostingError

    def down(self):
        raise HostingError("The server did not answer in time.")

    monkeypatch.setattr(WhmApiAdapter, "check_connection", down)
    client.force_login(manager)
    page = client.get(f"/staff/servers/{server.pk}/plans/", follow=True).content.decode()
    assert "Could not read the plans" in page and "did not answer in time" in page


def test_only_managing_staff_see_the_page_and_the_server_list_links_to_it(client, staff, manager, server, server_answers):
    client.force_login(staff(Role.SUPPORT_AGENT))
    assert client.get(f"/staff/servers/{server.pk}/plans/").status_code in (302, 403)
    client.force_login(manager)
    assert f"/staff/servers/{server.pk}/plans/" in client.get("/staff/servers/").content.decode()
