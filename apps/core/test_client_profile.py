"""Phase D4b: the staff client profile as tabs, each its own URL and shown only to staff who may open what it lists."""
import re

import pytest
from django.test import Client as HttpClient
from django.urls import reverse

from apps.clients import tabs as client_tabs

pytestmark = pytest.mark.django_db

# Tabs each role sees (the roles' permissions decide; Profile needs manage_clients, Emails view_settings, Log view_audit_log)
EXPECTED = {
    "support agent": ["Summary", "Contacts", "Products/Services", "Domains", "Billable Items", "Invoices", "Quotes",
                      "Transactions", "Tickets", "Cancellations", "Notes"],
    "manager": ["Summary", "Profile", "Contacts", "Products/Services", "Domains", "Billable Items", "Invoices", "Quotes",
                "Transactions", "Tickets", "Cancellations", "Affiliate", "Notes", "Log"],
    "admin": [t.label for t in client_tabs.TABS],
}


def browser(user=None):
    client = HttpClient(HTTP_HOST="localhost", raise_request_exception=False)
    if user is not None:
        client.force_login(user)
    return client


def bar(response):
    html = response.content.decode().split('<ul class="nav nav-tabs')[1].split("</ul>")[0]
    return [(re.sub(r"<[^>]+>", "", label).strip(), url) for url, label in
            re.findall(r'<a class="nav-link[^"]*" href="([^"]+)"[^>]*>(.*?)</a>', html, flags=re.S)]


def labels(response):
    return [re.sub(r"\s+\d+$", "", label) for label, _url in bar(response)]


@pytest.fixture
def profile(world):
    return reverse("clients_staff:detail", args=[world.client.pk])


# --- The tab bar ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("role", list(EXPECTED))
def test_each_role_sees_the_tabs_it_may_open(world, profile, role):
    assert labels(browser(world.people[role]).get(profile)) == EXPECTED[role]


@pytest.mark.parametrize("role", list(EXPECTED))
def test_every_tab_a_person_is_shown_opens_for_them(world, profile, role):
    client = browser(world.people[role])
    for label, url in bar(client.get(profile)):
        assert client.get(url).status_code == 200, (role, label, url)


@pytest.mark.parametrize("role", list(EXPECTED))
def test_a_tab_a_person_is_not_shown_is_refused_when_asked_for_directly(world, role):
    client = browser(world.people[role])
    shown = {url for _l, url in bar(client.get(reverse("clients_staff:detail", args=[world.client.pk])))}
    for tab in client_tabs.TABS:
        url = tab.url(world.client)
        if url not in shown:
            assert client.get(url).status_code in (302, 403), (role, tab.key)


def test_each_tab_is_its_own_page_and_marks_itself_current(world):
    client = browser(world.people["admin"])
    for tab in client_tabs.TABS:
        response = client.get(tab.url(world.client))
        assert response.status_code == 200, tab.key
        current = [re.sub(r"\s+\d+$", "", label) for label, url in bar(response) if url == tab.url(world.client)]
        assert current == [tab.label]
        assert f'aria-current="page">{tab.label}' in response.content.decode(), tab.key
    assert len({tab.url(world.client) for tab in client_tabs.TABS}) == len(client_tabs.TABS)


def test_the_tab_counts_equal_the_rows_they_open(world):
    client = browser(world.people["admin"])
    response = client.get(reverse("clients_staff:detail", args=[world.client.pk]))
    counts = {label: int(n) for label, n in re.findall(r'nav-link[^>]*>([^<]+?) <span class="badge[^>]*>(\d+)</span>',
                                                        response.content.decode())}
    assert counts, "no counts shown"
    by_label = {t.label: t for t in client_tabs.TABS}
    for label, number in counts.items():
        tab = by_label[label]
        assert client.get(tab.url(world.client)).context["page"].paginator.count == number, label


def test_a_client_with_nothing_shows_empty_tabs_not_someone_elses_records(world):
    from apps.clients import services as client_services

    other = client_services.create_client(world.people["manager"], {"first_name": "Zed", "email": "zed@elsewhere.test",
                                                                    "country": "US"})
    admin = browser(world.people["admin"])
    for tab in ("services", "domains", "invoices", "quotes", "transactions", "tickets", "cancellations", "billable"):
        page = admin.get(reverse("clients_staff:tab", args=[other.pk, tab]))
        assert page.context["rows"] == [] and b"Nothing here yet" in page.content, tab
    log = admin.get(reverse("clients_staff:tab", args=[other.pk, "log"]))
    assert all(str(other.pk) in (e.target_id, str(e.metadata.get("client_id"))) for e in log.context["page"])


# --- The list tabs ----------------------------------------------------------------------------------------------------

def test_each_list_tab_shows_this_clients_records_in_the_agreed_columns(world):
    client = browser(world.people["admin"])
    from apps.lifecycle.models import CancellationRequest

    expected = {"services": ("Domain", world.client.hosting_accounts.count()), "domains": ("Domain", world.client.domains.count()),
                "invoices": ("Invoice", world.client.invoices.count()), "quotes": ("Quote", world.client.quotes.count()),
                "tickets": ("Ticket", world.client.tickets.count()),
                "cancellations": ("Request", CancellationRequest.objects.filter(client=world.client).count())}
    for key, (first_column, rows) in expected.items():
        response = client.get(reverse("clients_staff:tab", args=[world.client.pk, key]))
        assert rows >= 5, key  # the world has several of each
        assert response.context["columns"][0] == first_column and len(response.context["rows"]) == rows, key


def test_a_cell_links_to_the_record_and_the_link_opens(world):
    client = browser(world.people["admin"])
    for key in ("services", "domains", "invoices", "quotes", "transactions", "tickets", "cancellations"):
        response = client.get(reverse("clients_staff:tab", args=[world.client.pk, key]))
        links = [c["url"] for row in response.context["rows"] for c in row if c.get("url")]
        assert links, key
        for url in links[:6]:
            assert client.get(url).status_code == 200, (key, url)


def test_dates_and_money_are_written_in_the_brand_format(world):
    page = browser(world.people["admin"]).get(reverse("clients_staff:tab", args=[world.client.pk, "invoices"])).content.decode()
    assert re.search(r"\d{2} [A-Z][a-z]{2} \d{4}", page) and "100.00" in page and "Overdue" in page and "Draft" in page


def test_a_long_list_is_paged_by_twenty_five_with_a_count(world):
    client = browser(world.people["admin"])
    from apps.audit.models import AuditEvent

    for n in range(40):
        AuditEvent.objects.create(action="client.touched", target_type="clients.client", target_id=str(world.client.pk))
    url = reverse("clients_staff:tab", args=[world.client.pk, "log"])
    first = client.get(url)
    assert len(first.context["rows"]) == 25 and first.context["page"].paginator.count > 40
    assert b"Showing 1" in first.content and b"of " in first.content
    assert len(client.get(url, {"page": 2}).context["rows"]) == 25
    assert client.get(url, {"page": "junk"}).status_code == 200 and client.get(url, {"page": 999}).status_code == 200


def test_the_emails_tab_lists_mail_to_the_client_and_its_contacts_and_nobody_elses(world):
    from apps.notifications.models import EmailMessage

    EmailMessage.objects.create(event="x", template="x", to_email="tech@harness.test", subject="To a contact", body_text="x")
    EmailMessage.objects.create(event="x", template="x", to_email="stranger@else.test", subject="Not this client", body_text="x")
    page = browser(world.people["admin"]).get(reverse("clients_staff:tab", args=[world.client.pk, "emails"])).content.decode()
    assert "To a contact" in page and "Not this client" not in page


def test_the_log_shows_this_clients_events_only(world):
    page = browser(world.people["admin"]).get(reverse("clients_staff:tab", args=[world.client.pk, "log"]))
    events = list(page.context["page"])
    assert events and all(str(world.client.pk) in (e.target_id, str(e.metadata.get("client_id"))) for e in events)


def test_the_affiliate_tab_shows_the_referral_and_the_credit_form_only_to_who_may_use_it(world):
    admin = browser(world.people["admin"]).get(reverse("clients_staff:tab", args=[world.client.pk, "affiliate"]))
    assert b"not referred by an affiliate" in admin.content and b"Credit this affiliate" in admin.content
    manager = browser(world.people["manager"]).get(reverse("clients_staff:tab", args=[world.client.pk, "affiliate"]))
    assert b"Credit this affiliate" in manager.content  # managers manage affiliates too


def test_an_unknown_tab_and_an_unknown_client_are_not_found(world):
    client = browser(world.people["admin"])
    assert client.get(f"/staff/clients/{world.client.pk}/tab/nonsense/").status_code == 404
    assert client.get(f"/staff/clients/{world.client.pk}/tab/summary/").status_code == 404  # summary is the profile itself
    assert client.get("/staff/clients/999999/tab/invoices/").status_code == 404
    assert client.get("/staff/clients/999999/contacts/").status_code == 404


def test_customers_and_visitors_cannot_open_any_tab(world):
    for role in ("customer owner", "billing contact", None):
        client = browser(world.people[role] if role else None)
        for tab in client_tabs.TABS:
            assert client.get(tab.url(world.client)).status_code in (302, 403), (role, tab.key)


# --- Contacts and notes -----------------------------------------------------------------------------------------------

def test_the_contacts_tab_lists_the_people_and_offers_changes_only_to_who_may_make_them(world):
    manager = browser(world.people["manager"]).get(reverse("clients_staff:contact_add", args=[world.client.pk])).content.decode()
    for email in ("ada@harness.test", "finance@harness.test", "tech@harness.test"):
        assert email in manager
    assert "Add user" in manager and "Remove" in manager
    agent = browser(world.people["support agent"]).get(reverse("clients_staff:contact_add", args=[world.client.pk])).content.decode()
    assert "ada@harness.test" in agent and "Add user" not in agent and "Remove" not in agent


def test_contact_changes_return_to_the_contacts_tab(world):
    client = browser(world.people["manager"])
    contacts = reverse("clients_staff:contact_add", args=[world.client.pk])
    response = client.post(contacts, {"email": "new@harness.test", "role": "technical", "first_name": "N", "last_name": "P"})
    assert response.headers["Location"] == contacts
    contact = world.client.contacts.get(user__email="new@harness.test")
    role = client.post(reverse("clients_staff:contact_role", args=[world.client.pk, contact.pk]), {"role": "billing"})
    assert role.headers["Location"] == contacts and world.client.contacts.get(pk=contact.pk).role == "billing"
    removed = client.post(reverse("clients_staff:contact_remove", args=[world.client.pk, contact.pk]))
    assert removed.headers["Location"] == contacts and not world.client.contacts.filter(pk=contact.pk).exists()


def test_a_status_change_still_returns_to_the_summary(world):
    client = browser(world.people["manager"])
    response = client.post(reverse("clients_staff:status", args=[world.client.pk]), {"status": "active", "reason": ""})
    assert response.headers["Location"] == reverse("clients_staff:detail", args=[world.client.pk])


def test_only_managers_can_add_contacts_and_a_get_never_changes_anything(world):
    agent = browser(world.people["support agent"])
    url = reverse("clients_staff:contact_add", args=[world.client.pk])
    before = world.client.contacts.count()
    assert agent.post(url, {"email": "x@harness.test", "role": "technical"}).status_code == 403
    browser(world.people["manager"]).get(url)
    assert world.client.contacts.count() == before


def test_the_notes_tab_shows_the_internal_notes_and_the_edit_link_only_to_managers(world):
    from apps.clients.models import Client

    Client.objects.filter(pk=world.client.pk).update(notes="Prefers email.\nPays late.")
    url = reverse("clients_staff:tab", args=[world.client.pk, "notes"])
    manager = browser(world.people["manager"]).get(url).content.decode()
    assert "Prefers email." in manager and reverse("clients_staff:edit", args=[world.client.pk]) in manager
    agent = browser(world.people["support agent"]).get(url).content.decode()
    assert "Pays late." in agent and reverse("clients_staff:edit", args=[world.client.pk]) not in agent
    assert "Prefers email" not in browser(world.people["customer owner"]).get(url).content.decode()


def test_the_edit_page_sits_inside_the_profile_with_its_tab_marked(world):
    page = browser(world.people["manager"]).get(reverse("clients_staff:edit", args=[world.client.pk])).content.decode()
    assert 'aria-current="page">Profile' in page and "Invoices" in page
    new_client = browser(world.people["manager"]).get(reverse("clients_staff:create")).content.decode()
    assert "nav-tabs" not in new_client  # creating a client has no profile yet


# --- The summary and the header ---------------------------------------------------------------------------------------

def test_the_summary_has_the_details_the_status_form_the_counts_and_five_recent_events(world, profile):
    response = browser(world.people["manager"]).get(profile)
    page = response.content.decode()
    assert "Ada Ltd" in page and "Update status" in page and "Recent activity" in page
    assert len(response.context["activity"]) <= 5 and page.count("stat-tile") >= 6
    assert page.count("<h1") == 1


def test_the_header_offers_quick_actions_by_permission_and_they_open(world, profile):
    manager = browser(world.people["manager"])
    page = manager.get(profile).content.decode()
    for label in ("New invoice", "New quote", "New order", "Open a ticket"):
        assert label in page
    for url in re.findall(r'class="btn btn-sm btn-outline-primary" href="([^"]+)"', page):
        assert manager.get(url).status_code in (200, 302), url
    agent = browser(world.people["support agent"]).get(profile).content.decode()
    assert "New invoice" not in agent and "New order" not in agent and "Open a ticket" in agent


def test_the_old_all_in_one_page_is_gone_but_its_address_still_works(world, profile):
    page = browser(world.people["manager"]).get(profile).content.decode()
    assert "Account records" not in page and 'class="records"' not in page
