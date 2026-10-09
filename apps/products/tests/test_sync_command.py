"""python manage.py sync_server_plans: the same sync as the Servers > Sync plans page, for the command line or a schedule."""
import io

import pytest
from django.core.management import CommandError, call_command

from apps.accounts.roles import Role
from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.whm_api import WhmApiAdapter, package_features
from apps.products import services
from apps.products.models import CatalogStatus, Product, ProductType, Server, ServerKind

pytestmark = pytest.mark.django_db

RAW = {"name": "Shared Grow", "QUOTA": "81920", "BWLIMIT": "153600", "MAXPOP": "20", "MAXSQL": "20", "MAXFTP": "5", "MAXSUB": "3", "MAXADDON": "5", "HASSHELL": "n", "IP": "n"}
RAW2 = {**RAW, "name": "Business-Best Value Grow"}
DETAILS = {"Shared Grow": package_features(RAW), "Business-Best Value Grow": package_features(RAW2)}


@pytest.fixture
def setup(staff, monkeypatch):
    manager = staff(Role.SUPER_ADMIN)
    server = Server.objects.create(name="live", hostname="whm.test", kind=ServerKind.WHM_API)
    product = services.create_product(manager, {"name": "Grow", "type": ProductType.SHARED_HOSTING, "whm_package_name": "Shared Grow",
                                                "resource_limits": {"disk_mb": 1}, "description": "1 GB storage\nFree SSL"})
    product.servers.add(server)
    monkeypatch.setattr(WhmApiAdapter, "check_connection", lambda self: {"version": "11", "packages": sorted(DETAILS), "details": DETAILS})
    return manager, server, Product.objects.get(pk=product.pk)


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    call_command("sync_server_plans", *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def test_a_dry_run_only_reports(setup):
    _, _, product = setup
    out, _ = run("--dry-run", "--create")
    assert "2 plans on the server, 1 product(s) differ, 1 to add" in out
    product.refresh_from_db()
    assert product.resource_limits == {"disk_mb": 1} and Product.objects.count() == 1


def test_it_copies_the_features_and_keeps_the_price_and_the_marketing_lines(setup):
    manager, _, product = setup
    services.set_price(manager, product, billing_cycle="monthly", price="750")
    out, _ = run()
    product.refresh_from_db()
    assert "features copied onto 1 product(s); 0 product(s) added" in out
    assert product.resource_limits["disk_mb"] == 81920 and product.description.splitlines()[0] == "80 GB storage" and product.description.endswith("Free SSL")
    assert str(product.prices.get().price) == "750.00" and Product.objects.count() == 1


def test_create_adds_the_other_plans_hidden_and_unpriced_and_running_again_changes_nothing(setup):
    run("--create")
    added = Product.objects.get(name="Business-Best Value Grow")
    assert added.status == CatalogStatus.HIDDEN and not added.prices.exists() and added.resource_limits["databases"] == 20
    out, _ = run("--create")
    assert "0 product(s) differ, 0 to add" in out and Product.objects.count() == 2


def test_an_unreachable_server_is_reported_and_the_rest_continue(setup, monkeypatch):
    def down(self):
        raise HostingError("The server did not answer in time.")

    monkeypatch.setattr(WhmApiAdapter, "check_connection", down)
    _, err = run()
    assert "live: The server did not answer in time." in err


def test_a_user_to_record_it_under_is_needed(setup):
    from apps.accounts.models import User

    User.objects.all().update(is_superuser=False)
    with pytest.raises(CommandError):
        run()
    out, _ = run("--as", User.objects.first().email, "--dry-run")
    assert "live:" in out
