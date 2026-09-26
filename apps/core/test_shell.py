"""Phase D6: the application shell, the sign-in screens, the Users area and the technical role."""
import re

import pytest
from django.test import Client as HttpClient
from django.urls import reverse

from apps.accounts import services as account_services
from apps.accounts.models import User
from apps.accounts.roles import ROLE_PERMISSIONS, STAFF_ROLES, Role, perm
from apps.audit.models import AuditEvent
from apps.core.harness import PASSWORD

pytestmark = pytest.mark.django_db

HOST = {"HTTP_HOST": "localhost"}


def browser(person):
    client = HttpClient(**HOST)
    if person is not None:
        client.force_login(person)
    return client


def page_of(person, name, *args, follow=False):
    return browser(person).get(reverse(name, args=args), follow=follow)


def rail_of(html):
    return html.split('<div id="rail"')[1].split("</nav>")[0]


# --- Sign in and create account -------------------------------------------------------------------------------------

def test_the_sign_in_screen_is_a_split_screen_without_the_site_navigation():
    html = HttpClient(**HOST).get(reverse("accounts:login")).content.decode()
    assert 'class="auth"' in html and 'class="auth-art"' in html and 'class="auth-panel"' in html
    assert "navbar" not in html and 'id="rail"' not in html
    assert reverse("accounts:password_reset") in html and reverse("accounts:register") in html
    assert "data-toggle-password" in html  # show / hide the password
    assert 'type="password"' in html and 'autocomplete="current-password"' in html
    assert "terms of service" in html  # the acknowledgement sits under the button


def test_signing_in_still_works_and_keeps_the_page_you_came_for(make_user):
    make_user("alice@example.com")
    client = HttpClient(**HOST)
    response = client.post(reverse("accounts:login"), {"email": "alice@example.com", "password": PASSWORD,
                                                       "next": reverse("notifications:inbox")})
    assert response.status_code == 302 and response.url == reverse("notifications:inbox")
    assert "_auth_user_id" in client.session


def test_a_wrong_password_is_explained_on_the_form_and_no_one_is_signed_in(make_user):
    make_user("alice@example.com")
    client = HttpClient(**HOST)
    response = client.post(reverse("accounts:login"), {"email": "alice@example.com", "password": "not-the-password"})
    html = response.content.decode()
    assert response.status_code == 200 and 'class="alert alert-danger auth-alert"' in html
    assert "_auth_user_id" not in client.session


def test_the_registration_screen_shows_each_error_beside_its_field():
    client = HttpClient(**HOST)
    html = client.post(reverse("accounts:register"), {"email": "not-an-email", "password": "abc", "password_confirm": "xyz"}).content.decode()
    assert html.count('class="invalid-msg"') >= 2 and "Passwords do not match." in html
    assert 'class="auth-card is-wide"' in html and reverse("accounts:login") in html
    assert html.count("data-toggle-password") == 2


def test_registration_still_creates_an_account_and_signs_the_person_in():
    client = HttpClient(**HOST)
    response = client.post(reverse("accounts:register"), {"first_name": "Zed", "email": "zed@example.com", "password": PASSWORD,
                                                          "password_confirm": PASSWORD})
    assert response.status_code == 302 and User.objects.filter(email="zed@example.com").exists()
    assert "_auth_user_id" in client.session


@pytest.mark.parametrize("name", ["accounts:password_reset"])
def test_the_password_pages_share_the_sign_in_design(name):
    html = HttpClient(**HOST).get(reverse(name)).content.decode()
    assert 'class="auth"' in html and "navbar" not in html


def test_password_reset_and_the_link_it_sends_still_work(make_user, mailoutbox):
    person = make_user("alice@example.com")
    client = HttpClient(**HOST)
    sent = client.post(reverse("accounts:password_reset"), {"email": person.email})
    assert 'class="auth-state"' in sent.content.decode()
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    link = reverse("accounts:password_reset_confirm", args=[urlsafe_base64_encode(force_bytes(person.pk)),
                                                            default_token_generator.make_token(person)])
    assert 'data-toggle-password' in client.get(link).content.decode()
    bad = client.get(reverse("accounts:password_reset_confirm", args=["MQ", "bad-token"]))
    assert bad.status_code == 400 and "Link invalid" in bad.content.decode()
    changed = client.post(link, {"new_password": "An0ther-Passw0rd!x", "new_password_confirm": "An0ther-Passw0rd!x"})
    assert changed.status_code == 302 and User.objects.get(pk=person.pk).check_password("An0ther-Passw0rd!x")


def test_the_store_keeps_its_own_top_bar_and_visitors_get_no_rail():
    html = HttpClient(**HOST).get("/").content.decode()
    assert "navbar-public" in html and 'id="rail"' not in html


# --- The shell -------------------------------------------------------------------------------------------------------

def test_signed_in_people_get_the_rail_the_top_bar_and_the_search(world):
    for who, area in (("customer owner", "client"), ("support agent", "staff"), ("technical staff", "staff"),
                      ("manager", "staff"), ("admin", "staff"), ("super admin", "staff")):
        html = page_of(world.people[who], "home", follow=True).content.decode()
        assert f'class="rail rail-{area}"' in html, who
        assert 'class="topbar"' in html and 'id="quick-nav"' in html and "data-rail-collapse" in html, who
        assert "navbar-client" not in html and "navbar-staff" not in html, who
        assert ('data-search-url="' + reverse("console:search") + '"' in html) == (area == "staff"), who


def test_the_rail_marks_the_current_page_and_opens_its_group(world):
    html = rail_of(page_of(world.people["manager"], "billing_staff:invoice_list").content.decode())
    assert re.search(r'class="rail-sublink is-current" href="/staff/billing/invoices/" aria-current="page"', html)
    assert re.search(r'class="rail-group is-open is-active">\s*<div class="rail-row">\s*<a class="rail-link is-current" href="/staff/billing/', html)
    assert html.count("is-open") == 1  # only the group you are in is open


def test_the_rail_lists_only_what_the_person_can_open(world):
    technical = rail_of(page_of(world.people["technical staff"], "console:dashboard").content.decode())
    for hidden in (reverse("billing_staff:invoice_list"), reverse("console:users"), reverse("reports_staff:index"),
                   reverse("console:email_providers"), reverse("catalog_staff:product_list")):
        assert hidden not in technical
    for shown in (reverse("hosting_staff:list"), reverse("support_staff:tickets"), reverse("clients_staff:list")):
        assert shown in technical


def test_the_sections_and_their_headings_are_drawn(world):
    html = rail_of(page_of(world.people["admin"], "console:dashboard").content.decode())
    headings = re.findall(r'<p class="rail-section"><span>([^<]+)</span></p>', html)
    assert headings == ["People", "Commerce", "Operations", "Insights", "System"]


def test_the_profile_menu_shows_who_you_are_and_signs_out_with_a_post(world):
    html = page_of(world.people["technical staff"], "console:dashboard").content.decode()
    assert "Technical Staff" in html and "technical@harness.test" in html
    assert re.search(r'<form method="post" action="/account/logout/">', html)
    admin = page_of(world.people["admin"], "console:dashboard").content.decode()
    root = page_of(world.people["super admin"], "console:dashboard").content.decode()
    assert "Database admin" not in admin and "Database admin" in root


def test_the_notification_bell_shows_the_unread_count_in_the_top_bar(world):
    html = page_of(world.people["customer owner"], "dashboard").content.decode()
    assert re.search(r'class="icon-badge">\d+<span class="visually-hidden"> new</span>', html)


def test_a_detail_page_keeps_exactly_one_group_open(world):
    invoice = world.objects["invoices"]["unpaid"]
    html = rail_of(page_of(world.people["manager"], "billing_staff:invoice_detail", invoice.pk).content.decode())
    assert html.count("is-open") == 1 and re.search(r'is-open is-active">\s*<div class="rail-row">\s*<a class="rail-link is-current" href="/staff/billing/', html)


def test_the_customer_sidebar_no_longer_repeats_the_sign_out_link(world):
    html = page_of(world.people["customer owner"], "dashboard").content.decode()
    assert html.count("Sign out") == 1  # the profile menu is the one place


def test_the_top_bar_carries_the_breadcrumbs_of_registry_pages(world):
    html = page_of(world.people["manager"], "billing_staff:invoice_list").content.decode()
    top = html.split('<header class="topbar">')[1].split("</header>")[0]
    assert 'aria-label="Breadcrumb"' in top and 'aria-current="page">Invoices<' in top


def test_the_collapsed_state_is_read_before_the_page_paints():
    """theme.js is loaded in the head, ahead of the stylesheet, so a collapsed rail never flashes open."""
    html = HttpClient(**HOST).get(reverse("accounts:login")).content.decode()
    assert html.index("js/theme.js") < html.index("css/shell.css")
    assert "wp.rail" in open("static/js/theme.js", encoding="utf-8").read()


# --- The technical role ----------------------------------------------------------------------------------------------

def test_technical_staff_are_a_staff_role_with_a_narrow_set_of_permissions():
    assert Role.TECHNICAL in STAFF_ROLES
    granted = set(ROLE_PERMISSIONS[Role.TECHNICAL])
    assert {"view_hosting", "manage_hosting", "view_domains", "manage_domains", "view_support", "manage_support",
            "view_clients", "view_orders"} <= granted
    for never in ("view_billing", "manage_billing", "view_reports", "view_users", "manage_users", "assign_roles",
                  "view_providers", "view_settings", "view_audit_log", "manage_clients", "manage_orders", "manage_products"):
        assert never not in granted, never


def test_technical_staff_are_kept_out_of_the_pages_their_role_does_not_cover(world):
    person = world.people["technical staff"]
    for name, args in (("billing_staff:invoice_list", []), ("console:users", []), ("reports_staff:index", []),
                       ("console:email_providers", []), ("console:registrars", []), ("console:audit_log", []),
                       ("catalog_staff:product_list", []), ("orders_staff:new", []), ("clients_staff:create", [])):
        assert page_of(person, name, *args).status_code == 403, name
    for name in ("hosting_staff:list", "domains_staff:list", "support_staff:tickets", "clients_staff:list",
                 "orders_staff:list", "catalog_staff:server_list", "console:dashboard"):
        assert page_of(person, name).status_code == 200, name


def test_an_admin_can_create_technical_staff_but_a_manager_cannot(world):
    admin, manager = world.people["admin"], world.people["manager"]
    made = account_services.create_staff_user(admin, email="new.tech@example.com", role=Role.TECHNICAL, first_name="New")
    assert made.role == Role.TECHNICAL and made.is_staff and not made.is_superuser
    assert made.has_perm(perm("manage_hosting")) and not made.has_perm(perm("view_billing"))
    with pytest.raises(Exception):
        account_services.create_staff_user(manager, email="other.tech@example.com", role=Role.TECHNICAL)


# --- The Users area --------------------------------------------------------------------------------------------------

def test_the_users_list_has_a_tab_and_a_count_for_every_role(world):
    html = page_of(world.people["admin"], "console:users").content.decode()
    tabs = re.findall(r'<a class="role-tab[^"]*" href="[^"]*"[^>]*>([^<]+) <span class="count">(\d+)</span>', html)
    assert [t[0] for t in tabs] == ["All", "Customers", "Support", "Technical", "Managers", "Admins", "Super admins"]
    counts = {label: int(n) for label, n in tabs}
    assert counts["All"] == User.objects.count() and counts["Customers"] == User.objects.filter(role="customer").count()
    assert counts["Technical"] == 1 and counts["Super admins"] == 1


def test_the_users_list_filters_by_role_status_and_search(world):
    admin = world.people["admin"]
    client = browser(admin)
    only_tech = client.get(reverse("console:users") + "?role=technical").content.decode()
    assert "technical@harness.test" in only_tech and "agent@harness.test" not in only_tech
    found = client.get(reverse("console:users") + "?q=finance").content.decode()
    assert "finance@harness.test" in found and "manager@harness.test" not in found
    User.objects.filter(email="agent@harness.test").update(status="suspended")
    suspended = client.get(reverse("console:users") + "?status=suspended").content.decode()
    assert "agent@harness.test" in suspended and "manager@harness.test" not in suspended
    junk = client.get(reverse("console:users") + "?role=wizard&status=nope&page=abc")
    assert junk.status_code == 200  # a value that is not a choice is ignored, never an error


def test_the_users_list_costs_the_same_number_of_queries_however_many_people(world, django_assert_max_num_queries):
    client = browser(world.people["admin"])
    client.get(reverse("console:users"))  # warm the caches (brand, permissions)
    for i in range(15):
        User.objects.create_user(email=f"bulk{i}@example.com", password=None)
    with django_assert_max_num_queries(16):
        assert client.get(reverse("console:users")).status_code == 200


def test_who_may_open_the_users_screens(world):
    for who, expected in (("anonymous", 302), ("customer owner", 403), ("support agent", 403), ("technical staff", 403),
                          ("manager", 200), ("admin", 200), ("super admin", 200)):
        person = world.people[who]
        assert page_of(person, "console:users").status_code == expected, who
        assert page_of(person, "console:user_detail", world.people["admin"].pk).status_code == expected, who


def test_the_detail_page_shows_a_customers_clients_and_what_they_own(world):
    owner = world.people["customer owner"]
    html = page_of(world.people["manager"], "console:user_detail", owner.pk).content.decode()
    assert "Client accounts" in html and world.client.display_name in html
    assert reverse("clients_staff:detail", args=[world.client.pk]) in html
    agent_html = page_of(world.people["admin"], "console:user_detail", world.people["support agent"].pk).content.decode()
    assert "Open tickets assigned" in agent_html and "Client accounts" not in agent_html


def test_a_manager_may_edit_a_customer_but_sees_no_controls_for_an_admin(world):
    manager = world.people["manager"]
    customer_html = page_of(manager, "console:user_detail", world.people["customer owner"].pk).content.decode()
    assert reverse("console:user_edit", args=[world.people["customer owner"].pk]) in customer_html
    assert reverse("console:staff_user_status", args=[world.people["customer owner"].pk]) in customer_html
    assert "staff_user_role" not in customer_html and "/role/" not in customer_html  # a customer has no staff role to change
    admin_html = page_of(manager, "console:user_detail", world.people["admin"].pk).content.decode()
    assert "/details/" not in admin_html and "/status/" not in admin_html and "/role/" not in admin_html


def test_a_manager_cannot_change_a_role_because_only_people_who_assign_roles_can(world):
    html = page_of(world.people["manager"], "console:user_detail", world.people["support agent"].pk).content.decode()
    assert "/status/" in html and "/role/" not in html
    admin_html = page_of(world.people["admin"], "console:user_detail", world.people["support agent"].pk).content.decode()
    assert "/role/" in admin_html


def test_an_admin_cannot_touch_a_super_admin_but_a_super_admin_can_touch_an_admin(world):
    admin, root = world.people["admin"], world.people["super admin"]
    html = page_of(admin, "console:user_detail", root.pk).content.decode()
    assert "/status/" not in html and "/details/" not in html and "/role/" not in html
    assert "/role/" in page_of(root, "console:user_detail", admin.pk).content.decode()
    # ... and the server says the same when the page is bypassed
    client = browser(admin)
    for name in ("console:user_edit", "console:staff_user_status", "console:staff_user_role"):
        client.post(reverse(name, args=[root.pk]), {"status": "suspended", "role": "customer", "first_name": "Hacked"})
    root.refresh_from_db()
    assert root.status == "active" and root.role == "super_admin" and root.first_name != "Hacked"


def test_a_manager_cannot_reach_an_admin_by_posting_directly(world):
    manager, admin = world.people["manager"], world.people["admin"]
    client = browser(manager)
    client.post(reverse("console:user_edit", args=[admin.pk]), {"first_name": "Hacked"})
    client.post(reverse("console:staff_user_status", args=[admin.pk]), {"status": "suspended", "reason": "x"})
    client.post(reverse("console:staff_user_role", args=[world.people["support agent"].pk]), {"role": "admin"})
    admin.refresh_from_db()
    assert admin.first_name != "Hacked" and admin.status == "active"
    assert User.objects.get(pk=world.people["support agent"].pk).role == "support_agent"


def test_nobody_can_change_their_own_role_status_or_details_from_the_users_screen(world):
    admin = world.people["admin"]
    html = page_of(admin, "console:user_detail", admin.pk).content.decode()
    assert "/details/" not in html and "/status/" not in html and "your own account" in html
    client = browser(admin)
    client.post(reverse("console:staff_user_status", args=[admin.pk]), {"status": "suspended"})
    client.post(reverse("console:user_edit", args=[admin.pk]), {"first_name": "Me"})
    admin.refresh_from_db()
    assert admin.status == "active" and admin.first_name != "Me"


def test_editing_a_persons_details_is_saved_and_audited(world):
    manager, customer = world.people["manager"], world.people["customer owner"]
    response = browser(manager).post(reverse("console:user_edit", args=[customer.pk]),
                                     {"first_name": "Adaline", "last_name": "Lovelace", "phone": "+1 555 0100"})
    assert response.status_code == 302 and response.url == reverse("console:user_detail", args=[customer.pk])
    customer.refresh_from_db()
    assert (customer.first_name, customer.last_name, customer.phone) == ("Adaline", "Lovelace", "+1 555 0100")
    event = AuditEvent.objects.filter(action="account.details_updated", target_id=str(customer.pk)).latest("id")
    assert event.actor_id == manager.pk and event.metadata["fields"] == ["first_name", "last_name", "phone"]


def test_suspending_and_reactivating_a_customer_works_from_the_detail_page(world):
    manager = world.people["manager"]
    target = world.people["technical contact"]
    client = browser(manager)
    client.post(reverse("console:staff_user_status", args=[target.pk]), {"status": "suspended", "reason": "chargeback"})
    target.refresh_from_db()
    assert target.status == "suspended" and not target.is_active
    assert "Reactivate" in client.get(reverse("console:user_detail", args=[target.pk])).content.decode()
    client.post(reverse("console:staff_user_status", args=[target.pk]), {"status": "active"})
    target.refresh_from_db()
    assert target.status == "active"


def test_an_agent_cannot_use_the_user_actions_at_all(world):
    agent = world.people["support agent"]
    target = world.people["technical contact"]
    for name, data in (("console:user_edit", {"first_name": "x"}), ("console:staff_user_status", {"status": "suspended"})):
        assert browser(agent).post(reverse(name, args=[target.pk]), data).status_code == 403, name


def test_the_role_action_only_applies_to_staff(world):
    admin = world.people["admin"]
    assert browser(admin).post(reverse("console:staff_user_role", args=[world.people["customer owner"].pk]),
                               {"role": "manager"}).status_code == 404
    assert User.objects.get(pk=world.people["customer owner"].pk).role == "customer"


def test_the_add_staff_form_reports_a_mistake_on_the_users_screen(world):
    response = browser(world.people["admin"]).post(reverse("console:staff_user_create"), {"email": "nope", "role": "technical"})
    html = response.content.decode()
    assert response.status_code == 200 and 'id="add-staff"' in html and 'class="collapse show"' in html
    assert "Enter a valid email address" in html


def test_a_new_staff_member_lands_on_their_own_page(world):
    response = browser(world.people["admin"]).post(reverse("console:staff_user_create"),
                                                   {"email": "fresh.tech@example.com", "role": "technical", "first_name": "Fresh"})
    made = User.objects.get(email="fresh.tech@example.com")
    assert response.status_code == 302 and response.url == reverse("console:user_detail", args=[made.pk])


def test_the_old_staff_and_roles_address_still_works(world):
    response = page_of(world.people["manager"], "console:staff_users")
    assert response.status_code == 302 and response.url == reverse("console:users")
    assert page_of(world.people["support agent"], "console:staff_users").status_code == 403


def test_someone_who_may_only_view_users_sees_no_controls_and_cannot_post(world):
    """No built-in role views users without managing them, but a custom permission set could: the page and the view must both hold."""
    from django.contrib.auth.models import Permission

    viewer = User.objects.create_user(email="viewer@example.com", password=PASSWORD, role=Role.SUPPORT_AGENT)
    account_services.sync_role_membership(viewer)
    viewer.user_permissions.add(Permission.objects.get(codename="view_users"))
    viewer = User.objects.get(pk=viewer.pk)
    target = world.people["technical contact"]
    html = page_of(viewer, "console:user_detail", target.pk).content.decode()
    assert "/details/" not in html and "/status/" not in html and "Add a staff member" not in page_of(viewer, "console:users").content.decode()
    assert browser(viewer).post(reverse("console:user_edit", args=[target.pk]), {"first_name": "x"}).status_code == 403
    assert browser(viewer).post(reverse("console:staff_user_status", args=[target.pk]), {"status": "suspended"}).status_code == 403


def test_nobody_may_edit_themselves_on_this_screen():
    from apps.console import views_users

    class Person:
        pk, role, is_superuser = 1, "customer", False

    class Actor(Person):
        pk = 2

    assert views_users.can_edit(Actor(), Person()) is True
    Actor.pk = Person.pk
    assert views_users.can_edit(Actor(), Person()) is False
