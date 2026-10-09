"""Copying a server plan's features (disk, bandwidth, mailboxes, databases...) onto products. A server knows features, never prices."""
from decimal import Decimal

import pytest

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.hosting.adapters.cpanel_api import plan_limit
from apps.hosting.adapters.whm_api import WhmApiAdapter, package_features
from apps.products import services
from apps.products.models import CatalogStatus, Product, ProductType, Server, ServerKind

pytestmark = pytest.mark.django_db

STARTER = {"name": "WordPress-Starter Startup", "QUOTA": "51200", "BWLIMIT": "unlimited", "MAXPOP": "10", "MAXSQL": "5", "MAXFTP": "unlimited",
           "MAXSUB": "20", "MAXPARK": "0", "MAXADDON": "1", "MAXLST": "unlimited", "MAX_EMAIL_PER_HOUR": "100", "HASSHELL": "n", "IP": "n"}
GROW = {"name": "Shared Grow", "QUOTA": "0", "BWLIMIT": "102400", "MAXPOP": "unlimited", "MAXSQL": "unlimited", "MAXFTP": "5", "MAXSUB": "unlimited",
        "MAXPARK": "unlimited", "MAXADDON": "unlimited", "MAXLST": "0", "MAX_EMAIL_PER_HOUR": "unlimited", "HASSHELL": "y", "IP": "y"}


@pytest.mark.parametrize("value,expected", [("unlimited", None), ("", None), (None, None), ("none", None), ("51200", 51200), (" 20 ", 20), ("7.0", 7), ("-5", None),
                                            ("abc", None), ("0", 0)])
def test_a_count_limit_is_a_number_unlimited_or_zero_meaning_none_allowed(value, expected):
    assert plan_limit(value) == expected  # checked on the live server: a plain 0 for subdomains or addon domains means none are allowed


@pytest.mark.parametrize("value,expected", [("0", None), ("unlimited", None), ("81920", 81920)])
def test_disk_and_bandwidth_treat_zero_as_unlimited(value, expected):
    assert plan_limit(value, zero_is_unlimited=True) == expected


def test_a_whm_package_is_read_into_neutral_features():
    assert package_features(STARTER) == {"disk_mb": 51200, "bandwidth_mb": None, "email_accounts": 10, "databases": 5, "ftp_accounts": None, "subdomains": 20,
                                         "parked_domains": 0, "addon_domains": 1, "mailing_lists": None, "emails_per_hour": 100, "shell_access": "No",
                                         "dedicated_ip": "No"}
    grow = package_features(GROW)
    assert grow["disk_mb"] is None and grow["bandwidth_mb"] == 102400 and grow["shell_access"] == "Yes" and grow["dedicated_ip"] == "Yes"


def test_the_connection_check_returns_each_packages_features(monkeypatch):
    def call(self, function, params=None, **kw):
        return {"metadata": {"result": 1}, "data": {"version": "11.138.0"} if function == "version" else {"pkg": [STARTER, GROW]}}

    monkeypatch.setattr(WhmApiAdapter, "_call", call)
    result = WhmApiAdapter(host="h", port=2087, credentials={}).check_connection()
    assert result["packages"] == ["Shared Grow", "WordPress-Starter Startup"]
    assert result["details"]["WordPress-Starter Startup"]["databases"] == 5 and result["details"]["Shared Grow"]["bandwidth_mb"] == 102400


def test_the_description_lines_read_naturally():
    limits, _ = services.features_of(package_features(STARTER))
    assert services.feature_lines(limits) == ["50 GB storage", "Unlimited bandwidth", "5 databases", "10 email accounts", "Unlimited FTP accounts",
                                              "20 subdomains", "1 addon domain"]
    one, _ = services.features_of({"disk_mb": 500, "databases": 1, "email_accounts": None, "shell_access": "Yes"})
    assert services.feature_lines(one) == ["500 MB storage", "1 database", "Unlimited email accounts", "Shell access"]


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def server():
    return Server.objects.create(name="live", hostname="whm.test", kind=ServerKind.WHM_API)


DETAILS = {"WordPress-Starter Startup": package_features(STARTER), "Shared Grow": package_features(GROW)}
PACKAGES = sorted(DETAILS)


def test_a_new_product_arrives_with_the_servers_features_hidden_and_with_no_price(manager, server):
    _, created, _ = services.apply_server_plans(manager, server, PACKAGES, create=["WordPress-Starter Startup"], details=DETAILS)
    product = created[0]
    assert product.resource_limits["disk_mb"] == 51200 and product.resource_limits["email_accounts"] == 10 and product.resource_limits["bandwidth_mb"] is None
    assert product.ordered_limits()[0][0] == "disk_mb" and product.limit_items[0] == ("Disk space (MB)", 51200)
    assert "50 GB storage" in product.description and "10 email accounts" in product.description
    assert product.status == CatalogStatus.HIDDEN and not product.prices.exists()


def test_refreshing_a_linked_product_copies_the_features_and_keeps_its_price_and_its_own_description_lines(manager, server):
    product = services.create_product(manager, {"name": "My Grow", "type": ProductType.SHARED_HOSTING, "whm_package_name": "Shared Grow",
                                                "description": "My own words", "resource_limits": {"disk_mb": 10, "databases": 1}, "price": "750"})
    product = Product.objects.get(pk=product.pk)
    report = services.plans_report(server, PACKAGES, DETAILS)
    assert report["linked"][0]["stale"] == [product]
    _, _, refreshed = services.apply_server_plans(manager, server, PACKAGES, details=DETAILS, refresh=[product.pk])
    product.refresh_from_db()
    assert refreshed == [product] and product.resource_limits["disk_mb"] is None and product.resource_limits["bandwidth_mb"] == 102400
    assert product.description.splitlines()[-1] == "My own words" and "Unlimited storage" in product.description and product.prices.get().price == Decimal("750.00")
    assert services.plans_report(server, PACKAGES, DETAILS)["linked"][0]["stale"] == []
    assert AuditEvent.objects.filter(action="product.features_synced", target_id=str(product.pk)).exists()


def test_a_blank_description_is_filled_from_the_features_on_refresh(manager, server):
    product = services.create_product(manager, {"name": "Bare", "type": ProductType.WORDPRESS_HOSTING, "whm_package_name": "WordPress-Starter Startup"})
    services.apply_server_plans(manager, server, PACKAGES, details=DETAILS, refresh=[product.pk])
    product.refresh_from_db()
    assert "5 databases" in product.description


def test_a_product_whose_package_has_no_details_is_left_alone(manager, server):
    product = services.create_product(manager, {"name": "Mystery", "type": ProductType.SHARED_HOSTING, "whm_package_name": "Mystery Plan",
                                                "resource_limits": {"disk_mb": 10}})
    services.apply_server_plans(manager, server, PACKAGES, details=DETAILS, refresh=[product.pk])
    product.refresh_from_db()
    assert product.resource_limits == {"disk_mb": 10}


def test_the_page_shows_the_features_and_the_refresh_boxes_and_applying_works(client, manager, server, monkeypatch):
    monkeypatch.setattr(WhmApiAdapter, "check_connection", lambda self: {"version": "11.138.0", "packages": PACKAGES, "details": DETAILS})
    product = services.create_product(manager, {"name": "My Grow", "type": ProductType.SHARED_HOSTING, "whm_package_name": "Shared Grow",
                                                "resource_limits": {"disk_mb": 10}})
    client.force_login(manager)
    page = client.get(f"/staff/servers/{server.pk}/plans/").content.decode()
    assert "Disk space (MB): 51200" in page and "Databases: 5" in page and "features differ from the server" in page and f'name="refresh" value="{product.pk}"' in page
    assert "a server does not know prices" in page
    text = client.post(f"/staff/servers/{server.pk}/plans/", {"refresh": [str(product.pk)], "create": ["WordPress-Starter Startup"]}, follow=True).content.decode()
    assert "features of 1 product(s) were copied" in text and "1 product(s) created" in text
    assert "same as the server" in client.get(f"/staff/servers/{server.pk}/plans/").content.decode()

