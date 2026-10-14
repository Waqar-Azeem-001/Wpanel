"""The customer's screens on a cPanel-backed account (answers faked in the shapes a real server gave): parts a cPanel server does not offer are left out, never an error."""
import pytest
from django.test import override_settings

from apps.accounts.roles import Role
from apps.clients import services as client_services
from apps.hosting.adapters.whm_api import WhmApiAdapter
from apps.hosting.models import HostingAccount, HostingStatus
from apps.hosting.tests.test_cpanel_adapter import FakeWhm
from apps.hosting.tests.test_cpanel_live_shapes import DOMAINS, HOME, REAL_FTP, REAL_SSL
from apps.products import services as product_services
from apps.products.models import Product, ProductType, Server, ServerKind

pytestmark = pytest.mark.django_db

ANSWERS = {**DOMAINS, **HOME, ("uapi_cpanel", "Email::list_pops_with_disk"): [{"email": "info@acme.com", "diskused": "1.5", "diskquota": "1024"}],
           ("uapi_cpanel", "Email::list_forwarders"): [], ("uapi_cpanel", "Ftp::list_ftp"): REAL_FTP, ("uapi_cpanel", "SSL::installed_hosts"): REAL_SSL,
           ("uapi_cpanel", "Stats::get_site_errors"): ["[error] something odd"], "accountsummary": {"acct": [{"maxpop": "15", "maxftp": "unlimited", "domain": "acme.com"}]},
           ("uapi_cpanel", "Fileman::list_files"): [{"file": "public_html", "type": "dir", "nicemode": "755"}], ("uapi_cpanel", "Mime::list_redirects"): []}


@pytest.fixture
def account(staff, monkeypatch):
    manager = staff(Role.MANAGER)
    client = client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test"})
    product = Product.objects.get(pk=product_services.create_product(manager, {"name": "Starter", "type": ProductType.SHARED_HOSTING}).pk)
    server = Server.objects.create(name="cp", hostname="whm.test", kind=ServerKind.WHM_API)
    fake = FakeWhm(ANSWERS)
    monkeypatch.setattr(WhmApiAdapter, "_call", lambda self, function, params=None, **kw: fake(function, params, sensitive=kw.get("sensitive", False), raw=kw.get("raw", False)))
    return HostingAccount.objects.create(client=client, product=product, server=server, domain="acme.com", username="acme", status=HostingStatus.ACTIVE)


@pytest.fixture
def owner(account):
    return account.client.contacts.get().user


@override_settings(HOSTING_CPANEL_CAPABILITIES=["files", "ftp", "logs", "domains", "dns", "ssl", "email", "cpanel_access"])
def test_the_email_page_lists_the_mailboxes_and_leaves_out_what_cpanel_does_not_offer(client, owner, account):
    client.force_login(owner)
    page = client.get(f"/account/hosting/{account.pk}/manage/email/").content.decode()
    assert "info@acme.com" in page and "alert-danger" not in page
    assert "Spam protection" not in page and "Where your mail is delivered" not in page and "Opens in a new window" not in page


@override_settings(HOSTING_CPANEL_CAPABILITIES=["files", "ftp", "logs", "domains", "dns", "ssl", "email", "cpanel_access"])
def test_ftp_files_logs_ssl_and_domains_pages_work_for_a_cpanel_account(client, owner, account):
    client.force_login(owner)
    ftp = client.get(f"/account/hosting/{account.pk}/manage/ftp/").content.decode()
    assert "dev@acme.com" in ftp and "unlimited" in ftp and "Add an FTP account" in ftp and "disabled" not in ftp.split("Add an FTP account")[1].split("</form>")[0]
    assert "public_html" in client.get(f"/account/hosting/{account.pk}/manage/files/").content.decode()
    logs = client.get(f"/account/hosting/{account.pk}/manage/logs/").content.decode()
    assert "something odd" in logs
    assert "Self-signed" in client.get(f"/account/hosting/{account.pk}/manage/ssl/").content.decode()
    assert "acme.com" in client.get(f"/account/hosting/{account.pk}/manage/domains/").content.decode()


@override_settings(HOSTING_CPANEL_CAPABILITIES=["files", "ftp", "logs", "cpanel_access"])
def test_the_menu_shows_only_what_is_enabled_and_advanced_tools_opens_cpanel(client, owner, account):
    client.force_login(owner)
    menu = client.get(f"/account/hosting/{account.pk}/").content.decode()
    for label in ("File manager", "FTP accounts", "Logs", "Advanced tools (cPanel)"):
        assert f"{label}</a>" in menu or label in menu
    assert f"/account/hosting/{account.pk}/manage/cpanel/" in menu
    page = client.get(f"/account/hosting/{account.pk}/manage/cpanel/").content.decode()
    assert "Open cPanel" in page and "never need a cPanel password" in page


def test_nothing_is_offered_until_staff_enable_it(client, owner, account):
    client.force_login(owner)
    assert "File manager</a>" not in client.get(f"/account/hosting/{account.pk}/").content.decode()
    assert "not available for your hosting server yet" in client.get(f"/account/hosting/{account.pk}/manage/files/").content.decode()
