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
    assert labels == ["Home", "Websites", "More services", "Domains", "Orders", "Billing", "Support", "Refer & earn"]
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
