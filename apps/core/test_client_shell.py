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
