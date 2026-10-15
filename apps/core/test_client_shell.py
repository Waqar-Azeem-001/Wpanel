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
