"""'Create website' on a server that cannot run our agent (cPanel today): WHPanel makes the database, writes the one-time installer, calls it, and reads what it left. Here the server is
the demo provider (real sandboxed folders and a real database list) with the call to the website faked, because the installer itself is PHP and is tested against a real WordPress
separately (apps/hosting/tests/test_wp_installer.py)."""
import json

import pytest
from django.urls import reverse

from apps.core.exceptions import ServiceError
from apps.core.test_client_area import browser, owner  # noqa: F401
from apps.hosting import control, site_apps
from apps.hosting.adapters.capabilities import Capability
from apps.hosting.models import AppState, WebsiteApp

pytestmark = pytest.mark.django_db
WP = {"type": "wordpress", "title": "Acme Shop", "admin_user": "siteadmin", "admin_email": "owner@gmail.example.com", "admin_password": "Kq7vRt2mXzLp-91"}


@pytest.fixture
def account(world, monkeypatch):
    from apps.products.models import Server, ServerKind

    account = world.objects["accounts"]["active"]
    Server.objects.filter(pk=account.server_id).update(kind=ServerKind.DEMO)
    monkeypatch.setattr(site_apps, "installs_by_agent", lambda a: False)  # pretend the server cannot run our agent
    monkeypatch.setattr(site_apps, "installs_by_script", lambda a: True)
    return account


def _folder(owner, account):
    """Empty the sample website folder: the installer only runs in an empty one."""
    for entry in control.perform(owner, account, Capability.FILES, "list_files", "public_html"):
        control.perform(owner, account, Capability.FILES, "delete_path", entry.path)


def _names(owner, account):
    return sorted(e.name for e in control.perform(owner, account, Capability.FILES, "list_files", "public_html"))


def _leave_result(owner, account, payload):
    app = WebsiteApp.objects.get(account=account)
    control.perform(owner, account, Capability.FILES, "write_file", f"public_html/{app.job_ref}", json.dumps(payload).encode(), overwrite=True)


def test_the_database_and_installer_are_made_and_the_result_is_read_later(world, owner, account, monkeypatch):  # noqa: F811
    _folder(owner, account)
    seen = {}

    def reach(acct, path, token, purpose="", domain=""):
        seen.update(path=path, token=token)
        return None  # still running: the answer comes through the result file

    monkeypatch.setattr(site_apps, "reach", reach)
    app = site_apps.start_wordpress(owner, account, title="Acme Shop", admin_user="siteadmin", admin_email="owner@gmail.example.com", admin_password=WP["admin_password"])
    assert app.state == AppState.INSTALLING and app.db_name and app.db_user and app.job_ref.startswith("whp-install-") and seen["path"] == app.installer_ref
    installer = _names(owner, account)
    assert app.installer_ref in installer  # written into the website's folder
    source = control.perform(owner, account, Capability.FILES, "read_file", f"public_html/{app.installer_ref}", max_bytes=500_000).decode()
    assert seen["token"] not in source  # the token itself is not in the file, only its hash
    assert {d.name for d in control.perform(owner, account, Capability.DATABASES, "list_databases")} >= {app.db_name}
    # nothing yet: still installing
    assert site_apps.poll(owner, account).state == AppState.INSTALLING
    _leave_result(owner, account, {"ok": True, "version": "7.1.3", "admin_url": "https://" + account.domain + "/wp-admin/"})
    done = site_apps.poll(owner, account)
    assert done.state == AppState.READY and done.version == "7.1.3" and done.admin_url.endswith("/wp-admin/")
    assert app.job_ref not in _names(owner, account)  # the result file is deleted once read
    assert WP["admin_password"] not in repr([getattr(done, f.name) for f in done._meta.fields])


def test_a_failure_reported_by_the_installer_undoes_what_was_made(world, owner, account, monkeypatch):  # noqa: F811
    _folder(owner, account)
    monkeypatch.setattr(site_apps, "reach", lambda a, p, t, purpose="", domain="": None)
    app = site_apps.start_wordpress(owner, account, title="Acme", admin_user="siteadmin", admin_email="o@gmail.example.com", admin_password=WP["admin_password"])
    db = app.db_name
    _leave_result(owner, account, {"ok": False, "error": "A download did not match its published checksum, so nothing was installed."})
    failed = site_apps.poll(owner, account)
    assert failed.state == AppState.FAILED and "checksum" in failed.error
    assert db not in {d.name for d in control.perform(owner, account, Capability.DATABASES, "list_databases")}  # the database is gone again
    assert not [n for n in _names(owner, account) if n.startswith("whp-install-")]  # and so are the installer and result files


def test_a_website_that_cannot_be_reached_fails_cleanly_with_a_reason(world, owner, account, monkeypatch):  # noqa: F811
    _folder(owner, account)

    def unreachable(acct, path, token, purpose="", domain=""):
        raise ServiceError("WHPanel could not reach your website to finish the installation (ConnectTimeout).", code="unreachable")

    monkeypatch.setattr(site_apps, "reach", unreachable)
    before = {d.name for d in control.perform(owner, account, Capability.DATABASES, "list_databases")}
    with pytest.raises(ServiceError):
        site_apps.start_wordpress(owner, account, title="Acme", admin_user="siteadmin", admin_email="o@gmail.example.com", admin_password=WP["admin_password"])
    app = WebsiteApp.objects.get(account=account)
    assert app.state == AppState.FAILED and "could not reach" in app.error
    assert {d.name for d in control.perform(owner, account, Capability.DATABASES, "list_databases")} == before and _names(owner, account) == []


def test_it_does_not_install_over_existing_files(world, owner, account, monkeypatch):  # noqa: F811
    _folder(owner, account)
    control.perform(owner, account, Capability.FILES, "write_file", "public_html/mine.php", b"<?php // mine", overwrite=False)
    monkeypatch.setattr(site_apps, "reach", lambda a, p, t, purpose="", domain="": pytest.fail("must not be called"))
    with pytest.raises(ServiceError) as err:
        site_apps.start_wordpress(owner, account, title="Acme", admin_user="siteadmin", admin_email="o@gmail.example.com", admin_password=WP["admin_password"])
    assert err.value.code == "not_empty" and _names(owner, account) == ["mine.php"]


def test_an_installation_that_never_reports_back_is_called_failed_after_ten_minutes(world, owner, account, monkeypatch):  # noqa: F811
    from datetime import timedelta

    from django.utils import timezone

    _folder(owner, account)
    monkeypatch.setattr(site_apps, "reach", lambda a, p, t, purpose="", domain="": None)
    app = site_apps.start_wordpress(owner, account, title="Acme", admin_user="siteadmin", admin_email="o@gmail.example.com", admin_password=WP["admin_password"])
    WebsiteApp.objects.filter(pk=app.pk).update(updated_at=timezone.now() - timedelta(minutes=11))
    assert site_apps.poll(owner, account).state == AppState.FAILED


def test_the_screens_poll_for_the_result(world, owner, account, monkeypatch):  # noqa: F811
    _folder(owner, account)
    monkeypatch.setattr(site_apps, "reach", lambda a, p, t, purpose="", domain="": None)
    client = browser(owner)
    assert client.post(reverse("hosting_customer:setup", args=[account.pk]), WP).status_code == 302
    page = client.get(reverse("hosting_customer:setup_status", args=[account.pk])).content.decode()
    assert "Installing WordPress" in page and 'http-equiv="refresh"' in page
    _leave_result(owner, account, {"ok": True, "version": "7.1.3", "admin_url": "https://" + account.domain + "/wp-admin/"})
    assert "Your website is ready" in client.get(reverse("hosting_customer:setup_status", args=[account.pk])).content.decode()


def test_the_panel_never_calls_a_domain_that_points_into_its_own_network(world, account, monkeypatch):  # noqa: F811
    called = []
    monkeypatch.setattr("apps.hosting.site_apps._public", lambda host: False)
    monkeypatch.setattr("requests.get", lambda *a, **k: called.append(a))
    account.server.ip_address = ""
    with pytest.raises(ServiceError):
        site_apps.reach(account, "whp-install-x.php", "t")
    assert called == []


def test_a_custom_website_gets_a_starter_page_only_when_there_is_no_index(world, owner, account):  # noqa: F811
    _folder(owner, account)
    site_apps.start_custom(owner, account)
    assert "index.html" in _names(owner, account)
    page = control.perform(owner, account, Capability.FILES, "read_file", "public_html/index.html", max_bytes=100_000).decode()
    assert account.domain in page and "ready" in page


def test_a_fresh_hosting_folder_with_the_servers_own_files_is_still_empty_enough(world, owner, account, monkeypatch):  # noqa: F811
    _folder(owner, account)
    for name in (".htaccess", ".user.ini", ".cpanel_thing", "cgi-bin/keep", "default.html", "php.ini", "error_log"):
        if "/" in name:
            control.perform(owner, account, Capability.FILES, "make_directory", "public_html/cgi-bin")
            continue
        control.perform(owner, account, Capability.FILES, "write_file", f"public_html/{name}", b"x", overwrite=False)
    monkeypatch.setattr(site_apps, "reach", lambda a, p, t, purpose="", domain="": None)
    app = site_apps.start_wordpress(owner, account, title="Acme", admin_user="siteadmin", admin_email="o@gmail.example.com", admin_password=WP["admin_password"])
    assert app.state == AppState.INSTALLING


def test_the_refusal_names_what_is_in_the_way(world, owner, account, monkeypatch):  # noqa: F811
    _folder(owner, account)
    control.perform(owner, account, Capability.FILES, "write_file", "public_html/old-site.php", b"<?php", overwrite=False)
    with pytest.raises(ServiceError) as err:
        site_apps.start_wordpress(owner, account, title="Acme", admin_user="siteadmin", admin_email="o@gmail.example.com", admin_password=WP["admin_password"])
    assert "old-site.php" in err.value.message
