"""SSL and PHP (H6, H7): the demo provider's rules, and the customer screens end to end."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from django.core.management import call_command

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.core.exceptions import ServiceError
from apps.hosting import control
from apps.hosting.adapters.base import HostingError
from apps.hosting.adapters.demo_php import DemoPhp
from apps.hosting.adapters.demo_ssl import DemoSsl
from apps.hosting.models import HostingAccount
from apps.products import services as product_services
from apps.products.models import ProductType

pytestmark = pytest.mark.django_db

NAMES = ["shop.example.com", "blog.shop.example.com", "elsewhere.org"]


# --- The provider's rules (no web, no database) -------------------------------------------------------------------

@pytest.fixture
def ssl(tmp_path):
    return DemoSsl(tmp_path / "state", lambda: NAMES, lambda name: name != "elsewhere.org")


@pytest.fixture
def php(tmp_path):
    return DemoPhp(tmp_path / "state", lambda: NAMES)


def write_cert(ssl, domain, days, auto_renew=False, redirect=False):
    now = datetime.now(timezone.utc)
    cert = {"issuer": "x", "issued_at": (now - timedelta(days=90 - days)).isoformat(), "expires_at": (now + timedelta(days=days)).isoformat(),
            "auto_renew": auto_renew, "https_redirect": redirect}
    ssl.file.parent.mkdir(parents=True, exist_ok=True)
    ssl.file.write_text(json.dumps({"certs": {domain: cert}}), encoding="utf-8")


def test_every_domain_is_listed_and_starts_without_a_certificate(ssl):
    assert [(s.domain, s.status, s.active) for s in ssl.list_status()] == [(n, "none", False) for n in NAMES]


def test_a_certificate_is_issued_for_90_days_with_automatic_renewal_on(ssl):
    ssl.request("shop.example.com")
    state = ssl.status("shop.example.com")
    assert state.status == "active" and state.active and state.auto_renew and not state.https_redirect and state.days_left in (89, 90)


def test_a_domain_that_does_not_point_here_cannot_get_a_certificate(ssl):
    with pytest.raises(HostingError, match="does not point to this server"):
        ssl.request("elsewhere.org")
    with pytest.raises(HostingError, match="not on this account"):
        ssl.request("stranger.com")


def test_always_use_https_needs_a_working_certificate_and_goes_off_with_it(ssl):
    with pytest.raises(HostingError, match="certificate first"):
        ssl.set_redirect("shop.example.com", True)
    ssl.request("shop.example.com")
    ssl.set_redirect("shop.example.com", True)
    assert ssl.status("shop.example.com").https_redirect
    ssl.remove("shop.example.com")
    state = ssl.status("shop.example.com")
    assert state.status == "none" and not state.https_redirect
    with pytest.raises(HostingError, match="no certificate"):
        ssl.remove("shop.example.com")
    with pytest.raises(HostingError, match="certificate first"):
        ssl.set_auto_renew("shop.example.com", True)


def test_status_follows_the_dates(ssl):
    write_cert(ssl, "shop.example.com", 9)
    assert ssl.status("shop.example.com").status == "expiring"
    write_cert(ssl, "shop.example.com", -3, redirect=True)
    state = ssl.status("shop.example.com")
    assert state.status == "expired" and not state.active and not state.https_redirect and state.days_left < 0
    with pytest.raises(HostingError, match="working certificate"):
        ssl.set_redirect("shop.example.com", True)


def test_automatic_renewal_renews_a_certificate_close_to_its_end_only_when_on(ssl):
    write_cert(ssl, "shop.example.com", 20, auto_renew=True)
    assert ssl.status("shop.example.com").days_left >= 89
    write_cert(ssl, "shop.example.com", 20, auto_renew=False)
    assert ssl.status("shop.example.com").days_left in (19, 20)


def test_php_starts_with_sensible_settings_and_only_listed_choices_are_accepted(php):
    current = php.settings("shop.example.com")
    assert current.version in ("8.3", "8.2", "8.1") and current.memory_limit == "256M" and not current.display_errors
    php.set_settings("shop.example.com", {"version": "8.2", "memory_limit": "512M", "max_execution_time": "120", "display_errors": True})
    changed = php.settings("shop.example.com")
    assert (changed.version, changed.memory_limit, changed.max_execution_time, changed.display_errors) == ("8.2", "512M", 120, True)
    for bad in ({"version": "5.6"}, {"memory_limit": "999M"}, {"upload_max_filesize": "1G"}, {"max_execution_time": "9999"},
                {"max_execution_time": "soon"}, {"auto_prepend_file": "/etc/passwd"}, {}):
        with pytest.raises(HostingError):
            php.set_settings("shop.example.com", bad)
    with pytest.raises(HostingError, match="not on this account"):
        php.settings("stranger.com")


def test_the_upload_limit_cannot_be_larger_than_the_memory_limit(php):
    with pytest.raises(HostingError, match="cannot be larger than the memory limit"):
        php.set_settings("shop.example.com", {"memory_limit": "128M", "upload_max_filesize": "256M"})


def test_required_extensions_stay_on_and_others_switch(php):
    by_name = {e.name: e for e in php.extensions("shop.example.com")}
    assert by_name["mysqli"].required and by_name["mysqli"].enabled and not by_name["imagick"].enabled
    with pytest.raises(HostingError, match="stays on"):
        php.set_extension("shop.example.com", "mysqli", False)
    php.set_extension("shop.example.com", "imagick", True)
    php.set_extension("shop.example.com", "gd", False)
    by_name = {e.name: e for e in php.extensions("shop.example.com")}
    assert by_name["imagick"].enabled and not by_name["gd"].enabled
    with pytest.raises(HostingError, match="not available"):
        php.set_extension("shop.example.com", "runkit", True)


def test_each_domain_keeps_its_own_php_settings(php):
    php.set_settings("shop.example.com", {"version": "8.1"})
    assert php.settings("blog.shop.example.com").version != "" and php.settings("shop.example.com").version == "8.1"
    php.set_extension("blog.shop.example.com", "soap", True)
    assert not {e.name: e for e in php.extensions("shop.example.com")}["soap"].enabled


# --- The screens ------------------------------------------------------------------------------------------------------

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


def surl(account, tail=""):
    return f"/account/hosting/{account.pk}/manage/ssl/{tail}"


def purl(account, tail=""):
    return f"/account/hosting/{account.pk}/manage/php/{tail}"


def test_the_ssl_page_shows_the_state_of_each_certificate(client, owner, account):
    client.force_login(owner)
    page = client.get(surl(account)).content.decode()
    assert "SSL certificate" in page and "demo-shop.example.com" in page and "Free SSL (sample data)" in page
    assert "Active" in page and "days left" in page and "Always use HTTPS" in page
    blog = HostingAccount.objects.get(domain="demo-blog.example.com")
    soon = client.get(surl(blog)).content.decode()
    assert "Ends soon" in soon and "ends soon" in soon
    agency = HostingAccount.objects.get(domain="demo-agency.example.com")
    gone = client.get(surl(agency)).content.decode()
    assert "Expired" in gone and "has expired" in gone and "days ago" in gone


def test_install_renew_switch_and_remove_a_certificate_from_the_page(client, owner, account):
    client.force_login(owner)
    act = surl(account, "do/")
    client.post(act, {"action": "remove", "domain": "demo-shop.example.com"})
    page = client.get(surl(account)).content.decode()
    assert "No certificate" in page and "Install" in page
    client.post(act, {"action": "install", "domain": "demo-shop.example.com"})
    assert "Active" in client.get(surl(account)).content.decode()
    client.post(act, {"action": "https", "domain": "demo-shop.example.com", "enabled": "1"})
    client.post(act, {"action": "auto_renew", "domain": "demo-shop.example.com", "enabled": "0"})
    status = control.perform(owner, account, "ssl", "get_ssl_status", "demo-shop.example.com")
    assert status.https_redirect and not status.auto_renew


def test_a_refused_ssl_change_says_why(client, owner, account):
    client.force_login(owner)
    act = surl(account, "do/")
    client.post(act, {"action": "remove", "domain": "demo-shop.example.com"})
    page = client.post(act, {"action": "https", "domain": "demo-shop.example.com", "enabled": "1"}, follow=True).content.decode()
    assert "Install a working certificate first" in page
    page = client.post(act, {"action": "install", "domain": "nowhere.example.org"}, follow=True).content.decode()
    assert "not on this account" in page
    assert "That action is not available" in client.post(act, {"action": "wipe"}, follow=True).content.decode()


def test_the_php_page_shows_settings_extensions_and_the_old_version_warning(client, owner, account):
    client.force_login(owner)
    page = client.get(purl(account)).content.decode()
    assert "PHP settings" in page and "Memory limit" in page and "Largest upload" in page and "imagick" in page and "Needed" in page
    blog = HostingAccount.objects.get(domain="demo-blog.example.com")
    old = client.get(purl(blog)).content.decode()
    assert "no longer gets security fixes" in old and "PHP 8.0" in old


def test_change_php_settings_and_extensions_from_the_page(client, owner, account):
    client.force_login(owner)
    client.post(purl(account, "do/"), {"action": "settings", "domain": "demo-shop.example.com", "version": "8.2", "memory_limit": "512M",
                                       "upload_max_filesize": "128M", "max_execution_time": "120", "display_errors": "1"})
    client.post(purl(account, "do/"), {"action": "extension", "domain": "demo-shop.example.com", "name": "imagick", "enabled": "1"})
    current = control.perform(owner, account, "php", "get_php_settings", "demo-shop.example.com")
    assert (current.version, current.memory_limit, current.upload_max_filesize, current.max_execution_time, current.display_errors) == (
        "8.2", "512M", "128M", 120, True)
    page = client.get(purl(account), {"domain": "demo-shop.example.com"}).content.decode()
    assert "PHP 8.2" in page
    bad = client.post(purl(account, "do/"), {"action": "settings", "domain": "demo-shop.example.com", "version": "5.6", "memory_limit": "512M",
                                             "upload_max_filesize": "64M", "max_execution_time": "60"}, follow=True).content.decode()
    assert "Choose one of the listed PHP versions" in bad
    refused = client.post(purl(account, "do/"), {"action": "extension", "domain": "demo-shop.example.com", "name": "mysqli", "enabled": "0"},
                          follow=True).content.decode()
    assert "stays on" in refused


def test_every_change_is_audited(client, owner, account):
    client.force_login(owner)
    client.post(surl(account, "do/"), {"action": "auto_renew", "domain": "demo-shop.example.com", "enabled": "0"})
    client.post(purl(account, "do/"), {"action": "settings", "domain": "demo-shop.example.com", "version": "8.2", "memory_limit": "256M",
                                       "upload_max_filesize": "64M", "max_execution_time": "60"})
    events = list(AuditEvent.objects.filter(action="hosting.control").values_list("metadata", flat=True))
    assert any(e.get("operation") == "set_ssl_auto_renew" and e.get("domain") == "demo-shop.example.com" for e in events)
    assert any(e.get("operation") == "set_php_settings" and e.get("version") == "8.2" for e in events)


def test_strangers_get_nothing_and_a_provider_without_them_shows_the_not_available_page(client, customer, owner, account):
    client.force_login(customer)
    for url in (surl(account), purl(account)):
        assert client.get(url).status_code == 404
    assert client.post(surl(account, "do/"), {"action": "remove", "domain": "demo-shop.example.com"}).status_code == 404
    assert client.post(purl(account, "do/"), {"action": "settings", "domain": "demo-shop.example.com"}).status_code == 404
    from apps.products.models import Server

    Server.objects.filter(kind="demo").update(kind="manual")
    client.force_login(owner)
    assert "not available for your hosting server yet" in client.get(surl(account)).content.decode()
    assert "not available for your hosting server yet" in client.get(purl(account)).content.decode()
    with pytest.raises(ServiceError):
        control.perform(owner, account, "ssl", "list_ssl_status")


def test_the_ssl_page_explains_installation_and_status(client, owner, account):
    client.force_login(owner)
    page = client.get(surl(account)).content.decode()
    assert "How to install an SSL certificate" in page and "SSL status" in page
    assert "Recommended for you" not in page  # nothing to suggest until staff create the add-ons


def test_the_ssl_page_suggests_ssl_and_whois_privacy_and_opens_a_filled_in_ticket(client, owner, account, manager):
    from apps.products.models import AddonKind, BillingCycle
    from apps.products import services as ps
    for name, kind in (("Premium SSL", AddonKind.SSL), ("WHOIS Privacy", AddonKind.WHOIS_PRIVACY)):
        addon = ps.create_addon(manager, {"name": name, "kind": kind})
        ps.set_addon_status(manager, addon, "active")
        ps.set_price(manager, addon, billing_cycle=BillingCycle.ANNUAL, price="12.00")
    client.force_login(owner)
    page = client.get(surl(account)).content.decode()
    assert "Recommended for you" in page and "Premium SSL" in page and "WHOIS Privacy" in page
    from django.urls import reverse
    assert reverse("support_customer:new") + "?" in page
    new = client.get(reverse("support_customer:new"), {"hosting_account": account.pk, "subject": "Order: Premium SSL", "body": "Hello"})
    assert new.status_code == 200 and "Order: Premium SSL" in new.content.decode()


def test_whois_privacy_is_offered_in_the_domains_section_and_one_menu_opens_on_the_care_page(client, owner, account, manager):
    from apps.products import services as ps
    from apps.products.models import AddonKind, BillingCycle
    addon = ps.create_addon(manager, {"name": "WHOIS Privacy Protection", "kind": AddonKind.WHOIS_PRIVACY})
    ps.set_addon_status(manager, addon, "active")
    ps.set_price(manager, addon, billing_cycle=BillingCycle.ANNUAL, price="1499")
    client.force_login(owner)
    page = client.get(f"/account/hosting/{account.pk}/manage/domains/").content.decode()
    assert "WHOIS Privacy Protection" in page and "Add WHOIS privacy" in page and "domain=" not in page.split("Add WHOIS privacy")[0][-400:]
    from django.urls import reverse
    from apps.hosting import dashboard
    request = client.get(reverse("hosting_customer:care", args=[account.pk])).wsgi_request
    groups = dashboard.workspace_groups(request, account)
    assert [g.label for g in groups if g.open] == ["Website"]  # Speed test, Malware scan and Website care are one page: one menu opens, not four
