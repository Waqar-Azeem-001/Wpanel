"""
Owner requests, second round (2026-09-26): mail "sent" but never received (SPF / envelope sender / deliverability check), a client who
cannot sign in (sign-in link, password when adding a client), and email wording that a Super Admin edits.
"""
import pytest
from django.core import mail
from django.test import Client as HttpClient
from django.urls import reverse

from apps.accounts import services as account_services
from apps.accounts.models import User
from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.core.exceptions import ServiceError
from apps.notifications import deliverability, mail_templates
from apps.notifications import services as notifications
from apps.notifications.backends import EnvelopeSMTPBackend
from apps.notifications.models import EmailMessage, EmailProvider, EmailTemplateOverride

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ng-Passw0rd!x"


def browser(user=None):
    client = HttpClient(HTTP_HOST="localhost", raise_request_exception=False)
    if user is not None:
        client.force_login(user)
    return client


@pytest.fixture
def boss(staff):
    return staff(Role.SUPER_ADMIN)


@pytest.fixture
def admin(staff):
    return staff(Role.ADMIN)


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


# --- Mail accepted but not received ------------------------------------------------------------------------------------

def fake_dns(monkeypatch, records):
    """records: {(name, type): [answers]}; a missing one is an empty answer."""
    monkeypatch.setattr(deliverability, "_query", lambda name, rtype: records.get((name, rtype), []))


def test_spf_passes_only_for_a_listed_server(monkeypatch):
    fake_dns(monkeypatch, {("shop.test", "TXT"): ["v=spf1 +a +mx ip4:10.0.0.0/24 include:relay.test ~all"],
                           ("relay.test", "TXT"): ["v=spf1 ip4:203.0.113.5 -all"],
                           ("shop.test", "A"): ["198.51.100.7"]})
    assert deliverability.spf_result("shop.test", "10.0.0.9") == "pass"        # ip4 range
    assert deliverability.spf_result("shop.test", "198.51.100.7") == "pass"    # the domain's own address
    assert deliverability.spf_result("shop.test", "203.0.113.5") == "pass"     # through include
    assert deliverability.spf_result("shop.test", "192.0.2.1") == "softfail"   # not listed, ~all
    assert deliverability.spf_result("nospf.test", "192.0.2.1") == "none"


def test_the_deliverability_report_names_the_problem_and_the_fix(monkeypatch):
    fake_dns(monkeypatch, {("brand.test", "TXT"): ["v=spf1 +a ~all"], ("login.test", "TXT"): ["v=spf1 ip4:173.212.243.56 ~all"]})
    monkeypatch.setattr(deliverability.socket, "gethostbyname", lambda host: "173.212.243.56")
    provider = EmailProvider(name="P", host="smtp.brand.test", username="info@login.test", from_email="Brand <info@brand.test>")
    levels = dict((text[:5], level) for level, text in deliverability.report(provider))
    text = " ".join(t for _, t in deliverability.report(provider))
    assert "does NOT list 173.212.243.56" in text and "Envelope sender" in text and levels["SPF: "] == "bad"
    provider.envelope_from = "info@login.test"  # the fix: the login's domain lists this server
    fixed = deliverability.report(provider)
    assert ("ok", "SPF: login.test allows 173.212.243.56 to send. Good.") in fixed
    assert not any(level == "bad" for level, _ in fixed)


def test_an_unreachable_dns_is_not_reported_as_a_missing_record(monkeypatch):
    def broken(name, rtype):
        raise deliverability.LookupFailed(name)

    monkeypatch.setattr(deliverability, "_query", broken)
    monkeypatch.setattr(deliverability.socket, "gethostbyname", lambda host: "1.2.3.4")
    checks = deliverability.report(EmailProvider(name="P", host="h.test", username="a@b.test", from_email="a@b.test"))
    assert checks[-1][0] == "warn" and "could not be reached" in checks[-1][1]


def test_the_envelope_sender_is_used_for_mail_from_but_not_the_visible_from():
    sent = {}

    class Conn:
        def sendmail(self, sender, recipients, data):
            sent.update(sender=sender, recipients=recipients, data=data)

    backend = EnvelopeSMTPBackend(host="x.test", envelope_from="bounces@login.test")
    backend.connection = Conn()
    message = mail.EmailMessage("Hi", "Body", "Shop <info@brand.test>", ["a@x.test"])
    assert backend._send(message)
    assert sent["sender"] == "bounces@login.test" and b"From: Shop <info@brand.test>" in sent["data"]


def test_the_provider_pages_offer_the_check_and_the_setting(admin, monkeypatch):
    provider = EmailProvider(name="P", host="smtp.brand.test", username="info@login.test", from_email="info@brand.test", is_active=True)
    provider.set_password("secret-password-value")
    provider.save()
    fake_dns(monkeypatch, {("brand.test", "TXT"): ["v=spf1 +a ~all"]})
    monkeypatch.setattr(deliverability.socket, "gethostbyname", lambda host: "203.0.113.9")
    page = browser(admin).get(reverse("console:email_providers")).content.decode()
    assert reverse("console:email_provider_check", args=[provider.pk]) in page and "Envelope sender" in page
    check = browser(admin).get(reverse("console:email_provider_check", args=[provider.pk]))
    assert check.status_code == 200 and b"does NOT list 203.0.113.9" in check.content and b"secret-password-value" not in check.content
    assert browser().get(reverse("console:email_provider_check", args=[provider.pk])).status_code == 302


def test_the_envelope_sender_is_saved_with_the_provider(admin):
    saved = notifications.save_email_provider(admin, None, {
        "name": "P", "kind": "smtp", "host": "smtp.brand.test", "port": 587, "username": "info@login.test", "use_tls": True,
        "use_ssl": False, "timeout": 20, "from_email": "info@brand.test", "envelope_from": "info@login.test", "is_active": True},
        password="x")
    assert saved.envelope_from == "info@login.test"
    assert AuditEvent.objects.get(action="email_provider.created").metadata["fields"].count("envelope_from") == 1


# --- A client who cannot sign in ------------------------------------------------------------------------------------------

@pytest.fixture
def acme(manager):
    return client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test", "company_name": "Acme"})


def test_a_super_admin_can_make_a_sign_in_link_that_works_once(boss, acme):
    owner = acme.contacts.get().user
    link = account_services.admin_setup_link(boss, owner)
    assert "/account/password/reset/" in link or "reset" in link
    assert AuditEvent.objects.get(action="account.setup_link_created").target_id == str(owner.pk)
    assert link not in str(AuditEvent.objects.get(action="account.setup_link_created").metadata)
    from urllib.parse import urlparse

    path = urlparse(link).path
    page = browser().get(path, follow=True)
    assert page.status_code == 200
    from django.urls import resolve

    kwargs = resolve(path).kwargs
    uid, token = kwargs["uidb64"], kwargs["token"]
    account_services.reset_password(uid, token, PASSWORD)
    with pytest.raises(ServiceError):
        account_services.reset_password(uid, token, "An0ther-Passw0rd!z")  # used: the link is dead


@pytest.mark.parametrize("role", [Role.ADMIN, Role.MANAGER])
def test_only_a_super_admin_can_make_a_sign_in_link(make_user, acme, role):
    with pytest.raises(ServiceError) as exc:
        account_services.admin_setup_link(make_user(f"x-{role}@example.com", role=role), acme.contacts.get().user)
    assert exc.value.status_code == 403


def test_the_sign_in_link_button_is_shown_to_and_works_for_a_super_admin_only(boss, admin, acme):
    owner = acme.contacts.get().user
    url = reverse("console:user_detail", args=[owner.pk])
    assert reverse("console:user_setup_link", args=[owner.pk]) in browser(boss).get(url).content.decode()
    assert reverse("console:user_setup_link", args=[owner.pk]) not in browser(admin).get(url).content.decode()
    response = browser(boss).post(reverse("console:user_setup_link", args=[owner.pk]), follow=True)
    assert b"Give this link to" in response.content and b"reset" in response.content
    before = AuditEvent.objects.filter(action="account.setup_link_created").count()
    browser(admin).post(reverse("console:user_setup_link", args=[owner.pk]))
    assert AuditEvent.objects.filter(action="account.setup_link_created").count() == before


def test_a_super_admin_can_add_a_client_with_a_password_and_no_email(boss):
    mail.outbox.clear()
    client = client_services.create_client(boss, {"first_name": "Zed", "email": "zed@acme.test"}, owner_password=PASSWORD)
    owner = client.contacts.get().user
    assert owner.check_password(PASSWORD) and owner.has_usable_password()
    assert not any(m.to == ["zed@acme.test"] for m in mail.outbox)
    assert AuditEvent.objects.get(action="client.created").metadata["password_set_by_admin"] is True
    assert PASSWORD not in str(AuditEvent.objects.get(action="client.created").metadata)
    login = browser().post("/account/login/", {"email": "zed@acme.test", "password": PASSWORD})
    assert login.status_code == 302


def test_setting_a_password_when_adding_a_client_is_a_super_admin_thing_and_is_checked(admin, boss, acme):
    with pytest.raises(ServiceError) as exc:
        client_services.create_client(admin, {"first_name": "Y", "email": "y@x.test"}, owner_password=PASSWORD)
    assert exc.value.status_code == 403 and not User.objects.filter(email="y@x.test").exists()
    with pytest.raises(ServiceError) as exc:
        client_services.create_client(boss, {"first_name": "Y", "email": "y@x.test"}, owner_password="12345678")
    assert exc.value.code == "weak_password" and not User.objects.filter(email="y@x.test").exists()
    with pytest.raises(ServiceError) as exc:
        client_services.create_client(boss, {"first_name": "Y", "email": "y@x.test"}, owner_email="ada@acme.test",
                                      owner_password=PASSWORD)
    assert exc.value.code == "owner_exists"


def test_the_add_client_form_offers_the_password_to_a_super_admin_only(boss, manager):
    assert "owner_password" in browser(boss).get(reverse("clients_staff:create")).content.decode()
    assert "owner_password" not in browser(manager).get(reverse("clients_staff:create")).content.decode()
    made = browser(boss).post(reverse("clients_staff:create"), {
        "first_name": "Web", "email": "web@client.test", "currency": "USD", "country": "PK", "owner_password": PASSWORD})
    assert made.status_code == 302 and User.objects.get(email="web@client.test").check_password(PASSWORD)


# --- Email wording -----------------------------------------------------------------------------------------------------------

def test_every_email_can_be_edited_and_the_variables_are_listed():
    keys = mail_templates.known_templates()
    assert "invoice_issued" in keys and "password_reset" in keys and len(keys) >= 25
    assert "invoice.number" in mail_templates.variables("invoice_issued") and "site_name" in mail_templates.variables("password_reset")
    subject, body = mail_templates.default_source("password_reset")
    assert "{{ link }}" in body and subject


def test_a_super_admin_changes_the_wording_and_new_emails_use_it(boss, customer):
    notifications.save_template(boss, "ticket_opened", "Got it, {{ user.get_short_name }}", "Hi {{ user.first_name }},\n\nWe have your ticket.\n\n{{ site_name }}")
    assert EmailTemplateOverride.objects.get().updated_by == boss
    customer.first_name = "Ada"
    customer.save()
    message = notifications.send_email(to_email=customer.email, template="ticket_opened", event="ticket.opened",
                                       context={"user": customer, "ticket": type("T", (), {"reference": "T1", "subject": "S"})()})
    assert message.subject == "Got it, Ada" and "We have your ticket." in message.body_text and "<a" not in message.body_text
    assert "We have your ticket." in message.body_html  # the branded HTML is built from the text
    reset = notifications.reset_template(boss, "ticket_opened")
    assert reset and not EmailTemplateOverride.objects.exists()
    default = notifications.send_email(to_email=customer.email, template="ticket_opened", event="ticket.opened",
                                       context={"user": customer, "ticket": type("T", (), {"reference": "T1", "subject": "S"})()})
    assert "We have your ticket." not in default.body_text


@pytest.mark.parametrize("role", [Role.ADMIN, Role.MANAGER, Role.SUPPORT_AGENT])
def test_only_a_super_admin_can_edit_wording(make_user, role):
    person = make_user(f"w-{role}@example.com", role=role)
    with pytest.raises(ServiceError) as exc:
        notifications.save_template(person, "ticket_opened", "Subject", "Body")
    assert exc.value.status_code == 403
    assert browser(person).get(reverse("notifications_staff:templates")).status_code == 403
    assert browser(person).post(reverse("notifications_staff:template", args=["ticket_opened"]), {"subject": "x", "body_text": "y"}).status_code == 403
    assert not EmailTemplateOverride.objects.exists()


def test_bad_wording_is_refused_before_it_is_stored(boss):
    for subject, body, code in (("", "Body", "template_empty"), ("S", " ", "template_empty"), ("A\nB", "Body", "template_invalid"),
                                ("S", "Hello {% if %}", "template_invalid"), ("S", "{% include \"emails/password_reset.txt\" %}", "template_invalid"),
                                ("S", "{% load i18n %}x", "template_invalid")):
        with pytest.raises(ServiceError) as exc:
            notifications.save_template(boss, "ticket_opened", subject, body)
        assert exc.value.code == code
    with pytest.raises(ServiceError) as exc:
        notifications.save_template(boss, "not_a_template", "S", "B")
    assert exc.value.code == "unknown_template"
    assert not EmailTemplateOverride.objects.exists()


def test_a_message_with_a_one_time_link_must_keep_the_link(boss):
    with pytest.raises(ServiceError) as exc:
        notifications.save_template(boss, "password_reset", "Reset", "Click the link we forgot to include.")
    assert exc.value.code == "template_link_missing"
    notifications.save_template(boss, "password_reset", "Reset your password", "Open {{ link }} to choose a new one.")


def test_wording_cannot_reach_the_password_hash_or_files(boss, customer):
    notifications.save_template(boss, "ticket_opened", "S {{ user.password }}", "{{ user.password }}|{{ user.is_superuser }}|{{ user.first_name }}")
    message = notifications.send_email(to_email=customer.email, template="ticket_opened", event="ticket.opened",
                                       context={"user": customer, "ticket": None})
    assert customer.password not in message.subject + message.body_text and "pbkdf2" not in message.body_text
    assert message.body_text.startswith("||")


def test_the_editor_pages_preview_save_and_reset(boss):
    url = reverse("notifications_staff:template", args=["ticket_opened"])
    listing = browser(boss).get(reverse("notifications_staff:templates")).content.decode()
    assert "ticket_opened" in listing and "Default" in listing
    page = browser(boss).get(url).content.decode()
    assert "Variables you can use" in page and "Save wording" in page and "Reset to default" not in page
    preview = browser(boss).post(url, {"do": "preview", "subject": "Hi {{ user.get_short_name }}", "body_text": "Ticket {{ ticket.reference }}"})
    assert b"\xc2\xabuser.get_short_name\xc2\xbb" in preview.content and not EmailTemplateOverride.objects.exists()
    saved = browser(boss).post(url, {"do": "save", "subject": "New subject", "body_text": "New body {{ site_name }}"})
    assert saved.status_code == 302 and EmailTemplateOverride.objects.get().subject == "New subject"
    assert "Reset to default" in browser(boss).get(url).content.decode() and "Customised" in browser(boss).get(reverse("notifications_staff:templates")).content.decode()
    error = browser(boss).post(url, {"do": "save", "subject": "x", "body_text": "{% bogus %}"})
    assert error.status_code == 200 and b"mistake" in error.content and EmailTemplateOverride.objects.get().subject == "New subject"
    browser(boss).post(url, {"do": "reset"})
    assert not EmailTemplateOverride.objects.exists()
    assert browser(boss).get(reverse("notifications_staff:template", args=["nope"])).status_code == 404
    assert AuditEvent.objects.filter(action__in=["email_template.saved", "email_template.reset"]).count() == 2


def test_the_email_templates_menu_entry_is_for_a_super_admin_only(boss, admin):
    assert reverse("notifications_staff:templates") in browser(boss).get(reverse("console:dashboard")).content.decode()
    assert reverse("notifications_staff:templates") not in browser(admin).get(reverse("console:dashboard")).content.decode()


# --- Reports and dashboard numbers -------------------------------------------------------------------------------------------

def test_the_reports_index_shows_sections_of_numbers_and_a_grid_of_report_cards(boss):
    page = browser(boss).get(reverse("reports_staff:index")).content.decode()
    for heading in ("Money", "Orders and customers", "Services", "Support"):
        assert heading in page
    for label in ("Income this year", "Unpaid balance", "Refunds this month", "Average order", "Orders awaiting payment",
                  "Active domains", "Expiring in 30 days", "Resolved this week", "Unassigned tickets"):
        assert label in page
    assert page.count("rep-card") >= 10 and "Sales performance" in page


def test_a_person_sees_only_the_numbers_they_may_open(staff):
    agent = browser(staff(Role.SUPPORT_AGENT)).get(reverse("reports_staff:index"))
    if agent.status_code == 200:
        text = agent.content.decode()
        assert "Income this year" not in text and "Unpaid balance" not in text


def test_the_dashboard_has_the_second_rank_of_numbers(boss):
    page = browser(boss).get(reverse("console:widget", args=["summary"])).content.decode()
    for label in ("Active domains", "Income this year", "Refunds this month", "Payments to confirm", "Average order",
                  "New customers today", "Tickets resolved"):
        assert label in page


# --- The customer menu and store categories under Services ---------------------------------------------------------------------------

@pytest.fixture
def owner_user(acme):
    return acme.contacts.get().user


def request_for(user, path="/account/profile/"):
    from django.test import RequestFactory
    from django.urls import resolve

    request = RequestFactory().get(path)
    request.user = user
    request.resolver_match = resolve(path)
    return request


def test_the_customer_menu_lists_the_store_categories_under_services(owner_user):
    from apps.core import navigation

    menus = navigation.build(request_for(owner_user), "client")
    services = next(e for e in menus["main"] if e.key == "client.services")
    kids = {c.key: c for c in services.children}
    assert "client.services.cat.wordpress" in kids and kids["client.services.cat.wordpress"].url == "/products/?category=wordpress"
    assert kids["client.services.cat.wordpress"].divider_before


def test_a_super_admin_hides_renames_and_reorders_customer_menu_entries(boss, owner_user):
    from apps.branding import services as branding
    from apps.core import navigation

    branding.save_menu(boss, {"client.affiliates": {"label": "", "visible": False, "order": None},
                              "client.support": {"label": "Help desk", "visible": True, "order": 5},
                              "client.services.cat.vps": {"label": "", "visible": False, "order": None},
                              "client.home": {"label": "", "visible": False, "order": None}})
    menus = navigation.build(request_for(owner_user), "client")
    names = [e.label for e in menus["main"]]
    assert "Affiliates" not in names and "Help desk" in names and names[0] == "Overview"  # the way home is never hidden
    assert names.index("Help desk") < names.index("Services")  # order 5 comes before the others
    services = next(e for e in menus["main"] if e.key == "client.services")
    assert "client.services.cat.vps" not in [c.key for c in services.children]
    branding.reset_menu(boss)
    assert "Affiliates" in [e.label for e in navigation.build(request_for(owner_user), "client")["main"]]


def test_only_people_who_manage_settings_change_the_customer_menu(boss, manager, owner_user):
    url = reverse("brand_staff:menu")
    assert browser(boss).get(url).status_code == 200 and b"Customer menu" in browser(boss).get(url).content
    assert browser(manager).post(url, {"do": "reset"}).status_code == 403
    saved = browser(boss).post(url, {"do": "save", "visible_client.home": "on", "label_client.support": "Help desk",
                                     "visible_client.support": "on", "order_client.support": "5"})
    assert saved.status_code == 302
    from apps.branding.models import MenuSetting

    assert MenuSetting.objects.filter(key="client.support", label="Help desk").exists()
    assert MenuSetting.objects.get(key="client.orders").is_visible is False  # unticked in the form = hidden
    assert browser(owner_user).get("/account/").status_code in (200, 302)
    assert browser().get(url).status_code == 302
