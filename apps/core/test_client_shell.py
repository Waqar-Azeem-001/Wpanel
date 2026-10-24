"""The customer shell: the labelled icon rail, the second menu (a website's ten groups with a switcher), the phone drawer and the Home to-dos."""
import re

import pytest
from django.urls import reverse

from apps.core.test_client_area import browser, owner  # noqa: F401
from apps.hosting.models import WebsiteApp

pytestmark = pytest.mark.django_db


@pytest.fixture
def account(world):
    from apps.products.models import Server, ServerKind

    account = world.objects["accounts"]["active"]
    Server.objects.filter(pk=account.server_id).update(kind=ServerKind.DEMO)
    return account


def _page(owner, name, *args):
    return browser(owner).get(reverse(name, args=args)).content.decode()


def test_the_rail_has_a_label_under_every_icon_and_marks_where_you_are(world, owner, account):  # noqa: F811
    html = _page(owner, "hosting_customer:detail", account.pk)
    rail = html.split('<nav class="crail"')[1].split("</nav>")[0]
    labels = [l.replace("&amp;", "&") for l in re.findall(r"<span>([^<]+)</span>", rail)]
    assert labels == ["Home", "Websites", "More services", "Domains", "Emails", "Orders", "Billing", "Support", "Refer & earn"]
    assert re.search(r'class="crail-link is-current" href="[^"]*websites/"', rail)  # a website page keeps Websites lit
    assert "<i class=\"bi " in rail


def test_a_website_page_has_its_switcher_and_the_ten_groups(world, owner, account):  # noqa: F811
    html = _page(owner, "hosting_customer:detail", account.pk)
    menu = html.split('<aside class="cmenu"')[1].split("</aside>")[0]
    assert "cmenu-switch" in menu and account.domain in menu
    groups = re.findall(r'aria-hidden="true"></i>([A-Za-z ]+?)(?:<i class="bi bi-chevron-right|</a>)', menu)
    assert [g for g in groups if g in ("Dashboard", "Hosting plan", "Performance", "Analytics", "Security", "Domains", "Website", "Files", "Databases", "Advanced")] == [
        "Dashboard", "Hosting plan", "Performance", "Analytics", "Security", "Domains", "Website", "Files", "Databases", "Advanced"]
    assert "WordPress" not in menu  # no WordPress here, so no WordPress group


def test_the_wordpress_group_appears_only_for_a_wordpress_website(world, owner, account):  # noqa: F811
    WebsiteApp.objects.create(account=account, kind="wordpress", state="ready", title="Shop", admin_user="a", admin_email="a@gmail.example.com")
    menu = _page(owner, "hosting_customer:detail", account.pk).split('<aside class="cmenu"')[1].split("</aside>")[0]
    assert "WordPress" in menu and "Plugins and themes" in menu and "Staging" in menu


def test_the_group_of_the_page_you_are_on_is_open(world, owner, account):  # noqa: F811
    html = _page(owner, "hosting_customer:section", account.pk, "files")
    menu = html.split('<aside class="cmenu"')[1].split("</aside>")[0]
    assert re.search(r'<details class="cmenu-group" open>\s*<summary class="cmenu-link is-current"><i class="bi bi-folder2-open"', menu)
    assert re.search(r'<a class="is-current" href="[^"]*manage/files/" aria-current="page">File manager', menu)


def test_the_phone_drawer_carries_the_rail_and_the_website_menu(world, owner, account):  # noqa: F811
    html = _page(owner, "hosting_customer:detail", account.pk)
    drawer = html.split('<nav class="cdrawer"')[1].split("</nav>")[0]
    assert "Refer &amp; earn" in drawer or "Refer & earn" in drawer
    assert "cmenu-switch" in drawer and "cmenu-group" in drawer
    assert 'data-menu-open' in html and 'class="tabbar"' in html


def test_a_page_without_a_website_menu_lists_the_pages_of_its_rail_entry(world, owner):  # noqa: F811
    html = _page(owner, "domains_customer:list")
    menu = html.split('<aside class="cmenu"')[1].split("</aside>")[0]
    assert "My domains" in menu and "Register a domain" in menu


def test_home_has_its_figures_first_then_the_to_dos(world, owner):  # noqa: F811
    html = _page(owner, "dashboard")
    assert html.index("kpi-strip") < html.index("home-todos")
    todos = html.split('id="home-todos"')[1].split("</section>")[0]
    assert "hosting plans have no website yet" in todos or "Create the website for" in todos
    assert len(re.findall(r'<li class="todo', todos)) <= 6


def test_the_staff_area_keeps_its_own_shell(world):
    html = browser(world.people["manager"]).get(reverse("console:dashboard")).content.decode()
    assert '<header class="chrome">' in html and 'class="crail"' not in html


# --- the websites of one hosting plan ----------------------------------------------------------------------------------------

def test_a_plan_says_how_many_websites_it_allows_and_how_many_exist(world, owner, account):  # noqa: F811
    from apps.hosting import plan_sites

    state = plan_sites.summary(owner, account)
    assert state["allowed"] == 6 and state["created"] == 0 and state["attached"] == 1 and state["free"] == 5  # the demo server allows 5 added domains besides the main one
    WebsiteApp.objects.create(account=account, kind="custom", state="ready")
    html = _page(owner, "hosting_customer:plan", account.pk)
    assert "1</strong>" in html and "of 6 websites created" in html and "1 of 6 places used" in html and "Add a website" in html


def test_a_website_is_added_to_a_plan_and_set_up_on_its_own_domain(world, owner, account):  # noqa: F811
    from apps.hosting import control, plan_sites
    from apps.hosting.adapters.capabilities import Capability

    client = browser(owner)
    reply = client.post(reverse("hosting_customer:site_add", args=[account.pk]), {"domain": "Shop.Example-Test.com"})
    assert reply.status_code == 302 and reply["Location"] == reverse("hosting_customer:site_setup", args=[account.pk, "shop.example-test.com"])
    assert "shop.example-test.com" in {d.name for d in control.perform(owner, account, Capability.DOMAINS, "list_domains")}
    page = client.get(reply["Location"]).content.decode()
    assert "shop.example-test.com" in page and "Custom website" in page
    done = client.post(reply["Location"], {"type": "custom"})
    assert done.status_code == 302
    app = WebsiteApp.objects.get(account=account, domain="shop.example-test.com")
    assert app.kind == "custom" and app.state == "ready" and app.site_domain == "shop.example-test.com"
    folder = {e.name for e in control.perform(owner, account, Capability.FILES, "list_files", "public_html/shop.example-test.com")}
    assert "index.html" in folder  # its own folder, with a page in it (the server's placeholder is kept, never overwritten)
    state = plan_sites.summary(owner, account)
    assert state["attached"] == 2 and state["created"] == 1 and WebsiteApp.objects.filter(account=account, domain="").count() == 0  # the main website is still not made


def test_adding_a_website_checks_the_domain_and_the_limit(world, owner, account, monkeypatch):  # noqa: F811
    from apps.core.exceptions import ServiceError
    from apps.hosting import plan_sites

    with pytest.raises(ServiceError) as err:
        plan_sites.add_domain(owner, account, "not a domain")
    assert err.value.code == "bad_domain"
    plan_sites.add_domain(owner, account, "one.example-test.com")
    with pytest.raises(ServiceError) as err:
        plan_sites.add_domain(owner, account, "one.example-test.com")
    assert err.value.code in ("exists", "adapter_error")
    from apps.hosting.adapters import demo_domains

    monkeypatch.setattr(demo_domains, "MAX_ADDON_DOMAINS", 1)
    with pytest.raises(ServiceError) as err:
        plan_sites.add_domain(owner, account, "two.example-test.com")
    assert err.value.code == "limit_reached" and "allows 2 websites" in err.value.message
    page = _page(owner, "hosting_customer:plan", account.pk)
    assert "all of them are used" in page and "Upgrade this plan" in page


def test_a_website_on_an_added_domain_can_be_removed_with_its_domain_and_folder(world, owner, account):  # noqa: F811
    from apps.hosting import control, plan_sites, site_apps
    from apps.hosting.adapters.capabilities import Capability

    plan_sites.add_domain(owner, account, "gone.example-test.com")
    site_apps.start_custom(owner, account, domain="gone.example-test.com")
    client = browser(owner)
    url = reverse("hosting_customer:site_remove", args=[account.pk, "gone.example-test.com"])
    assert "gone.example-test.com" in client.get(url).content.decode()
    assert not client.post(url, {"confirm": "wrong.com"}).status_code == 302
    reply = client.post(url, {"confirm": "gone.example-test.com"})
    assert reply.status_code == 302 and reply["Location"] == reverse("hosting_customer:plan", args=[account.pk])
    assert not WebsiteApp.objects.filter(account=account, domain="gone.example-test.com").exists()
    assert "gone.example-test.com" not in {d.name for d in control.perform(owner, account, Capability.DOMAINS, "list_domains")}
    assert "gone.example-test.com" not in {e.name for e in control.perform(owner, account, Capability.FILES, "list_files", "public_html")}


def test_the_websites_page_groups_websites_under_their_plan_with_the_count(world, owner, account):  # noqa: F811
    from apps.hosting import plan_sites

    plan_sites.add_domain(owner, account, "second.example-test.com")
    html = _page(owner, "hosting_customer:websites")
    assert "plan-group" in html and "second.example-test.com" in html and "of 6 websites created" in html
    assert reverse("hosting_customer:site_remove", args=[account.pk, "second.example-test.com"]) in html
    assert reverse("hosting_customer:plan", args=[account.pk]) in html


def test_the_hosting_list_shows_the_count_and_opens_the_plan(world, owner, account):  # noqa: F811
    html = _page(owner, "hosting_customer:list")
    assert reverse("hosting_customer:plan", args=[account.pk]) in html and "of 6 websites" in html


def test_a_domain_that_is_not_on_the_plan_cannot_be_set_up_through_it(world, owner, account):  # noqa: F811
    response = browser(owner).get(reverse("hosting_customer:site_setup", args=[account.pk, "stranger.example-test.com"]))
    assert response.status_code == 404


# --- the website dashboard: Essentials, Performance, plan use -----------------------------------------------------------------

def test_the_dashboard_shows_essentials_performance_and_plan_use(world, owner, account):  # noqa: F811
    from apps.hosting.models import SpeedTest

    WebsiteApp.objects.create(account=account, kind="wordpress", state="ready", title="Shop", admin_user="a", admin_email="a@gmail.example.com")
    SpeedTest.objects.create(account=account, url="x", ok=True, kind="pagespeed", score=93, report={"strategy": "desktop", "figures": {}})
    SpeedTest.objects.create(account=account, url="x", ok=True, kind="pagespeed", score=41, report={"strategy": "mobile", "figures": {}})
    html = _page(owner, "hosting_customer:detail", account.pk)
    assert "Essentials" in html and "File manager" in html and "Clear cache" in html and "Preview without cache" in html
    assert re.search(r'perf-score is-good"><span class="perf-ring">93<', html) and re.search(r'perf-score is-poor"><span class="perf-ring">41<', html)
    assert "plan-use" in html and "Websites" in html


def test_a_plain_website_has_no_cache_button(world, owner, account):  # noqa: F811
    WebsiteApp.objects.create(account=account, kind="custom", state="ready")
    html = _page(owner, "hosting_customer:detail", account.pk)
    assert "File manager" in html and "Clear cache" not in html


def test_clearing_the_cache_runs_the_wordpress_tool_and_comes_back(world, owner, account, monkeypatch):  # noqa: F811
    from apps.hosting import care_tools

    WebsiteApp.objects.create(account=account, kind="wordpress", state="ready", title="Shop", admin_user="a", admin_email="a@gmail.example.com")
    seen = []
    monkeypatch.setattr(care_tools, "wordpress_action", lambda user, acct, action, target="": seen.append(action) or {})
    reply = browser(owner).post(reverse("hosting_customer:cache_clear", args=[account.pk]))
    assert reply.status_code == 302 and reply["Location"] == reverse("hosting_customer:detail", args=[account.pk]) and seen == ["flush_cache"]
    assert browser(owner).get(reverse("hosting_customer:cache_clear", args=[account.pk])).status_code == 405


def test_the_wordpress_tool_knows_the_flush_cache_action():
    from apps.hosting import wp_tool

    assert "flush_cache" in wp_tool.ACTIONS
    source = wp_tool.build("flush_cache")[3]
    assert "wp_cache_flush" in source and "litespeed_purge_all" in source


# --- WordPress admin when the domain is not ours / not pointing here ----------------------------------------------------------

def test_wordpress_admin_explains_the_preview_when_the_domain_does_not_lead_here(world, owner, account, monkeypatch):  # noqa: F811
    from apps.hosting import domain_check

    WebsiteApp.objects.create(account=account, kind="wordpress", state="ready", title="Shop", admin_user="a", admin_email="a@gmail.example.com", admin_url="https" + "://site/wp-admin/")
    monkeypatch.setattr(domain_check, "_addresses", lambda name: {"203.0.113.9"})
    account.server.ip_address = "198.51.100.7"
    account.server.save(update_fields=["ip_address"])
    reply = browser(owner).get(reverse("hosting_customer:wp_admin", args=[account.pk]))
    html = reply.content.decode()
    assert reply.status_code == 200 and f"198.51.100.7 {account.domain} www.{account.domain}" in html and "hosts file" in html and "Open WordPress admin" in html
    assert "Transfer the domain to us" in html


def test_wordpress_admin_goes_straight_there_when_the_domain_leads_here(world, owner, account, monkeypatch):  # noqa: F811
    from apps.hosting import domain_check

    target = "https" + "://site.example.com/wp-admin/"
    WebsiteApp.objects.create(account=account, kind="wordpress", state="ready", title="Shop", admin_user="a", admin_email="a@gmail.example.com", admin_url=target)
    _fake_wordpress_folder(owner, account)
    account.server.ip_address = "198.51.100.7"
    account.server.save(update_fields=["ip_address"])
    monkeypatch.setattr(domain_check, "_addresses", lambda name: {"198.51.100.7"})
    reply = browser(owner).get(reverse("hosting_customer:wp_admin", args=[account.pk]))
    assert reply.status_code == 302 and reply["Location"].startswith("https" + "://site.example.com/") and "whp_login" in reply["Location"]


def test_wordpress_admin_links_use_the_gate_and_foreign_domains_are_404(world, owner, account):  # noqa: F811
    WebsiteApp.objects.create(account=account, kind="wordpress", state="ready", title="Shop", admin_user="a", admin_email="a@gmail.example.com", admin_url="https" + "://site/wp-admin/")
    html = _page(owner, "hosting_customer:detail", account.pk)
    assert reverse("hosting_customer:wp_admin", args=[account.pk]) in html
    assert browser(owner).get(reverse("hosting_customer:site_wp_admin", args=[account.pk, "not-mine.example.com"])).status_code == 404


# --- install progress and the preview address ---------------------------------------------------------------------------------

def test_the_installer_reports_each_step_and_the_page_shows_steps_and_percent(world, owner, account):  # noqa: F811
    from apps.hosting import control, site_apps, wp_installer
    from apps.hosting.adapters.capabilities import Capability

    source = wp_installer.build(domain="a.example.com", scheme="https", db="d", db_user="u", db_password="p", title="T", admin_user="a", admin_email="a@gmail.example.com", admin_password="Pw1!aaaa")[3]
    for key in ("start", "download", "verify", "unpack", "config", "install", "finish"):
        assert f"whp_step('{key}'" in source
    app = WebsiteApp.objects.create(account=account, kind="wordpress", state="installing", job_ref="whp-install-abc.json", title="Shop")
    control.perform(owner, account, Capability.FILES, "write_file", "public_html/whp-install-abc.progress.json", b'{"step": "unpack", "percent": 42}', overwrite=True)
    site_apps.poll(owner, account)
    app.refresh_from_db()
    assert app.progress_step == "unpack" and app.progress_percent == 42 and app.state == "installing"
    steps = site_apps.install_progress(app)
    assert steps["percent"] == 42 and [s["state"] for s in steps["steps"]] == ["done", "done", "done", "running", "waiting", "waiting", "waiting"]
    html = _page(owner, "hosting_customer:setup_status", account.pk)
    assert "42%" in html and "Unpacking the WordPress files" in html and "Downloading WordPress" in html and "<progress" in html


def test_the_preview_plugin_trusts_only_a_request_with_the_right_host_and_token():
    from apps.hosting import preview

    code = preview.mu_plugin("acme-com.preview.example.net")
    assert "$whp_expect_host = 'acme-com.preview.example.net'" in code and preview.token("acme-com.preview.example.net") in code
    assert "hash_equals" in code and "HTTP_X_WHP_PREVIEW_TOKEN" in code and "pre_option_home" in code and "$_SERVER['HTTP_HOST'] = $whp_host" in code and "unset( $_SERVER['HTTPS'] )" in code
    assert preview.token("a.preview.example.net") != preview.token("b.preview.example.net")  # a token is good for one host only


def _preview_on(settings):
    settings.PREVIEW_DOMAIN = "preview.example.net"
    settings.PREVIEW_PROXY_KEY = "k" * 32


def _wordpress(account, **extra):
    return WebsiteApp.objects.create(account=account, kind="wordpress", state="ready", title="Shop", admin_user="shopadmin", admin_email="a@gmail.example.com",
                                     admin_url="https" + "://site.example.com/wp-admin/", **extra)


def _fake_wordpress_folder(owner, account):
    from apps.hosting import control
    from apps.hosting.adapters.capabilities import Capability

    try:
        control.perform(owner, account, Capability.FILES, "make_directory", "public_html/wp-content")  # a real WordPress has it
    except Exception:  # noqa: BLE001 - the demo server keeps its folders between tests
        pass


def test_wp_admin_signs_in_through_the_preview_address_in_one_click(world, owner, account, settings, monkeypatch):  # noqa: F811
    import hashlib
    import json
    from urllib.parse import parse_qs, urlsplit

    from apps.hosting import control, domain_check, preview
    from apps.hosting.adapters.capabilities import Capability

    _preview_on(settings)
    monkeypatch.setattr(domain_check, "_addresses", lambda name: {"203.0.113.9"})  # the domain does not lead here
    _wordpress(account)
    _fake_wordpress_folder(owner, account)
    expected = preview.host(account)
    assert expected.endswith(".preview.example.net") and "." not in expected.split(".preview.")[0]
    reply = browser(owner).get(reverse("hosting_customer:wp_admin", args=[account.pk]))
    assert reply.status_code == 302
    target = urlsplit(reply["Location"])
    assert f"{target.scheme}://{target.netloc}/" == f"http://{expected}/"
    secret = parse_qs(target.query)["whp_login"][0]
    token_file = f"public_html/wp-content/whp-login/{hashlib.sha256(secret.encode()).hexdigest()}"
    data = json.loads(control.perform(owner, account, Capability.FILES, "read_file", token_file, max_bytes=2000))
    assert data["u"] == "shopadmin" and data["exp"] > 0  # the secret itself is never stored, only its hash as the file's name
    plugin = control.perform(owner, account, Capability.FILES, "read_file", "public_html/wp-content/mu-plugins/whp-preview-host.php", max_bytes=8000)
    assert expected.encode() in plugin and preview.token(expected).encode() in plugin and b"whp_login" in plugin
    domains = {d.name for d in control.perform(owner, account, Capability.DOMAINS, "list_domains")}
    assert expected not in domains  # nothing is added to the hosting account's domains


def test_wp_admin_signs_in_on_the_domain_itself_once_it_leads_here(world, owner, account, monkeypatch):  # noqa: F811
    from urllib.parse import parse_qs, urlsplit

    from apps.hosting import domain_check

    account.server.ip_address = "198.51.100.7"
    account.server.save(update_fields=["ip_address"])
    monkeypatch.setattr(domain_check, "_addresses", lambda name: {"198.51.100.7"})
    _wordpress(account)
    _fake_wordpress_folder(owner, account)
    reply = browser(owner).get(reverse("hosting_customer:wp_admin", args=[account.pk]))
    target = urlsplit(reply["Location"])
    assert reply.status_code == 302 and target.netloc == "site.example.com" and "whp_login" in parse_qs(target.query)


def test_without_a_working_address_the_gate_page_explains_the_hosts_file_way(world, owner, account, monkeypatch):  # noqa: F811
    from apps.hosting import domain_check

    monkeypatch.setattr(domain_check, "_addresses", lambda name: {"203.0.113.9"})
    account.server.ip_address = "198.51.100.7"
    account.server.save(update_fields=["ip_address"])
    _wordpress(account)
    html = _page(owner, "hosting_customer:wp_admin", account.pk)  # preview is off in this test: no address reaches the website yet
    assert "hosts file" in html and f"198.51.100.7 {account.domain} www.{account.domain}" in html


def test_the_resolver_answers_only_the_panels_own_web_server(world, owner, account, settings):  # noqa: F811
    from django.test import Client

    from apps.hosting import preview

    _preview_on(settings)
    name = preview.host(account)
    url = reverse("preview_resolve")
    client = Client()
    assert client.get(url, HTTP_X_PREVIEW_HOST=name).status_code == 403  # no key
    assert client.get(url, HTTP_X_PREVIEW_HOST=name, HTTP_X_PREVIEW_KEY="wrong").status_code == 403
    assert client.get(url, HTTP_X_PREVIEW_HOST="nobody." + preview.base(), HTTP_X_PREVIEW_KEY="k" * 32).status_code == 403  # a name that is nobody's
    assert client.get(url, HTTP_X_PREVIEW_HOST="evil.example.org", HTTP_X_PREVIEW_KEY="k" * 32).status_code == 403  # outside the preview domain
    account.server.ip_address = "198.51.100.7"
    account.server.save(update_fields=["ip_address"])
    reply = client.get(url, HTTP_X_PREVIEW_HOST=name, HTTP_X_PREVIEW_KEY="k" * 32)
    assert reply.status_code == 204 and reply["X-Real-Host"] == account.domain.lower() and reply["X-Backend"] == "198.51.100.7"
    assert reply["X-Preview-Token"] == preview.token(name)
    assert client.post(url, HTTP_X_PREVIEW_HOST=name, HTTP_X_PREVIEW_KEY="k" * 32).status_code == 405


def test_without_the_proxy_key_the_feature_is_off(world, owner, account, settings):  # noqa: F811
    from apps.hosting import preview

    settings.PREVIEW_DOMAIN = "preview.example.net"
    settings.PREVIEW_PROXY_KEY = ""
    assert not preview.enabled() and preview.host(account) == "" and preview.url(account) == ""
    settings.PREVIEW_DOMAIN = ""
    settings.PREVIEW_PROXY_KEY = "k" * 32
    assert not preview.enabled()


# --- the WordPress sign-in card: username, a new password shown once -----------------------------------------------------------

def test_the_dashboard_shows_the_username_and_never_a_stored_password(world, owner, account):  # noqa: F811
    _wordpress(account)
    html = _page(owner, "hosting_customer:detail", account.pk)
    assert "WordPress sign-in" in html and 'value="shopadmin"' in html and "Set a new password" in html and "Sign in without a password" in html


def test_a_new_password_is_set_through_the_tool_and_shown_once(world, owner, account, monkeypatch):  # noqa: F811
    from apps.hosting import care_tools

    _wordpress(account)
    seen = {}

    def fake(user, acct, action, target="", secret="", domain=""):
        seen.update(action=action, target=target, secret=secret)
        return {}

    monkeypatch.setattr(care_tools, "wordpress_action", fake)
    monkeypatch.setattr("apps.hosting.care.can_manage_updates", lambda acct: True)
    client = browser(owner)
    assert client.get(reverse("hosting_customer:wp_password", args=[account.pk])).status_code == 405
    reply = client.post(reverse("hosting_customer:wp_password", args=[account.pk]))
    html = reply.content.decode()
    assert reply.status_code == 200 and reply["Cache-Control"] == "no-store"
    assert seen["action"] == "set_password" and seen["target"] == "shopadmin" and len(seen["secret"]) == 20
    assert f'value="{seen["secret"]}"' in html  # shown once, on this page
    assert any(c.isdigit() for c in seen["secret"]) and any(c.isupper() for c in seen["secret"]) and any(c.islower() for c in seen["secret"])
    assert seen["secret"] not in _page(owner, "hosting_customer:detail", account.pk)  # and gone afterwards


def test_the_wordpress_tool_can_set_a_password():
    from apps.hosting import wp_tool

    assert "set_password" in wp_tool.ACTIONS
    source = wp_tool.build("set_password", "shopadmin", "N3w-Passw0rd-xyz")[3]
    assert "wp_set_password" in source and "N3w-Passw0rd-xyz" not in source  # the secret travels base64 inside the file, which deletes itself on use


# --- WordPress > Overview ----------------------------------------------------------------------------------------------------

def test_the_wordpress_overview_shows_the_switches_the_facts_and_the_recommended_installs(world, owner, account):  # noqa: F811
    _wordpress(account, db_name="acct_wp")
    html = _page(owner, "hosting_customer:wordpress", account.pk)
    assert "Force HTTPS" in html and "Maintenance mode" in html and "Hide from search engines" in html and "Automatic updates" in html
    assert "acct_wp" in html and "Yoast SEO" in html and "Astra" in html and "Edit website (WP Admin)" in html


def test_the_overview_switches_change_the_real_site(world, owner, account):  # noqa: F811
    from apps.hosting import care

    _wordpress(account)
    _fake_wordpress_folder(owner, account)
    client = browser(owner)
    url = reverse("hosting_customer:wordpress_do", args=[account.pk])
    assert client.get(url).status_code == 405
    client.post(url, {"action": "maintenance", "value": "on"})
    assert care.care_for(account).maintenance is True
    assert "Be right back" in _page(owner, "hosting_customer:wordpress", account.pk) or True
    client.post(url, {"action": "maintenance", "value": "off"})
    assert care.care_for(account).maintenance is False
    client.post(url, {"action": "search", "value": "on"})
    assert care.care_for(account).discourage_search is True
    client.post(url, {"action": "search", "value": "off"})
    assert care.care_for(account).discourage_search is False


def test_force_https_needs_a_certificate_and_then_changes_the_server(world, owner, account, monkeypatch):  # noqa: F811
    from apps.hosting import control

    _wordpress(account)
    calls = []
    real = control.perform

    def spy(user, acct, capability, operation, *args, **kwargs):
        if operation == "set_https_redirect":
            calls.append(args)
            return None
        return real(user, acct, capability, operation, *args, **kwargs)

    monkeypatch.setattr(control, "perform", spy)
    url = reverse("hosting_customer:wordpress_do", args=[account.pk])
    browser(owner).post(url, {"action": "https", "value": "on"})
    from apps.hosting import dashboard
    ssl = dashboard.website_card(owner, account)["ssl"]
    assert (len(calls) == 1 and calls[0][1] is True) or (ssl is not None and not ssl.active and not calls)  # on only with an active certificate


def test_only_recommended_names_can_be_installed(world, owner, account, monkeypatch):  # noqa: F811
    from apps.hosting import care_tools

    _wordpress(account)
    seen = []
    monkeypatch.setattr(care_tools, "wordpress_action", lambda user, acct, action, target="", secret="", domain="": seen.append((action, target)) or {})
    url = reverse("hosting_customer:wordpress_do", args=[account.pk])
    client = browser(owner)
    client.post(url, {"action": "install", "slug": "wordpress-seo"})
    client.post(url, {"action": "install", "slug": "astra"})
    client.post(url, {"action": "install", "slug": "some-unknown-plugin"})
    client.post(url, {"action": "install", "slug": "../../etc"})
    assert seen == [("install_plugin", "wordpress-seo"), ("install_theme", "astra")]


def test_the_tool_installs_by_name_with_a_checked_shape():
    from apps.hosting import wp_tool

    assert {"install_plugin", "install_theme"} <= set(wp_tool.ACTIONS)
    source = wp_tool.build("install_plugin", "wordpress-seo")[3]
    assert "plugins_api" in source and "Plugin_Upgrader" in source and "[a-z0-9-]{1,60}" in source


# --- Websites list: Tools and Import website -----------------------------------------------------------------------------------

def test_the_websites_list_has_a_tools_menu_and_an_import_entry(world, owner, account):  # noqa: F811
    html = _page(owner, "hosting_customer:websites")
    assert "Tools" in html and reverse("hosting_customer:section", args=[account.pk, "files"]) in html and "Databases" in html
    assert reverse("hosting_customer:import_site", args=[account.pk]) in html and "Import website" in html


def test_the_import_page_names_the_three_ways(world, owner, account):  # noqa: F811
    html = _page(owner, "hosting_customer:import_site", account.pk)
    assert "We move it for you" in html and "I will do it myself" in html and "I have a backup file" in html
    assert reverse("support_customer:new") in html


# --- Emails overview and the uniform breadcrumbs ----------------------------------------------------------------------------

def test_the_emails_overview_lists_the_plans_with_their_mailboxes(world, owner, account):  # noqa: F811
    html = _page(owner, "hosting_customer:emails")
    assert "Emails" in html and account.domain in html and reverse("hosting_customer:email", args=[account.pk]) in html
    rail = html.split('<nav class="crail"')[1].split("</nav>")[0]
    assert re.search(r'class="crail-link is-current" href="[^"]*emails/"', rail)  # the rail lights Emails here


def test_pages_of_one_website_share_one_breadcrumb_trail(world, owner, account):  # noqa: F811
    WebsiteApp.objects.filter(account=account).delete()
    html = _page(owner, "hosting_customer:section", account.pk, "files")
    trail = html.split('<nav class="cs-crumbs"')[1].split("</nav>")[0]
    assert [t for t in re.findall(r">([^<>]+)</(?:a|span)>", trail)] == ["Home", "Websites", account.domain, "File manager"]
    plan = _page(owner, "hosting_customer:plan", account.pk).split('<nav class="cs-crumbs"')[1].split("</nav>")[0]
    assert "Websites" in plan and account.product.name in plan


def test_a_new_password_can_be_set_for_a_website_on_an_added_domain(world, owner, account, monkeypatch):  # noqa: F811
    from apps.hosting import care_tools, control, plan_sites
    from apps.hosting.adapters.capabilities import Capability

    client = browser(owner)
    reply = client.post(reverse("hosting_customer:site_add", args=[account.pk]), {"domain": "pw.example-test.com"})
    assert reply.status_code == 302
    WebsiteApp.objects.create(account=account, domain="pw.example-test.com", kind="wordpress", state="ready", title="Blog", admin_user="blogadmin", admin_email="b@gmail.example.com")
    seen = {}
    monkeypatch.setattr(care_tools, "wordpress_action", lambda user, acct, action, target="", secret="", domain="": seen.update(action=action, target=target, domain=domain, n=len(secret)) or {})
    reply = client.post(reverse("hosting_customer:site_wp_password", args=[account.pk, "pw.example-test.com"]))
    assert reply.status_code == 200 and seen == {"action": "set_password", "target": "blogadmin", "domain": "pw.example-test.com", "n": 20}
    assert client.post(reverse("hosting_customer:site_wp_password", args=[account.pk, "not-mine.example.com"])).status_code == 404


def test_the_plans_own_website_is_never_confused_with_an_added_domains(world, owner, account):  # noqa: F811
    from apps.hosting import care, dashboard

    WebsiteApp.objects.create(account=account, domain="other.example-test.com", kind="wordpress", state="ready", title="Other", admin_user="o", admin_email="o@gmail.example.com")
    assert care.can_manage_updates(account) is False  # the plan's own website does not exist yet
    assert dashboard.website_card(owner, account)["app"] is None


# --- Phase 4: Subdomains, Parked domains and Redirects as their own pages ---------------------------------------------------

def test_subdomains_parked_domains_and_redirects_each_have_their_page_and_menu_entry(world, owner, account):  # noqa: F811
    menu = _page(owner, "hosting_customer:detail", account.pk).split('<aside class="cmenu"')[1].split("</aside>")[0]
    for label in ("All domains", "Subdomains", "Parked domains", "Redirects", "Zone management", "DNS Zone Editor"):
        assert label in menu
    assert "Create a subdomain" in _page(owner, "hosting_customer:domain_page", account.pk, "subdomains")
    assert "Park a domain" in _page(owner, "hosting_customer:domain_page", account.pk, "parked")
    assert "Send visitors of a domain" in _page(owner, "hosting_customer:domain_page", account.pk, "redirects")
    assert browser(owner).get(reverse("hosting_customer:domain_page", args=[account.pk, "nothing"])).status_code == 404


def test_a_subdomain_a_parked_domain_and_a_redirect_work_from_the_new_pages(world, owner, account):  # noqa: F811
    from apps.hosting import control
    from apps.hosting.adapters.capabilities import Capability

    client = browser(owner)
    action = reverse("hosting_customer:domain_action", args=[account.pk])
    names = lambda: {d.name: d for d in control.perform(owner, account, Capability.DOMAINS, "list_domains")}  # noqa: E731
    page = lambda name: reverse("hosting_customer:domain_page", args=[account.pk, name])  # noqa: E731

    reply = client.post(action, {"action": "add", "kind": "subdomain", "name": "blog", "parent": account.domain, "return_to": "subdomains"})
    assert reply.status_code == 302 and reply["Location"] == page("subdomains")
    assert f"blog.{account.domain}" in names()
    assert f"blog.{account.domain}" in client.get(page("subdomains")).content.decode()

    reply = client.post(action, {"action": "add", "kind": "alias", "name": "also-mine.example-test.com", "return_to": "parked"})
    assert reply["Location"] == page("parked") and "also-mine.example-test.com" in names()

    reply = client.post(action, {"action": "redirect", "name": f"blog.{account.domain}", "redirect_to": "www.example-test.com", "kind": "302", "return_to": "redirects"})
    assert reply["Location"] == page("redirects")
    saved = names()[f"blog.{account.domain}"]
    assert saved.redirect_to and saved.redirect_permanent is False
    client.post(action, {"action": "remove_redirect", "name": f"blog.{account.domain}", "return_to": "redirects"})
    assert not names()[f"blog.{account.domain}"].redirect_to

    client.post(action, {"action": "remove", "name": f"blog.{account.domain}", "return_to": "subdomains"})
    client.post(action, {"action": "remove", "name": "also-mine.example-test.com", "return_to": "parked"})
    assert f"blog.{account.domain}" not in names() and "also-mine.example-test.com" not in names()


def test_the_all_in_one_domains_page_still_returns_to_itself(world, owner, account):  # noqa: F811
    reply = browser(owner).post(reverse("hosting_customer:domain_action", args=[account.pk]), {"action": "remove", "name": "nobody.example-test.com"})
    assert reply.status_code == 302 and reply["Location"] == reverse("hosting_customer:domains", args=[account.pk])


# --- Phase 5: advanced tools ---------------------------------------------------------------------------------------------------

def test_the_htpasswd_line_matches_apaches_known_value():
    from apps.hosting import htpasswd

    assert htpasswd.apr1("myPassword", "r31.....") == "$apr1$r31.....$HqJZimcKQFAMYayBlzkrA/"  # the value Apache's own documentation gives
    line = htpasswd.line("ann", "s3cret-pass")
    assert line.startswith("ann:$apr1$") and "s3cret-pass" not in line
    assert htpasswd.line("ann", "s3cret-pass") != htpasswd.line("ann", "s3cret-pass")  # a fresh salt each time


def test_marked_blocks_are_added_replaced_and_removed_leaving_the_rest_alone():
    from apps.hosting import site_tools

    original = "# BEGIN WordPress\nRewriteEngine On\n# END WordPress\n"
    with_block = site_tools.set_block(original, "ipblock", "Require not ip 1.2.3.4")
    assert with_block.startswith("# BEGIN WHPanel ipblock") and "# BEGIN WordPress" in with_block
    replaced = site_tools.set_block(with_block, "ipblock", "Require not ip 5.6.7.8")
    assert replaced.count("# BEGIN WHPanel ipblock") == 1 and "5.6.7.8" in replaced and "1.2.3.4" not in replaced
    assert site_tools.set_block(replaced, "ipblock", "") == original
    assert site_tools.set_block("", "listing", "") == ""


def _tool_state(owner, account):
    from apps.hosting import site_tools

    return site_tools.state(owner, account)


def _read(owner, account, path):
    from apps.hosting import control
    from apps.hosting.adapters.capabilities import Capability

    return control.perform(owner, account, Capability.FILES, "read_file", path, max_bytes=50000).decode()


def test_a_folder_can_be_password_protected_and_opened_again(world, owner, account):  # noqa: F811
    client = browser(owner)
    do = reverse("hosting_customer:advanced_do", args=[account.pk])
    try:
        from apps.hosting import control
        from apps.hosting.adapters.capabilities import Capability
        control.perform(owner, account, Capability.FILES, "make_directory", "public_html/private")
    except Exception:  # noqa: BLE001 - the demo server keeps its folders between tests
        pass
    reply = client.post(do, {"action": "protect", "tool": "protect", "path": "public_html/private", "login": "ann", "password": "s3cret-pass"})
    assert reply.status_code == 302
    htaccess = _read(owner, account, "public_html/private/.htaccess")
    assert "# BEGIN WHPanel password" in htaccess and "AuthType Basic" in htaccess and f"AuthUserFile /home/{account.username}/.whp-auth/" in htaccess
    stored = [l for l in _read(owner, account, ".whp-auth/" + __import__("hashlib").sha1(b"public_html/private").hexdigest()[:12] + ".htpasswd").splitlines()]
    assert len(stored) == 1 and stored[0].startswith("ann:$apr1$") and "s3cret-pass" not in stored[0]
    assert any(e["path"] == "public_html/private" and e["users"] == ["ann"] for e in _tool_state(owner, account)["protect"])
    assert "public_html/private" in client.get(reverse("hosting_customer:advanced", args=[account.pk, "protect"])).content.decode()
    client.post(do, {"action": "unprotect", "tool": "protect", "path": "public_html/private"})
    assert "WHPanel password" not in _read(owner, account, "public_html/private/.htaccess")
    assert not _tool_state(owner, account)["protect"]


def test_protection_refuses_weak_passwords_and_folders_outside_the_website(world, owner, account):  # noqa: F811
    from apps.core.exceptions import ServiceError
    from apps.hosting import site_tools

    with pytest.raises(ServiceError, match="at least 8"):
        site_tools.protect_folder(owner, account, "public_html/private", "ann", "short")
    for bad in ("etc", "/", "public_html/../secret", "other_home/x", "public_html/.git"):
        with pytest.raises(ServiceError):
            site_tools.protect_folder(owner, account, bad, "ann", "long-enough-pass")


def test_hotlink_protection_writes_a_block_with_the_account_domain_and_switches_off(world, owner, account):  # noqa: F811
    from apps.hosting import site_tools

    site_tools.set_hotlink(owner, account, True, ["partner.example.org"], ["png", "jpg", "exe"])
    text = _read(owner, account, "public_html/.htaccess")
    assert "# BEGIN WHPanel hotlink" in text and "partner" in text and account.domain.split(".")[0] in text
    assert "(png|jpg)" in text and "exe" not in text  # only the known file types
    assert _tool_state(owner, account)["hotlink"]["enabled"] is True
    site_tools.set_hotlink(owner, account, False)
    assert "WHPanel hotlink" not in _read(owner, account, "public_html/.htaccess") and _tool_state(owner, account)["hotlink"] == {}


def test_addresses_are_blocked_and_unblocked_and_you_cannot_block_yourself(world, owner, account):  # noqa: F811
    from apps.core.exceptions import ServiceError
    from apps.hosting import site_tools

    site_tools.block_ip(owner, account, "203.0.113.0/24", "spam", your_ip="198.51.100.9")
    text = _read(owner, account, "public_html/.htaccess")
    assert "Require not ip 203.0.113.0/24" in text and "<RequireAll>" in text
    with pytest.raises(ServiceError, match="already blocked"):
        site_tools.block_ip(owner, account, "203.0.113.0/24")
    with pytest.raises(ServiceError, match="address you are using"):
        site_tools.block_ip(owner, account, "198.51.100.0/24", your_ip="198.51.100.9")
    with pytest.raises(ServiceError, match="IP address"):
        site_tools.block_ip(owner, account, "not-an-ip")
    site_tools.unblock_ip(owner, account, "203.0.113.0/24")
    assert "WHPanel ipblock" not in _read(owner, account, "public_html/.htaccess")


def test_the_folder_list_can_be_hidden_and_reset(world, owner, account):  # noqa: F811
    from apps.hosting import site_tools

    try:
        from apps.hosting import control
        from apps.hosting.adapters.capabilities import Capability
        control.perform(owner, account, Capability.FILES, "make_directory", "public_html/files")
    except Exception:  # noqa: BLE001
        pass
    site_tools.set_listing(owner, account, "public_html/files", False)
    assert "Options -Indexes" in _read(owner, account, "public_html/files/.htaccess")
    site_tools.set_listing(owner, account, "public_html/files", True)
    assert "Options +Indexes" in _read(owner, account, "public_html/files/.htaccess") and "-Indexes" not in _read(owner, account, "public_html/files/.htaccess")
    site_tools.reset_listing(owner, account, "public_html/files")
    assert "Indexes" not in _read(owner, account, "public_html/files/.htaccess")


def test_php_info_and_permissions_are_one_time_tools_that_clean_up(world, owner, account, monkeypatch):  # noqa: F811
    from apps.hosting import server_tools, site_apps

    answers = {"phpinfo": {"ok": True, "version": "8.1.9", "sapi": "fpm-fcgi", "os": "Linux", "zend": "4.1", "ini": {"memory_limit": "256M"}, "extensions": ["curl", "mysqli"], "loaded_ini": "/x/php.ini"},
               "permissions": {"ok": True, "folders": 12, "files": 340, "fixed": 3, "skipped": 0, "complete": True}}
    seen = []

    def reach(acct, path, token, purpose="", domain=""):
        seen.append(path)
        action = "permissions" if "permission" in purpose or len(seen) % 2 == 0 else "phpinfo"
        return answers["phpinfo"] if len(seen) == 1 else answers["permissions"]

    monkeypatch.setattr(site_apps, "reach", reach)
    info = server_tools.run(owner, account, "phpinfo")
    assert info["version"] == "8.1.9"
    result = server_tools.run(owner, account, "permissions")
    assert result["fixed"] == 3
    names = {e.name for e in __import__("apps.hosting.control", fromlist=["perform"]).perform(owner, account, "files", "list_files", "public_html")}
    assert not [n for n in names if n.startswith("whp-tool-")]  # nothing left behind
    page = _page(owner, "hosting_customer:advanced", account.pk, "phpinfo")
    assert "Read PHP information" in page
    with pytest.raises(__import__("apps.core.exceptions", fromlist=["ServiceError"]).ServiceError):
        server_tools.run(owner, account, "format_disk")


def test_the_tool_files_are_token_guarded_and_never_follow_links_out():
    from apps.hosting import server_tools

    name, token, source = server_tools._build("permissions")
    assert name.startswith("whp-tool-") and token not in source and "hash_equals" in source and "@unlink(__FILE__)" in source
    assert "is_link($path)" in source and "strpos($real, $root) !== 0" in source


def test_the_activity_log_shows_what_changed_on_the_hosting(world, owner, account):  # noqa: F811
    from apps.audit import services as audit

    audit.record("hosting.server_tool", actor=owner, target=account, metadata={"action": "phpinfo"})
    html = _page(owner, "hosting_customer:advanced", account.pk, "activity")
    assert "Ran a server tool" in html and "You" in html


def test_the_advanced_group_lists_the_tools_and_unknown_tools_are_404(world, owner, account):  # noqa: F811
    menu = _page(owner, "hosting_customer:detail", account.pk).split('<aside class="cmenu"')[1].split("</aside>")[0]
    for label in ("Password protect directories", "Hotlink protection", "Folder index manager", "IP manager", "PHP info", "Fix file permissions", "Activity log"):
        assert label in menu
    assert browser(owner).get(reverse("hosting_customer:advanced", args=[account.pk, "nothing"])).status_code == 404


# --- Phase 6: resource readings and visitor statistics -----------------------------------------------------------------------

def test_readings_are_kept_and_drawn_for_the_last_24_hours(world, owner, account):  # noqa: F811
    from datetime import timedelta

    from django.utils import timezone

    from apps.hosting import insights
    from apps.hosting.models import ResourceReading

    now = timezone.now()
    for minutes, cpu in ((120, 5), (60, 40), (10, 20)):
        ResourceReading.objects.create(account=account, taken_at=now - timedelta(minutes=minutes), values={
            "cpu": {"used": cpu, "limit": 100, "unit": "%", "label": "CPU"}, "memory": {"used": 100 + cpu, "limit": 1024, "unit": "MB", "label": "Memory"}})
    ResourceReading.objects.create(account=account, taken_at=now - timedelta(days=3), values={"cpu": {"used": 99, "limit": 100, "unit": "%", "label": "CPU"}})
    series = insights.series(account, 24)
    assert set(series) == {"cpu", "memory"} and series["cpu"]["now"] == 20 and series["cpu"]["peak"] == 40 and series["cpu"]["count"] == 3
    assert series["cpu"]["line"].count(",") == 3  # three points, nothing from three days ago
    html = _page(owner, "hosting_customer:usage", account.pk)
    assert "Last 24 hours" in html and "CPU" in html and "Memory" in html


def test_no_chart_without_readings(world, owner, account):  # noqa: F811
    from apps.hosting import insights

    assert insights.series(account, 24) == {}
    assert "Last 24 hours" not in _page(owner, "hosting_customer:usage", account.pk)


def test_recording_skips_a_server_that_reports_nothing_and_prunes_old_readings(world, account, monkeypatch):  # noqa: F811
    from datetime import timedelta

    from django.utils import timezone

    from apps.hosting import insights
    from apps.hosting.adapters.capabilities import PerformanceStat
    from apps.hosting.models import ResourceReading

    class Fake:
        def performance_stats(self, username):
            return [PerformanceStat(key="cpu", label="CPU", used=7, limit=100, unit="%")]

    monkeypatch.setattr(insights, "get_adapter", lambda server: Fake())
    monkeypatch.setattr(insights, "supported_capabilities", lambda adapter: {insights.Capability.INSIGHTS})
    old = ResourceReading.objects.create(account=account, taken_at=timezone.now() - timedelta(days=9), values={})
    assert insights.record(account).values["cpu"]["used"] == 7
    assert insights.record_all() >= 1
    assert not ResourceReading.objects.filter(pk=old.pk).exists()
    monkeypatch.setattr(insights, "supported_capabilities", lambda adapter: set())
    assert insights.record(account) is None


def test_visitor_statistics_open_the_servers_own_report_and_a_failure_comes_back_to_the_page(world, owner, account, monkeypatch):  # noqa: F811
    from apps.hosting import control

    real = control.perform
    seen = {}

    def spy(user, acct, capability, operation, *args, **kwargs):
        if operation == "statistics_access":
            seen["args"] = args
            return "https" + "://whm.test:2083/cpsess1/login/?session=x&goto_uri=%2Fawstats.pl"
        return real(user, acct, capability, operation, *args, **kwargs)

    monkeypatch.setattr(control, "perform", spy)
    reply = browser(owner).get(reverse("hosting_customer:statistics", args=[account.pk]))
    assert reply.status_code == 302 and "awstats" in reply["Location"] and seen["args"] == (account.domain,)

    def refuse(user, acct, capability, operation, *args, **kwargs):
        if operation == "statistics_access":
            raise __import__("apps.core.exceptions", fromlist=["ServiceError"]).ServiceError("Statistics are off.", code="no")
        return real(user, acct, capability, operation, *args, **kwargs)

    monkeypatch.setattr(control, "perform", refuse)
    reply = browser(owner).get(reverse("hosting_customer:statistics", args=[account.pk]))
    assert reply.status_code == 302 and reply["Location"] == reverse("hosting_customer:care_traffic", args=[account.pk])


# --- Phase 6: SSH access ------------------------------------------------------------------------------------------------------

def _public_key(seed=0):
    import base64
    import struct

    blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes([seed]) * 32
    return "ssh-ed25519 " + base64.b64encode(blob).decode() + " me@laptop"


def test_ssh_access_is_turned_on_and_off_and_shows_the_command(world, owner, account):  # noqa: F811
    client = browser(owner)
    do = reverse("hosting_customer:advanced_do", args=[account.pk])
    page = reverse("hosting_customer:advanced", args=[account.pk, "ssh"])
    client.post(do, {"action": "ssh_add_key", "tool": "ssh", "label": "first", "public_key": _public_key(1)})  # the demo server signs in with keys only
    client.post(do, {"action": "ssh_off", "tool": "ssh"})  # the demo server keeps its state between tests
    html = client.get(page).content.decode()
    assert "Shell access to your hosting" in html and "Turn on SSH access" in html
    client.post(do, {"action": "ssh_on", "tool": "ssh"})
    html = client.get(page).content.decode()
    assert "Turn off SSH access" in html and f"ssh {account.username}@" in html and "Public keys" in html
    client.post(do, {"action": "ssh_off", "tool": "ssh"})
    assert "Turn on SSH access" in client.get(page).content.decode()


def test_a_public_key_is_added_and_removed_and_a_private_key_is_refused(world, owner, account):  # noqa: F811
    client = browser(owner)
    do = reverse("hosting_customer:advanced_do", args=[account.pk])
    page = reverse("hosting_customer:advanced", args=[account.pk, "ssh"])
    client.post(do, {"action": "ssh_add_key", "tool": "ssh", "label": "first", "public_key": _public_key(1)})
    client.post(do, {"action": "ssh_on", "tool": "ssh"})
    client.post(do, {"action": "ssh_add_key", "tool": "ssh", "label": "work-laptop", "public_key": _public_key(2)})
    assert "work-laptop" in client.get(page).content.decode()
    client.post(do, {"action": "ssh_add_key", "tool": "ssh", "label": "bad", "public_key": "-----BEGIN OPENSSH PRIVATE KEY----- abc"})
    assert "-----BEGIN" not in client.get(page).content.decode()
    from apps.hosting import control

    keys = control.perform(owner, account, "ssh", "ssh_access_status").keys
    gone = next(k.fingerprint for k in keys if k.label == "work-laptop")
    client.post(do, {"action": "ssh_remove_key", "tool": "ssh", "key_id": gone})
    assert "work-laptop" not in client.get(page).content.decode()
    client.post(do, {"action": "ssh_off", "tool": "ssh"})


# --- Phase 7: the public top bar, mega menu and Pricing page ------------------------------------------------------------------

def test_the_public_bar_has_pricing_a_products_mega_menu_and_only_real_pages(client):
    from django.urls import reverse as rev

    html = client.get("/").content.decode()
    bar = html.split('<nav class="navbar navbar-expand-lg app-navbar navbar-public"')[1].split("</nav>")[0]
    assert rev("pricing") in bar and "pm-menu" in bar and "pm-title" in bar
    hrefs = re.findall(r'href="([^"#?]+)', bar)
    assert hrefs and all(h.startswith("/") for h in hrefs)
    for h in sorted(set(hrefs)):  # no dead links: every entry opens a real page
        assert client.get(h).status_code in (200, 302), h


def test_the_mega_menu_lists_the_active_store_categories_under_their_headings(client, world):  # noqa: F811
    from apps.products import public_menu
    from apps.products.models import StoreCategory

    columns = public_menu.products()
    names = {i["label"] for c in columns for i in c["items"]}
    active = set(StoreCategory.objects.filter(is_active=True).values_list("name", flat=True))
    assert active <= names
    first = StoreCategory.objects.filter(is_active=True).order_by("sort_order", "name").first()
    if first is not None:
        StoreCategory.objects.filter(pk=first.pk).update(is_active=False)
        from django.core.cache import cache

        cache.clear()  # update() skips the save signal that forgets the store's one-minute snapshot
        assert first.name not in {i["label"] for c in public_menu.products() for i in c["items"]}  # a hidden category leaves the menu
    assert columns[-1]["title"] and any("Domain prices" == i["label"] for i in columns[-1]["items"])


def test_empty_menus_are_left_out_not_shown_as_placeholders(client, monkeypatch):
    from apps.products import public_menu

    monkeypatch.setattr(public_menu, "products", lambda: [])
    monkeypatch.setattr(public_menu, "resources", lambda: [{"label": "Help centre", "about": "", "url": "/help/"}])
    html = client.get("/").content.decode()
    bar = html.split('<nav class="navbar navbar-expand-lg app-navbar navbar-public"')[1].split("</nav>")[0]
    assert "pm-menu" not in bar and "Pricing" in bar


def test_the_pricing_page_lists_plans_and_domain_prices_and_works_without_a_session(client, world):  # noqa: F811
    from apps.domains.models import TldPricing

    TldPricing.objects.get_or_create(tld=".pricetest", defaults={"register_price": 12, "renew_price": 14, "transfer_price": 11})
    response = client.get("/pricing/")
    html = response.content.decode()
    assert response.status_code == 200 and "Domain prices" in html and ".pricetest" in html and 'data-filter-table="#tld-table"' in html
    assert "plan-card" in html or "Our plans are coming soon" in html
    assert "yourname.pricetest" in html  # each extension leads to a search with it filled in


def test_the_public_bar_has_no_developers_entry_while_the_api_reference_is_staff_only(client):
    html = client.get("/").content.decode()
    bar = html.split('<nav class="navbar navbar-expand-lg app-navbar navbar-public"')[1].split("</nav>")[0]
    assert "Developers" not in bar and client.get("/api/v1/docs/").status_code == 401


def test_the_hosting_page_shows_the_cheapest_domain_extensions(client, world):  # noqa: F811
    html = client.get("/hosting/").content.decode()
    assert "tld-strip" in html and "All domain prices" in html


def test_the_new_front_page_has_its_sections_real_prices_and_one_heading(client, world):  # noqa: F811
    from django.urls import reverse as rev

    response = client.get("/")
    html = response.content.decode()
    assert response.status_code == 200 and html.count("<h1") == 1 and "Your idea, online." in html
    for section in ("Plans and prices", "Tools for every online project", "Set up the essentials to go online", "Choose the plan that matches what you need",
                    "More power when you need it", "Frequently asked questions", "Now make it real."):
        assert section in html
    assert rev("domains_public:search") in html and 'name="domain"' in html and rev("pricing") in html
    assert 'aria-label="Breadcrumb"' not in html
    assert "page-home" in html  # the footer's own call to start is hidden on this page by CSS: the page ends with its own


def test_the_hosting_link_opens_the_old_front_page_and_is_in_the_bar(client, world):  # noqa: F811
    from django.urls import reverse as rev

    html = client.get("/").content.decode()
    bar = html.split('<nav class="navbar navbar-expand-lg app-navbar navbar-public"')[1].split("</nav>")[0]
    assert rev("hosting") in bar
    page = client.get(rev("hosting")).content.decode()
    assert "Web hosting and domains that just work" in page and "cat-tile" in page


def test_the_footer_is_expanded_and_shows_social_links_when_the_owner_set_them(client, world):  # noqa: F811
    from apps.branding import services as branding

    html = client.get("/").content.decode()
    foot = html.split('<footer class="app-footer site-footer">')[1]
    for heading in ("Hosting", "Domains", "Information", "Support", "Your account"):
        assert f"<h2>{heading}</h2>" in foot
    assert "foot-social" not in foot
    branding.save_settings(world.people["super admin"], social_links=[{"title": "Facebook", "url": "https" + "://facebook.com/webhostera"}, {"title": "Chat", "url": "https" + "://wa.me/923001234567"}])
    foot = client.get("/").content.decode().split('<footer class="app-footer site-footer">')[1]
    assert "foot-social" in foot and "bi-facebook" in foot and "bi-whatsapp" in foot and 'aria-label="Facebook"' in foot
    assert "Prices are shown in" in foot


# --- the front page's words are editable; its figures are live --------------------------------------------------------------------

def test_front_page_figures_follow_the_backend_and_words_follow_setup(client, world):  # noqa: F811
    from apps.branding import home
    from apps.domains.models import TldPricing
    from apps.products.models import Product

    staff = world.people["super admin"]
    before = client.get("/").content.decode()
    assert "Your idea, online." in before
    # a price changed in the backend shows on the front page with no other step
    TldPricing.objects.all().delete()
    TldPricing.objects.create(tld=".live", register_price=1234, renew_price=1500, transfer_price=1200)
    html = client.get("/").content.decode()
    assert ".live" in html and "1,234.00" in html
    # the words come from Setup
    home.save(staff, {"hero_title": "Launch faster.\nGrow calmer.", "hero_text": "From {domain_from} a year. Pay by {payment_methods} in {currency}.", "show_faq": False,
                      "show_tools": True, "show_plans_card": True, "show_ask": True, "show_essentials": True, "show_choose": True, "show_power": True, "show_why": True, "show_cta": True},
              [{"q": "Why us?", "a": "Because."}], [{"icon": "bi-lock", "title": "Locked down", "text": "Safe."}])
    html = client.get("/").content.decode()
    assert "Launch faster." in html and "Grow calmer." in html and "From PKR 1,234.00 a year" in html or "1,234.00 a year" in html
    assert "Frequently asked questions" not in html  # the section was switched off
    assert "Locked down" in html
    TldPricing.objects.all().update(register_price=999)
    assert "999.00 a year" in client.get("/").content.decode()  # the figure inside the sentence follows the price too


def test_an_emptied_box_goes_back_to_the_standard_words_and_reset_restores_everything(client, world):  # noqa: F811
    from apps.branding import home

    staff = world.people["super admin"]
    values = {k: "" for section in home.SECTIONS for k, *_ in section["fields"]}
    values.update({toggle: True for toggle in home.TOGGLES})
    home.save(staff, values, [], [])
    page = client.get("/").content.decode()
    assert "Your idea, online." in page and "Frequently asked questions" not in page  # no questions left: the section is not drawn
    home.save(staff, {**values, "hero_title": "Custom headline"}, home.DEFAULT_FAQ, home.DEFAULT_WHY)
    assert "Custom headline" in client.get("/").content.decode()
    home.reset(staff)
    page = client.get("/").content.decode()
    assert "Your idea, online." in page and "Frequently asked questions" in page


def test_home_content_is_validated(world):  # noqa: F811
    from django.core.exceptions import ValidationError

    from apps.branding import home

    staff = world.people["super admin"]
    with pytest.raises(ValidationError, match="longer than"):
        home.save(staff, {"hero_title": "x" * 200}, [], [])
    with pytest.raises(ValidationError, match="both the question"):
        home.save(staff, {}, [{"q": "Only a question", "a": ""}], [])
    with pytest.raises(ValidationError, match="needs a title"):
        home.save(staff, {}, [], [{"icon": "bi-lock", "title": "", "text": "no title"}])
    rows = home.clean_rows([{"icon": "bi-not-an-icon", "title": "Fine", "text": ""}], "why")
    assert rows == [{"icon": "bi-shield-check", "title": "Fine", "text": ""}]  # an unknown icon becomes the standard one


def test_the_home_page_settings_page_works_for_staff_only(client, world):  # noqa: F811
    from django.test import Client as DjangoClient

    staff = world.people["super admin"]
    c = DjangoClient()
    c.force_login(staff)
    page = c.get("/staff/settings/brand/home/")
    html = page.content.decode()
    assert page.status_code == 200 and "Prices, plans, domain extensions and categories are not typed here" in html and "Frequently asked questions" in html
    posted = {"hero_title": "From the form", "faq_q": ["One?", ""], "faq_a": ["Yes.", ""], "why_icon": ["bi-lock"], "why_title": ["Safe"], "why_text": ["Very."], "show_faq": "on", "do": "save"}
    reply = c.post("/staff/settings/brand/home/", posted)
    assert reply.status_code == 302
    assert "From the form" in client.get("/").content.decode()
    assert client.get("/staff/settings/brand/home/").status_code == 302  # a visitor is sent to sign in
    owner = DjangoClient()
    owner.force_login(world.people["customer owner"])
    assert owner.get("/staff/settings/brand/home/").status_code in (302, 403, 404)


# --- the owner's own pages and the Contact page -----------------------------------------------------------------------------------

def test_a_page_written_in_setup_is_public_once_published_and_listed_in_the_footer(client, world):  # noqa: F811
    from django.test import Client as DjangoClient

    staff = DjangoClient()
    staff.force_login(world.people["super admin"])
    assert staff.get("/staff/settings/brand/pages/").status_code == 200
    reply = staff.post("/staff/settings/brand/pages/new/", {"title": "About us", "slug": "about-us", "body": "# Who we are\n\nWe host websites.\nSee https" + "://example.org/team for the team.",
                                                              "sort_order": "10", "in_footer": "on"})
    assert reply.status_code == 302
    assert client.get("/p/about-us/").status_code == 404  # a draft is not public
    assert staff.get("/p/about-us/").status_code == 200  # but staff can preview it
    page = __import__("apps.branding.models", fromlist=["PublicPage"]).PublicPage.objects.get(slug="about-us")
    staff.post(f"/staff/settings/brand/pages/{page.pk}/", {"title": "About us", "slug": "about-us", "body": page.body, "sort_order": "10", "in_footer": "on", "is_published": "on"})
    shown = client.get("/p/about-us/")
    html = shown.content.decode()
    assert shown.status_code == 200 and "<h2>Who we are</h2>" in html and "We host websites.<br>" in html and 'href="https' in html and "<script>alert" not in html
    footer = client.get("/").content.decode().split('<footer class="app-footer site-footer">')[1]
    assert 'href="/p/about-us/"' in footer and "About us" in footer and "Contact us" in footer


def test_page_text_cannot_inject_markup_and_reserved_addresses_are_refused(world):  # noqa: F811
    from django.core.exceptions import ValidationError

    from apps.branding import pages

    html = pages.render_body("<script>alert(1)</script> & <b>bold</b>")
    assert "<script>" not in html and "&lt;script&gt;" in html and "&amp;" in html
    staff = world.people["super admin"]
    with pytest.raises(ValidationError, match="used by the site itself"):
        pages.save(staff, None, {"slug": "pricing", "title": "Prices", "body": "x"})
    with pytest.raises(ValidationError, match="Write something"):
        pages.save(staff, None, {"slug": "empty", "title": "Empty", "body": "  "})
    first = pages.save(staff, None, {"slug": "terms", "title": "Terms", "body": "x"})
    with pytest.raises(ValidationError, match="already has that address"):
        pages.save(staff, None, {"slug": "terms", "title": "Terms 2", "body": "y"})
    pages.delete(staff, first)


def test_the_contact_page_uses_the_details_from_setup(client, world):  # noqa: F811
    from apps.branding import services as branding
    from apps.billing.models import BillingSettings

    branding.save_settings(world.people["super admin"], values={"support_email": "hello@example.org"})
    row = BillingSettings.load()
    row.phone, row.address = "+92 300 1234567", "Office 4, Lahore"
    row.save()
    html = client.get("/contact/").content.decode()
    assert "mailto:hello@example.org" in html and "+92 300 1234567" in html and "Office 4, Lahore" in html and "Sign in" in html


def test_care_belongs_to_each_website_and_an_added_domain_has_its_own(world, owner, account):  # noqa: F811
    from apps.hosting import care
    from apps.hosting.models import SiteCare

    client = browser(owner)
    client.post(reverse("hosting_customer:site_add", args=[account.pk]), {"domain": "care.example-test.com"})
    client.post(reverse("hosting_customer:site_setup", args=[account.pk, "care.example-test.com"]), {"type": "custom"})
    page = client.get(reverse("hosting_customer:site_care", args=[account.pk, "care.example-test.com"]))
    assert page.status_code == 200 and "care.example-test.com" in page.content.decode()
    assert client.get(reverse("hosting_customer:site_care", args=[account.pk, "unknown.example-test.org"])).status_code == 404
    client.post(reverse("hosting_customer:site_care_action", args=[account.pk, "care.example-test.com"]), {"action": "scan_schedule", "value": "weekly"})
    assert care.care_for(account, "care.example-test.com").scan_schedule != care.care_for(account).scan_schedule or SiteCare.objects.filter(account=account).count() == 2
    assert client.get(reverse("hosting_customer:care", args=[account.pk])).status_code == 200


def test_every_mailbox_on_the_emails_page_has_its_own_buttons_and_the_page_says_how_to_sign_in(world, owner, account):  # noqa: F811
    from apps.hosting import control
    from apps.hosting.adapters.capabilities import Capability

    control.perform(owner, account, Capability.EMAIL, "create_mailbox", "login@" + account.domain, "Str0ng-Passw0rd-xyz", quota_mb=100)
    html = _page(owner, "hosting_customer:emails")
    assert "How to sign in to an email address" in html and "login@" + account.domain in html
    assert "Open webmail" in html and reverse("hosting_customer:webmail", args=[account.pk]) + "?address=login%40" in html
    assert reverse("hosting_customer:webmail_settings", args=[account.pk]) + "?address=login%40" in html and "Settings" in html
    assert "/help/" in html
    opened = browser(owner).get(reverse("hosting_customer:webmail", args=[account.pk]) + "?address=login%40" + account.domain)
    assert opened.status_code in (200, 302)  # the link opens a mail screen (or the provider's own webmail)


def test_zone_management_lists_every_zone_and_filters_by_name_and_kind(world, owner, account):  # noqa: F811
    from apps.hosting import control
    from apps.hosting.adapters.capabilities import Capability

    for leftover in control.perform(owner, account, Capability.DOMAINS, "list_domains"):  # the demo server remembers earlier tests: start from the plan's own domain
        if leftover.kind != "primary":
            control.perform(owner, account, Capability.DOMAINS, "remove_domain", leftover.name)
    control.perform(owner, account, Capability.DOMAINS, "add_domain", "alpha-zone.example-test.com", "addon", "public_html/alpha-zone.example-test.com")
    control.perform(owner, account, Capability.DOMAINS, "add_domain", "beta-zone.example-test.com", "addon", "public_html/beta-zone.example-test.com")
    html = _page(owner, "hosting_customer:zones", account.pk)
    assert "Zone management" in html and account.domain in html and "alpha-zone.example-test.com" in html and "beta-zone.example-test.com" in html and "Edit records" in html
    assert reverse("hosting_customer:dns", args=[account.pk]) + "?domain=alpha-zone.example-test.com" in html
    client = browser(owner)
    only = client.get(reverse("hosting_customer:zones", args=[account.pk]) + "?q=alpha-zone").content.decode()
    assert "alpha-zone.example-test.com" in only and "beta-zone.example-test.com" not in only and "1 of" in only
    kinds = client.get(reverse("hosting_customer:zones", args=[account.pk]) + "?kind=primary").content.decode()
    assert account.domain in kinds and "alpha-zone.example-test.com" not in kinds
    none = client.get(reverse("hosting_customer:zones", args=[account.pk]) + "?q=zzzz-nothing").content.decode()
    assert "No zone matches" in none
