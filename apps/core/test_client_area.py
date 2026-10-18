"""Phase D3: the customer area (roadmap Section 10) - dashboard, sidebars with counts, tabbed service and domain pages,
the new pages, and that all of it agrees with what each person may open."""
import re

import pytest
from django.test import Client as HttpClient
from django.urls import reverse

from apps.clients import services as client_services
from apps.core import navigation

pytestmark = pytest.mark.django_db


def browser(user=None):
    client = HttpClient(HTTP_HOST="localhost", raise_request_exception=False)
    if user is not None:
        client.force_login(user)
    return client


def rows(response):
    """How many rows a list page shows, whichever name the view gave them."""
    context = response.context
    for key in ("page", "accounts", "domains", "orders", "clients"):
        if key in context:
            value = context[key]
            return value.paginator.count if hasattr(value, "paginator") else len(list(value))
    raise AssertionError("not a list page")


@pytest.fixture
def owner(world):
    return world.people["customer owner"]


@pytest.fixture
def stranger(world):
    """A customer of a different client: must never see the world's data."""
    return client_services.create_client(world.people["manager"], {
        "first_name": "Stranger", "email": "stranger@elsewhere.test", "country": "US"}).contacts.get().user


# --- Home -------------------------------------------------------------------------------------------------------------

def test_customers_land_on_their_dashboard_staff_on_theirs_and_visitors_see_the_store(world, stranger):
    for role in ("customer owner", "billing contact", "technical contact"):
        assert browser(world.people[role]).get("/").headers["Location"] == reverse("dashboard"), role
    assert browser(world.people["manager"]).get("/").headers["Location"] == reverse("console:dashboard")
    assert browser().get("/").status_code == 200  # a visitor sees the storefront, not a sign-in page


def test_a_customer_without_an_account_is_not_sent_to_a_page_that_needs_one(world, make_user):
    loner = make_user("loner@harness.test")
    assert browser(loner).get("/").headers["Location"] == reverse("accounts:profile")
    page = browser(loner).get(reverse("dashboard"))
    assert page.status_code == 200 and b"do not have a client account" in page.content


def test_the_dashboard_needs_sign_in_and_staff_are_sent_to_their_own_page(world):
    assert browser().get(reverse("dashboard")).headers["Location"].startswith("/account/login/?next=")
    assert browser(world.people["manager"]).get(reverse("dashboard")).headers["Location"] == reverse("accounts:profile")


# --- Dashboard --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["customer owner", "billing contact", "technical contact"])
def test_every_contact_sees_the_accounts_dashboard(world, role):
    response = browser(world.people[role]).get(reverse("dashboard"))
    assert response.status_code == 200
    page = response.content.decode()
    assert "Welcome back" in page and "Your websites" in page and "Your domains" in page and "Recent support tickets" in page
    assert "Find a new domain" in page and "Affiliate program" in page
    assert page.index("Your websites") < page.index("Your domains") < page.index("Recent support tickets")  # websites first, then domains, then support


def test_each_tile_shows_exactly_the_rows_its_link_opens(world, owner):
    client = browser(owner)
    tiles = client.get(reverse("dashboard")).context["tiles"]
    assert [t["label"] for t in tiles] == ["Services", "Domains", "Quotes", "Tickets", "Invoices"]
    for tile in tiles:
        target = client.get(tile["url"])
        assert target.status_code == 200, tile
        assert rows(target) == tile["value"], (tile["label"], rows(target), tile["value"])
    assert sum(t["value"] for t in tiles) > 5  # the world is not empty


def test_the_overdue_banner_counts_and_totals_overdue_invoices_and_links_to_them(world, owner):
    context = browser(owner).get(reverse("dashboard")).context
    assert context["overdue_count"] == 1
    assert context["overdue_totals"] == [("USD", world.objects["invoices"]["overdue"].balance_due)]
    page = browser(owner).get(reverse("dashboard")).content.decode()
    assert "You have 1 overdue invoice" in page
    assert reverse("billing_customer:invoice_detail", args=[world.objects["invoices"]["overdue"].pk]) in page  # one: straight to it


def test_several_overdue_invoices_link_to_the_filtered_list(world, owner):
    from apps.billing.models import Invoice

    Invoice.objects.filter(pk=world.objects["invoices"]["unpaid"].pk).update(due_date="2020-01-01")
    page = browser(owner).get(reverse("dashboard")).content.decode()
    assert "You have 2 overdue invoices" in page and reverse("billing_customer:invoice_list") + "?status=overdue" in page


def test_no_banner_when_nothing_is_overdue(world, owner):
    from apps.billing.models import Invoice

    Invoice.objects.filter(pk=world.objects["invoices"]["overdue"].pk).update(due_date="2999-01-01")
    assert b"overdue invoice" not in browser(owner).get(reverse("dashboard")).content


def test_the_dashboard_lists_only_live_services_and_the_latest_tickets(world, owner):
    context = browser(owner).get(reverse("dashboard")).context
    services = list(context["services"])
    assert {a.status for a in services} <= {"active", "suspended"} and len(services) == 5  # live ones only, at most five
    due = [a.expires_at for a in services if a.expires_at]
    assert due == sorted(due)  # the ones falling due soonest come first
    assert len(context["recent_tickets"]) == 5
    assert list(context["recent_tickets"]) == sorted(context["recent_tickets"], key=lambda t: (-t.last_activity_at.timestamp(), -t.pk))


def test_the_sidebar_describes_the_account_its_contacts_and_shortcuts(world, owner):
    sidebar = browser(owner).get(reverse("dashboard")).context["side_panels"]
    assert [p.title for p in sidebar] == ["Your info", "Contacts", "Shortcuts"]
    assert "Ada Ltd" in sidebar[0].lines[1] or "Ada Ltd" in sidebar[0].lines
    assert any("Owner" in line for line in sidebar[1].lines) and any("Billing" in line for line in sidebar[1].lines)
    assert [link.label for link in sidebar[2].links] == ["Order new services", "Register a domain", "Open a ticket"]
    assert not any(link.post for link in sidebar[2].links)  # signing out lives in the profile menu, once


def test_the_affiliate_panel_follows_the_customers_affiliate_state(world, owner, stranger):
    assert b"Ready to pay" in browser(owner).get(reverse("dashboard")).content  # an active affiliate
    other = browser(stranger).get(reverse("dashboard")).content
    assert b"Join the program" in other and b"Ready to pay" not in other


def test_nobody_sees_another_customers_data_on_their_dashboard(world, stranger):
    response = browser(stranger).get(reverse("dashboard"))
    assert [t["value"] for t in response.context["tiles"]] == [0, 0, 0, 0, 0]
    assert response.context["overdue_count"] == 0 and list(response.context["services"]) == []
    assert b"harness.test" not in response.content and b"Ada" not in response.content


def test_the_dashboard_has_one_h1_and_no_dead_links(world, owner):
    page = browser(owner).get(reverse("dashboard")).content.decode()
    assert page.count("<h1") == 1
    assert 'href="#"' not in page and 'href=""' not in page


# --- Sidebars with counts ---------------------------------------------------------------------------------------------

LISTS = ["hosting_customer:list", "domains_customer:list", "billing_customer:invoice_list",
         "billing_customer:quote_list", "support_customer:list"]


@pytest.mark.parametrize("name", LISTS)
def test_every_count_in_a_view_panel_is_the_number_of_rows_behind_that_link(world, owner, name):
    client = browser(owner)
    panel = client.get(reverse(name)).context["sidebar"][0]
    assert panel.title == "View" and len(panel.links) >= 4
    for link in panel.links:
        target = client.get(link.url)
        assert target.status_code == 200, link.url
        assert rows(target) == link.count, (link.label, rows(target), link.count)


@pytest.mark.parametrize("name", LISTS)
def test_exactly_one_view_link_is_marked_as_current(world, owner, name):
    panel = browser(owner).get(reverse(name)).context["sidebar"][0]
    assert sum(1 for link in panel.links if link.active) == 1


@pytest.mark.parametrize("name,junk", [(n, j) for n in LISTS for j in ("nonsense", "<script>", "", "%00", "a" * 300)])
def test_a_junk_status_value_is_ignored_never_an_error(world, owner, name, junk):
    response = browser(owner).get(reverse(name), {"status": junk})
    assert response.status_code == 200 and b"<script>alert" not in response.content


def test_the_current_filter_marks_its_link_and_narrows_the_list(world, owner):
    client = browser(owner)
    response = client.get(reverse("hosting_customer:list"), {"status": "suspended"})
    assert {a.status for a in response.context["accounts"]} == {"suspended"}
    assert [l.label for l in response.context["sidebar"][0].links if l.active] == ["Suspended"]


def test_invoice_views_are_unpaid_overdue_paid_and_the_rest(world, owner):
    panel = browser(owner).get(reverse("billing_customer:invoice_list")).context["sidebar"][0]
    counts = {l.label: l.count for l in panel.links}
    invoices = world.objects["invoices"]
    assert list(counts) == ["All invoices", "Unpaid", "Overdue", "Paid", "Cancelled", "Refunded"]
    assert counts["Overdue"] == 1 and counts["Paid"] == 1 and counts["Cancelled"] == 1 and counts["Refunded"] == 1
    assert counts["Unpaid"] >= 3 and counts["All invoices"] == sum(counts[k] for k in ("Unpaid", "Paid", "Cancelled", "Refunded"))
    assert "draft" not in " ".join(counts).lower() and invoices["draft"].pk  # a draft is never the customer's business


def test_a_draft_invoice_is_never_listed_for_a_customer(world, owner):
    numbers = [i.number for i in browser(owner).get(reverse("billing_customer:invoice_list")).context["page"]]
    assert world.objects["invoices"]["draft"].number not in numbers


def test_the_ticket_list_keeps_the_older_open_and_closed_addresses(world, owner):
    client = browser(owner)
    assert {t.status for t in client.get(reverse("support_customer:list"), {"view": "closed"}).context["page"]} == {"closed"}
    open_page = client.get(reverse("support_customer:list"), {"view": "open"}).context["page"]
    assert "closed" not in {t.status for t in open_page} and len(open_page) == 5
    assert rows(client.get(reverse("support_customer:list"))) == 5  # the default is Open
    assert rows(client.get(reverse("support_customer:list"), {"status": "all"})) == 6


def test_view_panels_and_pages_keep_the_filter_when_paging(world, owner):
    page = browser(owner).get(reverse("hosting_customer:list"), {"status": "active"}).content.decode()
    assert 'aria-current="page"' in page and "?status=suspended" in page


def test_the_page_filters_are_one_strip_above_the_page_not_a_third_column(world, owner):
    page = browser(owner).get(reverse("hosting_customer:list")).content.decode()
    assert '<nav class="cs-filters"' in page and "cs-chip" in page
    assert '<aside class="col-lg-3' not in page and 'class="col-lg-9' not in page
    assert page.index('<nav class="cs-filters"') < page.index("<h1")  # the strip comes before the page itself


def test_a_page_without_a_sidebar_is_still_a_single_column(world, owner):
    page = browser(owner).get(reverse("accounts:profile")).content.decode()
    assert '<aside class="col-lg-3' not in page and 'class="col-lg-9' not in page


# --- Service and domain tabs ------------------------------------------------------------------------------------------

def tab_links(response):
    """(label, url) for each tab of the page's tab bar (not the top navigation). Domains still use a row of tabs."""
    bar = response.content.decode().split('<ul class="nav nav-tabs')[1].split("</ul>")[0]
    return [(label, url) for url, label in re.findall(r'<a class="nav-link(?: active)?" href="([^"]+)"[^>]*>([^<]+)</a>', bar)]


def tab_labels(response):
    return [label for label, _url in tab_links(response)]


def menu_links(response):
    """(label, url) of every link in the website workspace menu (the left menu that replaced the row of tabs)."""
    sidebar = response.context["sidebar"]
    return [(l.label, l.url) for panel in sidebar for l in panel.links if l.url]


def plan_labels(response):
    return [l.label for l in response.context["sidebar"][1].links]


def test_a_service_has_a_workspace_menu_with_overview_tools_and_plan_pages(world, owner):
    live = world.objects["accounts"]["active"]
    client = browser(owner)
    response = client.get(reverse("hosting_customer:detail", args=[live.pk]))
    sidebar = response.context["sidebar"]
    assert [p.title for p in sidebar] == ["Website", "Plan"] and sidebar.workspace
    assert sidebar[0].links[0].label == "Overview" and plan_labels(response) == ["Information", "Add-ons", "Upgrade / downgrade", "Request cancellation", "Open a ticket", "Delete website"]
    for label, url in menu_links(response):
        assert client.get(url).status_code == 200, (label, url)  # every entry opens (Rule 5.6)


def test_the_menu_marks_the_current_page(world, owner):
    live = world.objects["accounts"]["active"]
    client = browser(owner)
    for name, label in (("hosting_customer:detail", "Overview"), ("hosting_customer:information", "Information"), ("hosting_customer:addons", "Add-ons")):
        response = client.get(reverse(name, args=[live.pk]))
        active = [l.label for panel in response.context["sidebar"] for l in panel.links if l.active]
        assert active == [label], name
        assert f'aria-current="page"' in response.content.decode()


def test_a_phone_gets_the_same_menu_from_a_button_in_the_header(world, owner):
    live = world.objects["accounts"]["active"]
    page = browser(owner).get(reverse("hosting_customer:detail", args=[live.pk])).content.decode()
    assert 'data-menu-open' in page and 'id="topnav"' in page and 'class="cdrawer"' in page and "cmenu-group" in page  # the shell's drawer carries the website menu
    assert "Manage your website" in page or "Billing term" in page  # the overview content is still there


def test_tools_the_server_cannot_do_are_not_listed_at_all(world, owner):
    live = world.objects["accounts"]["active"]
    response = browser(owner).get(reverse("hosting_customer:detail", args=[live.pk]))
    assert "sidebar-soon" not in response.content.decode()
    assert all(l.url for l in response.context["sidebar"][0].links)  # no muted, address-less rows


def test_the_information_tab_shows_the_server_but_never_a_password(world, owner):
    page = browser(owner).get(reverse("hosting_customer:information", args=[world.objects["accounts"]["active"].pk])).content.decode()
    main = page.split("<main")[1].split("</main>")[0].lower()
    assert "hosting information" in main and "srv1.harness.test" in main
    assert "we never keep your password" in main
    from apps.hosting.models import HostingAccount

    assert not [f.name for f in HostingAccount._meta.fields if "password" in f.name]  # there is nothing to show


def test_the_addons_tab_lists_addons_ordered_with_that_service(world, owner):
    from apps.orders.models import ItemKind, OrderItem

    account = world.objects["accounts"]["active"]
    order = next(iter(world.objects["orders"].values()))
    parent = OrderItem.objects.create(order=order, kind=ItemKind.HOSTING, description="Hosting", unit_price=10, line_total=10,
                                      hosting_account=account)
    OrderItem.objects.create(order=order, kind=ItemKind.ADDON, description="Daily backups", parent=parent, unit_price=5,
                             line_total=5, addon=world.objects["addon"])
    page = browser(owner).get(reverse("hosting_customer:addons", args=[account.pk])).content.decode()
    assert "Daily backups" in page and order.reference in page
    other = world.objects["accounts"]["suspended"]
    assert "No addons were ordered" in browser(owner).get(reverse("hosting_customer:addons", args=[other.pk])).content.decode()


def test_the_cancel_and_upgrade_links_appear_only_where_they_can_be_used(world):
    active = world.objects["accounts"]["active"]
    suspended = world.objects["accounts"]["suspended"]
    for role, expect_cancel in (("customer owner", True), ("technical contact", False), ("billing contact", False)):
        page = browser(world.people[role]).get(reverse("hosting_customer:detail", args=[active.pk]))
        assert (("Request cancellation" in plan_labels(page)) == expect_cancel), role
    assert "Upgrade / downgrade" not in plan_labels(browser(world.people["customer owner"]).get(
        reverse("hosting_customer:detail", args=[suspended.pk])))


def test_a_service_belonging_to_someone_else_is_not_found_on_any_tab(world, stranger):
    live = world.objects["accounts"]["active"]
    for name in ("detail", "information", "addons"):
        assert browser(stranger).get(reverse(f"hosting_customer:{name}", args=[live.pk])).status_code == 404, name


DOMAIN_TABS = ["Overview", "Auto renew", "Nameservers", "DNS", "Registrar lock", "Renew", "Request cancellation"]


def test_a_domain_has_a_workspace_menu_like_a_website_and_every_entry_opens(world, owner):
    domain = world.objects["domains"]["active"]
    client = browser(owner)
    response = client.get(reverse("domains_customer:detail", args=[domain.pk]))
    sidebar = response.context["sidebar"]
    assert sidebar.workspace and [p.title for p in sidebar] == ["Domain", "Registration"]
    assert [l.label for l in sidebar[0].links] == ["Overview", "DNS records", "Nameservers", "Auto renew", "Registrar lock", "Transfer"]
    assert [l.label for l in sidebar[1].links] == ["Renew", "Request cancellation", "Open a ticket"]
    assert 'class="cdrawer"' in response.content.decode()  # a phone opens the same menu from the header button
    for panel in sidebar:
        for link in panel.links:
            assert client.get(link.url).status_code == 200, link.label


def test_domain_actions_return_to_the_tab_they_came_from(world, owner):
    domain = world.objects["domains"]["active"]
    client = browser(owner)
    pk = domain.pk
    assert client.post(reverse("domains_customer:auto_renew", args=[pk]), {"auto_renew": "on"}).headers["Location"] == \
        reverse("domains_customer:auto_renew", args=[pk])
    assert client.post(reverse("domains_customer:nameservers", args=[pk]), {"nameservers": "ns1.a.test\nns2.a.test"}
                       ).headers["Location"] == reverse("domains_customer:nameservers", args=[pk])
    assert client.post(reverse("domains_customer:unlock", args=[pk])).headers["Location"] == reverse("domains_customer:lock_tab", args=[pk])
    assert client.post(reverse("domains_customer:lock", args=[pk])).headers["Location"] == reverse("domains_customer:lock_tab", args=[pk])
    dns = reverse("domains_customer:dns_add", args=[pk])
    assert client.post(dns, {"record_type": "A", "name": "www", "content": "203.0.113.5", "ttl": 3600}).headers["Location"] == dns


def test_domain_changes_really_happen_and_show_on_their_tab(world, owner):
    domain = world.objects["domains"]["active"]
    client = browser(owner)
    client.post(reverse("domains_customer:nameservers", args=[domain.pk]), {"nameservers": "ns1.new.test\nns2.new.test"})
    assert "ns1.new.test" in client.get(reverse("domains_customer:nameservers", args=[domain.pk])).content.decode()
    client.post(reverse("domains_customer:dns_add", args=[domain.pk]),
                {"record_type": "A", "name": "shop", "content": "203.0.113.9", "ttl": 3600})
    assert "203.0.113.9" in client.get(reverse("domains_customer:dns_add", args=[domain.pk])).content.decode()


def test_the_dns_and_nameserver_tabs_are_read_only_for_a_finished_domain(world, owner):
    cancelled = world.objects["domains"]["cancelled"]
    page = browser(owner).get(reverse("domains_customer:nameservers", args=[cancelled.pk])).content.decode()
    assert "can no longer be changed" in page and "<textarea" not in page
    dns = browser(owner).get(reverse("domains_customer:dns_add", args=[cancelled.pk])).content.decode()
    assert "Add a record" not in dns and "Remove" not in dns


def test_the_renew_tab_offers_renewal_only_for_an_active_domain(world, owner):
    active, expired = world.objects["domains"]["active"], world.objects["domains"]["expired"]
    assert b"Renew (creates an invoice)" in browser(owner).get(reverse("domains_customer:renew_tab", args=[active.pk])).content
    assert b"Only an active domain can be renewed" in browser(owner).get(reverse("domains_customer:renew_tab", args=[expired.pk])).content


def test_the_domain_tab_pages_of_someone_elses_domain_are_not_found(world, stranger):
    domain = world.objects["domains"]["active"]
    for name in ("detail", "auto_renew", "nameservers", "dns_add", "lock_tab", "renew_tab"):
        assert browser(stranger).get(reverse(f"domains_customer:{name}", args=[domain.pk])).status_code == 404, name


def test_only_the_post_actions_change_a_domain_a_get_never_does(world, owner):
    domain = world.objects["domains"]["active"]
    client = browser(owner)
    before = type(domain).objects.get(pk=domain.pk)
    for name in ("auto_renew", "nameservers", "dns_add", "lock_tab", "renew_tab"):
        client.get(reverse(f"domains_customer:{name}", args=[domain.pk]))
    after = type(domain).objects.get(pk=domain.pk)
    assert (before.auto_renew, before.is_locked, before.nameservers) == (after.auto_renew, after.is_locked, after.nameservers)
    assert client.get(reverse("domains_customer:lock", args=[domain.pk])).status_code == 405  # POST only


# --- The new pages ----------------------------------------------------------------------------------------------------

def test_available_addons_lists_active_addons_with_active_prices_and_needs_no_sign_in(world):
    from apps.products.models import Addon

    Addon.objects.create(name="Hidden thing", slug="hidden-thing", status="hidden")
    page = browser().get(reverse("catalog:addons")).content.decode()
    assert "Backups" in page and "Hidden thing" not in page and "20.00" in page


def test_payment_methods_shows_active_methods_with_their_instructions(world, owner):
    from apps.billing.models import PaymentMethod

    method = world.objects["method"]
    PaymentMethod.objects.filter(pk=method.pk).update(instructions="Send to account 1234")
    PaymentMethod.objects.create(code="old", name="Retired method", is_active=False)
    page = browser(owner).get(reverse("billing_customer:payment_methods")).content.decode()
    assert "Bank transfer" in page and "Send to account 1234" in page and "Retired method" not in page
    assert browser().get(reverse("billing_customer:payment_methods")).status_code == 302  # sign in first


def test_email_history_lists_only_my_emails_and_never_a_message_with_a_secret(world, owner, stranger):
    from apps.notifications.models import EmailMessage

    EmailMessage.objects.create(user=owner, event="x", template="x", to_email=owner.email, subject="Secret link",
                                body_text="https://secret", is_sensitive=True)
    mine = EmailMessage.objects.create(user=owner, event="x", template="x", to_email=owner.email, subject="A public one",
                                       body_text="Hello Ada")
    theirs = EmailMessage.objects.create(user=stranger, event="x", template="x", to_email=stranger.email, subject="Not yours",
                                         body_text="Hello Stranger")
    page = browser(owner).get(reverse("notifications:emails")).content.decode()
    assert "A public one" in page and "Secret link" not in page and "Not yours" not in page
    assert b"Hello Ada" in browser(owner).get(reverse("notifications:email", args=[mine.pk])).content
    assert browser(owner).get(reverse("notifications:email", args=[theirs.pk])).status_code == 404
    secret = EmailMessage.objects.get(subject="Secret link")
    assert browser(owner).get(reverse("notifications:email", args=[secret.pk])).status_code == 404


def test_contacts_lists_each_of_my_accounts_with_roles_and_nothing_of_anyone_elses(world, owner, stranger):
    page = browser(owner).get(reverse("clients_customer:contacts")).content.decode()
    for email in ("finance@harness.test", "tech@harness.test", "ada@harness.test"):
        assert email in page
    assert "stranger@elsewhere.test" not in page and "manager@harness.test" not in page
    other = browser(stranger).get(reverse("clients_customer:contacts")).content.decode()
    assert "ada@harness.test" not in other and "stranger@elsewhere.test" in other


def test_the_registry_offers_the_new_pages_to_customers_only(world, owner):
    from django.test import RequestFactory

    def labels(user, area):
        request = RequestFactory().get("/account/")
        request.user = user
        request.resolver_match = None
        menus = navigation.build(request, area)
        return {c.label for top in menus["main"] + menus["account"] for c in (top.children or [top])}

    mine = labels(owner, "client")
    assert {"Add-ons", "Payment methods", "Contacts", "Email history", "Invoices"} <= mine
    staff = labels(world.people["manager"], "staff")
    assert not ({"Contacts", "Email history"} & staff)


# --- The re-skin ------------------------------------------------------------------------------------------------------

CUSTOMER_TEMPLATES = [
    "accounts", "affiliates/customer", "billing/customer", "clients/customer", "domains/customer", "hosting/customer",
    "lifecycle/customer", "notifications", "orders/customer", "renewals/customer", "support/customer", "support/kb",
    "products/public", "domains/public"]


def customer_template_files():
    from pathlib import Path

    from django.conf import settings

    root = Path(settings.BASE_DIR) / "templates"
    return [f for folder in CUSTOMER_TEMPLATES for f in sorted((root / folder).glob("*.html"))
            if f.parent.name != "staff"]


def test_no_customer_page_uses_the_old_class_names_or_inline_scripts():
    """The customer area is on Bootstrap and the design tokens; the bridge stylesheet is for the staff area now."""
    old = re.compile(r'class="[^"]*(?<![\w-])(btn secondary|btn danger|btn small|table-wrap|cols|grid-form|row-form|meta|muted|num'
                     r'|actions|stats|stat|tabs|subnav|filters)(?![\w-])[^"]*"')
    offenders = []
    for file in customer_template_files():
        text = file.read_text(encoding="utf-8")
        for match in old.finditer(text):
            offenders.append(f"{file.name}: {match.group(0)[:60]}")
        for banned in ('onsubmit="return confirm', ' style="'):
            if banned in text:
                offenders.append(f"{file.name}: {banned}")
    assert offenders == []


def test_customer_pages_use_the_confirmation_modal_not_the_browsers_confirm_box(world, owner):
    domain = world.objects["domains"]["active"]
    page = browser(owner).get(reverse("domains_customer:lock_tab", args=[domain.pk])).content.decode()
    assert "data-confirm=" in page and "return confirm(" not in page


def test_customer_pages_are_translation_ready():
    """Every customer page loads the translation library: strings are wrapped as each page is re-skinned."""
    missing = [f.name for f in customer_template_files() if "trans" not in f.read_text(encoding="utf-8")
               and f.name not in ("_header.html",)]
    assert missing == []


def test_the_bridge_stylesheet_is_gone_and_nothing_loads_it():
    """D4e: every screen is on the components; the compatibility sheet from D1 was deleted."""
    from pathlib import Path

    from django.conf import settings

    root = Path(settings.BASE_DIR)
    assert not (root / "static" / "css" / "legacy.css").exists()
    offenders = [str(p.relative_to(root)) for p in (root / "templates").rglob("*.html") if "legacy.css" in p.read_text(encoding="utf-8")]
    assert offenders == []


# --- the "Add website" steps (plan -> domain -> confirm) -------------------------------------------------------------------------------------

def test_add_website_walks_plan_domain_confirm_and_only_then_posts_to_the_cart(world, owner):
    from apps.orders.models import CartItem
    from apps.products.models import Product, ProductType

    product = Product.objects.filter(type=ProductType.SHARED_HOSTING, status="active").first()
    client = browser(owner)
    first = client.get(reverse("hosting_customer:new"))
    assert first.status_code == 200 and first.context["step"] == "plan" and product.name in first.content.decode()
    second = client.get(reverse("hosting_customer:new"), {"product": product.pk})
    assert second.context["step"] == "domain" and second.context["options"] and second.context["done"] == ["plan"]
    bad = client.get(reverse("hosting_customer:new"), {"product": product.pk, "domain": "not a domain"})
    assert bad.context["step"] == "domain" and bad.context["error"]
    third = client.get(reverse("hosting_customer:new"), {"product": product.pk, "domain": "Example.COM", "cycle": second.context["cycle"]})
    assert third.context["step"] == "confirm" and third.context["domain"] == "example.com" and third.context["done"] == ["plan", "domain"]
    page = third.content.decode()
    assert reverse("orders_customer:cart_add_hosting") in page and 'name="domain" value="example.com"' in page
    assert not CartItem.objects.filter(cart__user=owner, domain_name="example.com").exists()  # walking through the steps creates nothing
    client.post(reverse("orders_customer:cart_add_hosting"), {"product": product.pk, "cycle": third.context["chosen"]["value"], "domain": "example.com"})
    assert CartItem.objects.filter(cart__user=owner, domain_name="example.com").exists()


def test_add_website_ignores_a_made_up_plan_or_cycle_and_needs_sign_in(world, owner):
    from django.test import Client

    from apps.products.models import Product, ProductType

    product = Product.objects.filter(type=ProductType.SHARED_HOSTING, status="active").first()
    assert Client().get(reverse("hosting_customer:new")).status_code == 302  # signed out: sent to sign in
    client = browser(owner)
    assert client.get(reverse("hosting_customer:new"), {"product": "99999"}).context["step"] == "plan"
    odd = client.get(reverse("hosting_customer:new"), {"product": product.pk, "cycle": "custom:9999", "domain": "a.com"})
    assert odd.context["cycle"] != "custom:9999" and odd.context["step"] == "confirm"  # falls back to a real period, never a made-up one


def test_the_websites_page_offers_the_guided_way_to_add_one(world, owner):
    page = browser(owner).get(reverse("hosting_customer:list")).content.decode()
    assert reverse("hosting_customer:new") in page
