"""Usage, logs and performance (H11): the demo provider's history and live figures, server-side limits, and the screen."""
from datetime import date, timedelta

import pytest
from django.core.management import call_command

from apps.accounts.roles import Role
from apps.clients import services as client_services
from apps.core.exceptions import ServiceError
from apps.hosting import control
from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.demo import DemoAdapter
from apps.hosting.adapters.demo_insights import MAX_DAYS, DemoInsights
from apps.hosting.models import HostingAccount
from apps.products import services as product_services
from apps.products.models import ProductType

pytestmark = pytest.mark.django_db

USAGE = {"disk_used_mb": 5000, "disk_limit_mb": 20480, "bandwidth_used_mb": 31000, "bandwidth_limit_mb": 102400}


def insights(seed=7):
    return DemoInsights(seed, lambda: dict(USAGE))


def test_history_has_one_point_per_day_ending_today_at_todays_disk_figure():
    points = insights().history(30)
    assert len(points) == 30 and points[-1].day == date.today().isoformat() and points[0].day == (date.today() - timedelta(days=29)).isoformat()
    assert points[-1].disk_mb == USAGE["disk_used_mb"] and all(p.disk_mb <= USAGE["disk_used_mb"] for p in points)
    assert [p.disk_mb for p in points] == sorted(p.disk_mb for p in points)  # storage grows towards today
    assert all(p.bandwidth_mb >= 0 for p in points)


def test_history_is_steady_and_only_the_listed_periods_are_accepted():
    assert insights().history(7) == insights().history(7) and len(insights().history(MAX_DAYS)) == MAX_DAYS
    for bad in (0, 5, 365, -1):
        with pytest.raises(HostingError):
            insights().history(bad)


def test_performance_reports_four_figures_inside_their_limits():
    stats = {s.key: s for s in insights().performance()}
    assert set(stats) == {"cpu", "memory", "processes", "io"}
    assert all(0 <= s.used <= s.limit for s in stats.values()) and stats["cpu"].unit == "%" and stats["memory"].limit == 1024


# --- Limits are enforced by the provider ------------------------------------------------------------------------------

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


def test_a_full_disk_refuses_a_write_whatever_screen_asks(owner, account, monkeypatch):
    control.perform(owner, account, "files", "write_file", "public_html/ok.txt", b"fits")
    monkeypatch.setattr(DemoAdapter, "get_usage", lambda self, username: {**USAGE, "disk_used_mb": 20480})
    with pytest.raises(ServiceError, match="disk space is full"):
        control.perform(owner, account, "files", "write_file", "public_html/more.txt", b"x")
    monkeypatch.setattr(DemoAdapter, "get_usage", lambda self, username: {**USAGE, "disk_used_mb": 20479})
    with pytest.raises(ServiceError, match="disk space is full"):
        control.perform(owner, account, "files", "write_file", "public_html/big.bin", b"x" * 2_000_000)
    control.perform(owner, account, "files", "write_file", "public_html/small.txt", b"x" * 1000)


def test_the_other_plan_limits_are_enforced_by_the_provider_too(owner, account):
    limits = control.perform(owner, account, "databases", "database_limits")
    assert limits.max_databases
    mailboxes = control.perform(owner, account, "email", "email_limits")
    assert mailboxes.max_mailboxes


# --- The screen ---------------------------------------------------------------------------------------------------------

def uurl(account, query=""):
    return f"/account/hosting/{account.pk}/manage/usage/{query}"


def test_the_page_shows_the_allowance_resource_counts_history_and_live_figures(client, owner, account):
    client.force_login(owner)
    page = client.get(uurl(account)).content.decode()
    flat = " ".join(page.split())
    assert "Plan allowance" in page and "Storage" in page and "Bandwidth (this month)" in page and "<progress" in page
    for label in ("Databases", "Email accounts", "Websites (extra domains)", "Subdomains", "FTP accounts", "Cron jobs", "Backups kept"):
        assert label in page, label
    assert "0 / 5" in flat and "1 / 5" in flat  # no extra domains yet, the sample FTP account
    assert "History" in page and "<polyline" in page and "Storage in use" in page and "Bandwidth per day" in page
    assert "Right now" in page and "Running processes" in page and "Disk activity" in page
    assert f"/account/hosting/{account.pk}/manage/logs/" in page


def test_the_period_can_be_changed_and_a_bad_one_falls_back(client, owner, account):
    client.force_login(owner)
    assert "7 days" in client.get(uurl(account, "?days=7")).content.decode()
    page = client.get(uurl(account, "?days=abc")).content.decode()
    assert 'aria-current="page">30 days' in page
    assert 'aria-current="page">90 days' in client.get(uurl(account, "?days=90")).content.decode()


def test_a_nearly_full_account_gets_a_warning_with_the_upgrade_link(client, owner, account):
    client.force_login(owner)
    agency = HostingAccount.objects.get(domain="demo-agency.example.com")  # 20,100 of 20,480 MB used
    page = client.get(uurl(agency)).content.decode()
    assert "Storage is" in page and "alert-danger" in page and "See upgrade options" in page
    assert "Storage is" not in client.get(uurl(account)).content.decode()  # demo-shop is at about a quarter of its storage


def test_a_pending_account_explains_that_usage_comes_later(client, owner, account):
    client.force_login(owner)
    pending = HostingAccount.objects.get(domain="demo-new.example.com")
    page = client.get(uurl(pending), follow=True).content.decode()
    assert "Usage appears here once your hosting is active" in page


def test_strangers_get_nothing_and_the_menu_opens_this_screen(client, customer, owner, account):
    client.force_login(customer)
    assert client.get(uurl(account)).status_code == 404
    client.force_login(owner)
    menu = client.get(f"/account/hosting/{account.pk}/").content.decode()
    assert uurl(account) in menu
