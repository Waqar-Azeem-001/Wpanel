"""
Owner requests (2026-09-26): a Super Admin sets/resets a client's password and deletes a client; USD/PKR/SAR on clients and
draft documents; emails that are tracked, retried, deletable in bulk; audit-log export and purge; deleting in lists.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core import mail
from django.test import Client as HttpClient
from django.urls import reverse
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken

from apps.accounts import services as account_services
from apps.accounts.models import User
from apps.accounts.roles import Role
from apps.audit import services as audit
from apps.audit.models import AuditEvent
from apps.billing import invoicing
from apps.billing.models import Invoice, Quote
from apps.clients import services as client_services
from apps.clients.models import Client
from apps.core import currencies
from apps.core.exceptions import ServiceError
from apps.notifications import services as notifications
from apps.notifications.models import EmailEvent, EmailLink, EmailMessage, Notification

pytestmark = pytest.mark.django_db

NEW_PASSWORD = "Fresh-Passw0rd!2026"


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


@pytest.fixture
def acme(manager):
    return client_services.create_client(manager, {"first_name": "Ada", "last_name": "Lovelace", "email": "ada@acme.test",
                                                   "company_name": "Acme Ltd", "country": "PK"})


@pytest.fixture
def owner(acme):
    return acme.contacts.get().user


def lines(price="100.00"):
    return [{"description": "Hosting", "quantity": 1, "unit_price": price}]


def denied(exc):
    return exc.value.status_code == 403


# --- A Super Admin sets and resets passwords -----------------------------------------------------------------------------

def test_super_admin_sets_a_password_and_the_person_is_signed_out(boss, owner):
    from rest_framework_simplejwt.tokens import RefreshToken

    RefreshToken.for_user(owner)
    mail.outbox.clear()
    account_services.admin_set_password(boss, owner, NEW_PASSWORD)
    owner.refresh_from_db()
    assert owner.check_password(NEW_PASSWORD)
    assert BlacklistedToken.objects.filter(token__user=owner).exists()
    event = AuditEvent.objects.get(action="account.password_set_by_admin")
    assert event.actor == boss and event.target_id == str(owner.pk) and NEW_PASSWORD not in str(event.metadata)
    assert len(mail.outbox) == 1 and NEW_PASSWORD not in mail.outbox[0].body and "changed" in mail.outbox[0].subject
    message = EmailMessage.objects.get(event="account.password_changed_by_admin")
    assert NEW_PASSWORD not in message.body_text and not message.is_sensitive


def test_a_weak_password_is_refused_and_nothing_changes(boss, owner):
    before = owner.password
    with pytest.raises(ServiceError) as exc:
        account_services.admin_set_password(boss, owner, "12345678")
    assert exc.value.code == "weak_password"
    owner.refresh_from_db()
    assert owner.password == before


@pytest.mark.parametrize("role", [Role.ADMIN, Role.MANAGER, Role.SUPPORT_AGENT, Role.TECHNICAL])
def test_only_a_super_admin_can_set_a_password(make_user, owner, role):
    with pytest.raises(ServiceError) as exc:
        account_services.admin_set_password(make_user(f"p-{role}@example.com", role=role), owner, NEW_PASSWORD)
    assert denied(exc)
    owner.refresh_from_db()
    assert not owner.check_password(NEW_PASSWORD)


def test_a_super_admin_cannot_set_their_own_or_another_super_admins_password(boss, make_user):
    other = make_user("other-boss@example.com", role=Role.SUPER_ADMIN)
    with pytest.raises(ServiceError):
        account_services.admin_set_password(boss, boss, NEW_PASSWORD)
    with pytest.raises(ServiceError) as exc:
        account_services.admin_set_password(boss, other, NEW_PASSWORD)
    assert denied(exc)


def test_reset_link_is_sent_by_staff_who_manage_the_client_and_never_to_an_inactive_account(manager, admin, owner, staff):
    mail.outbox.clear()
    account_services.admin_send_reset_link(manager, owner)
    assert len(mail.outbox) == 1 and "/password/reset/" in mail.outbox[0].body or "reset" in mail.outbox[0].body.lower()
    assert AuditEvent.objects.filter(action="account.reset_link_sent", target_id=str(owner.pk)).exists()
    with pytest.raises(ServiceError) as exc:
        account_services.admin_send_reset_link(staff(Role.SUPPORT_AGENT), owner)
    assert denied(exc)
    owner.status = "suspended"
    owner.save()
    with pytest.raises(ServiceError) as exc:
        account_services.admin_send_reset_link(admin, owner)
    assert exc.value.code == "account_inactive"


def test_the_users_page_offers_set_password_only_to_a_super_admin(boss, admin, owner):
    url = reverse("console:user_detail", args=[owner.pk])
    as_boss = browser(boss).get(url).content.decode()
    assert reverse("console:user_set_password", args=[owner.pk]) in as_boss and "Send reset link" in as_boss
    as_admin = browser(admin).get(url).content.decode()
    assert reverse("console:user_set_password", args=[owner.pk]) not in as_admin and "Send reset link" in as_admin


def test_setting_a_password_from_the_page_and_a_direct_post_by_an_admin(boss, admin, owner):
    url = reverse("console:user_set_password", args=[owner.pk])
    body = {"new_password": NEW_PASSWORD, "new_password_confirm": NEW_PASSWORD}
    assert browser(admin).post(url, body).status_code in (200, 302)
    owner.refresh_from_db()
    assert not owner.check_password(NEW_PASSWORD)  # an admin's direct POST changes nothing
    response = browser(boss).post(url, body)
    assert response.status_code == 302
    owner.refresh_from_db()
    assert owner.check_password(NEW_PASSWORD)
    mismatch = browser(boss).post(url, {"new_password": NEW_PASSWORD, "new_password_confirm": "x"})
    assert mismatch.status_code == 200 and b"do not match" in mismatch.content
    assert browser().post(url, body).status_code == 302  # anonymous: sent to sign in
    assert browser(owner).post(url, body).status_code == 403


def test_the_client_contacts_tab_has_the_password_actions(boss, manager, acme, owner):
    url = reverse("clients_staff:contact_add", args=[acme.pk])
    page = browser(boss).get(url).content.decode()
    assert "Send reset link" in page and "Set password" in page
    assert "Set password" not in browser(manager).get(url).content.decode()
    mail.outbox.clear()
    sent = browser(manager).post(reverse("clients_staff:contact_reset_link", args=[acme.pk, acme.contacts.get().pk]))
    assert sent.status_code == 302 and len(mail.outbox) == 1


# --- Deleting a client -----------------------------------------------------------------------------------------------------

def test_a_client_with_no_history_can_be_deleted_with_its_drafts_and_sign_ins(boss, manager, acme, owner):
    draft = invoicing.create_invoice(manager, acme, lines=lines())
    quote = invoicing.create_quote(manager, acme, lines=lines())
    result = client_services.delete_client(boss, acme, confirm_email="ADA@acme.test ")
    assert not Client.objects.filter(pk=acme.pk).exists()
    assert not Invoice.objects.filter(pk=draft.pk).exists() and not Quote.objects.filter(pk=quote.pk).exists()
    assert not User.objects.filter(pk=owner.pk).exists() and result["logins_deleted"] == [owner.email]
    event = AuditEvent.objects.get(action="client.deleted")
    assert event.actor == boss and event.metadata["email"] == "ada@acme.test"


def test_the_typed_email_must_match_and_only_a_super_admin_may_delete(boss, admin, manager, acme):
    with pytest.raises(ServiceError) as exc:
        client_services.delete_client(boss, acme, confirm_email="someone@else.test")
    assert exc.value.code == "confirmation_mismatch"
    for actor in (admin, manager):
        with pytest.raises(ServiceError) as exc:
            client_services.delete_client(actor, acme, confirm_email=acme.email)
        assert denied(exc)
    assert Client.objects.filter(pk=acme.pk).exists()


def test_a_client_with_records_cannot_be_deleted_and_the_reason_is_listed(boss, manager, acme):
    invoicing.issue_invoice(manager, invoicing.create_invoice(manager, acme, lines=lines()))
    assert client_services.delete_blockers(acme) == [(1, "issued invoices")]
    with pytest.raises(ServiceError) as exc:
        client_services.delete_client(boss, acme, confirm_email=acme.email)
    assert exc.value.code == "client_has_history" and "1 issued invoices" in exc.value.message
    assert Client.objects.filter(pk=acme.pk).exists() and Invoice.objects.filter(client=acme).exists()


def test_sign_ins_shared_with_another_client_survive_a_deletion(boss, manager, acme, owner):
    other = client_services.create_client(manager, {"first_name": "Bo", "email": "bo@other.test"})
    client_services.add_contact(manager, other, email=owner.email, role="billing")
    result = client_services.delete_client(boss, acme, confirm_email=acme.email)
    assert User.objects.filter(pk=owner.pk).exists() and result["logins_deleted"] == []


def test_client_delete_pages_and_direct_urls(boss, admin, manager, acme):
    url = reverse("clients_staff:delete", args=[acme.pk])
    assert browser(admin).get(url).status_code == 403 and browser(manager).post(url, {}).status_code == 403
    assert browser().get(url).status_code == 302
    page = browser(boss).get(url)
    assert page.status_code == 200 and b"Delete permanently" in page.content
    detail = reverse("clients_staff:detail", args=[acme.pk])
    assert url in browser(boss).get(detail).content.decode() and url not in browser(admin).get(detail).content.decode()
    wrong = browser(boss).post(url, {"confirm_email": "nope@x.test"})
    assert wrong.status_code == 200 and b"exactly" in wrong.content and Client.objects.filter(pk=acme.pk).exists()
    done = browser(boss).post(url, {"confirm_email": acme.email, "delete_logins": "on"})
    assert done.status_code == 302 and not Client.objects.filter(pk=acme.pk).exists()


def test_a_client_with_history_shows_why_instead_of_the_form(boss, manager, acme):
    invoicing.issue_invoice(manager, invoicing.create_invoice(manager, acme, lines=lines()))
    page = browser(boss).get(reverse("clients_staff:delete", args=[acme.pk]))
    assert b"cannot be deleted" in page.content and b"issued invoices" in page.content and b"confirm_email" not in page.content


def test_bulk_client_actions_and_bulk_delete_is_super_admin_only(boss, admin, manager, acme):
    spare = client_services.create_client(manager, {"first_name": "Zed", "email": "zed@spare.test"})
    invoicing.issue_invoice(manager, invoicing.create_invoice(manager, acme, lines=lines()))
    bulk = reverse("clients_staff:bulk")
    browser(admin).post(bulk, {"do": "delete", "ids": [spare.pk]})
    assert Client.objects.filter(pk=spare.pk).exists()  # an admin cannot delete
    browser(admin).post(bulk, {"do": "deactivate", "ids": [spare.pk]})
    spare.refresh_from_db()
    assert spare.status == "inactive"
    response = browser(boss).post(bulk, {"do": "delete", "ids": [spare.pk, acme.pk]}, follow=True)
    assert not Client.objects.filter(pk=spare.pk).exists() and Client.objects.filter(pk=acme.pk).exists()
    assert b"could not be changed" in response.content
    listing = browser(boss).get(reverse("clients_staff:list")).content.decode()
    assert "Delete (no history only)" in listing
    assert "Delete (no history only)" not in browser(admin).get(reverse("clients_staff:list")).content.decode()


# --- USD, PKR and SAR ------------------------------------------------------------------------------------------------------

def test_the_currencies_offered_are_usd_pkr_sar():
    assert currencies.CODES == ("USD", "PKR", "SAR")
    assert [c for c, _ in currencies.choices("EUR")][0] == "EUR"  # an old value stays selectable


def test_a_client_can_be_set_to_pkr_or_sar_but_not_to_other_codes(manager, acme):
    client_services.update_client(manager, acme, {"currency": "sar"})
    acme.refresh_from_db()
    assert acme.currency == "SAR"
    with pytest.raises(ServiceError) as exc:
        client_services.update_client(manager, acme, {"currency": "GBP"})
    assert exc.value.code == "invalid_currency"
    with pytest.raises(ServiceError):
        client_services.create_client(manager, {"first_name": "X", "email": "x@y.test", "currency": "EUR"})
    Client.objects.filter(pk=acme.pk).update(currency="GBP")  # old data keeps working when left alone
    acme.refresh_from_db()
    client_services.update_client(manager, acme, {"currency": "GBP", "notes": "still fine"})


def test_a_draft_invoice_takes_the_chosen_currency_and_can_change_until_it_is_issued(manager, acme):
    assert acme.currency == "USD"
    invoice = invoicing.create_invoice(manager, acme, lines=lines("5000"))
    assert invoice.currency == "USD"
    other = invoicing.create_invoice(manager, acme, lines=lines("5000"), currency="PKR")
    assert other.currency == "PKR"
    invoicing.update_invoice(manager, other, lines=lines("5000"), currency="SAR")
    other.refresh_from_db()
    assert other.currency == "SAR" and Decimal(other.subtotal) == Decimal("5000.00")  # never converted
    updated = AuditEvent.objects.filter(action="invoice.updated").latest("id")
    assert updated.metadata["currency"] == "SAR" and updated.metadata["currency_from"] == "PKR"
    with pytest.raises(ServiceError) as exc:
        invoicing.update_invoice(manager, other, lines=lines(), currency="EUR")
    assert exc.value.code == "invalid_currency"
    invoicing.issue_invoice(manager, other)
    with pytest.raises(ServiceError):
        invoicing.update_invoice(manager, other, lines=lines(), currency="USD")
    other.refresh_from_db()
    assert other.currency == "SAR"  # an issued invoice keeps its currency


def test_a_quote_takes_a_currency_too(manager, acme):
    quote = invoicing.create_quote(manager, acme, lines=lines(), currency="PKR")
    assert quote.currency == "PKR"
    invoicing.update_quote(manager, quote, lines=lines(), currency="SAR")
    quote.refresh_from_db()
    assert quote.currency == "SAR"


def test_the_invoice_page_lets_staff_pick_the_currency_before_sharing(manager, acme):
    def data(**extra):
        return {"lines-TOTAL_FORMS": "2", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "100",
                "lines-0-description": "Hosting", "lines-0-quantity": "1", "lines-0-unit_price": "9000", "lines-0-taxable": "on",
                "lines-1-quantity": "1", "discount_type": "", "notes": "", **extra}

    page = browser(manager).get(f"{reverse('billing_staff:invoice_create')}?client={acme.pk}").content.decode()
    assert 'name="currency"' in page and "Pakistani Rupee" in page and "Saudi Riyal" in page and "data-currency-code" in page
    saved = browser(manager).post(f"{reverse('billing_staff:invoice_create')}?client={acme.pk}", data(currency="PKR"))
    assert saved.status_code == 302
    invoice = Invoice.objects.get(client=acme)
    assert invoice.currency == "PKR"
    detail = browser(manager).get(reverse("billing_staff:invoice_detail", args=[invoice.pk])).content.decode()
    assert "PKR" in detail
    edited = browser(manager).post(reverse("billing_staff:invoice_edit", args=[invoice.pk]),
                                   {**data(currency="SAR"), "lines-INITIAL_FORMS": "1"})
    invoice.refresh_from_db()
    assert edited.status_code == 302 and invoice.currency == "SAR"
    blank = browser(manager).post(f"{reverse('billing_staff:invoice_create')}?client={acme.pk}", data())
    assert blank.status_code == 302 and Invoice.objects.filter(client=acme).latest("id").currency == "USD"  # blank = client default
    bad = browser(manager).post(f"{reverse('billing_staff:invoice_create')}?client={acme.pk}", data(currency="EUR"))
    assert bad.status_code == 200


# --- Email: tracking, retries, log management ---------------------------------------------------------------------------

def make_email(**extra):
    return notifications.send_email(to_email="who@example.test", template="invoice_issued", event="invoice.issued", context={
        "user": None, "invoice": type("I", (), {"number": "INV-1", "total": "10", "currency": "USD", "due_date": None,
                                                  "reference": "INV-1", "client": None})(), "link": "https://portal.test/x/"},
                                    **extra)


def plain_email(html="<p>Hi <a href=\"https://portal.test/pay/1/\">pay</a></p>", sensitive=False):
    message = EmailMessage.objects.create(to_email="who@example.test", subject="Hello", template="x", body_text="Hi",
                                          body_html=html, is_sensitive=sensitive)
    return message


def test_a_sent_email_has_a_timeline_a_message_id_and_says_when_nothing_really_left(settings):
    message = notifications.send_email(to_email="who@example.test", template="client_welcome", event="client.welcome",
                                       context={"user": None, "client": None, "link": "https://portal.test/reset/"})
    message.refresh_from_db()
    assert message.status == "sent" and message.message_id.startswith("<") and message.last_attempt_at
    assert message.delivery == "fallback"  # no provider is set up in the tests
    kinds = list(message.events.values_list("kind", flat=True))
    assert kinds == ["queued", "sent"]
    assert "did not leave this machine" in message.events.last().detail


def test_links_in_a_normal_email_are_counted_and_sent_on_to_the_original_address(client, settings):
    settings.EMAIL_CLICK_TRACKING = True
    message = notifications.send_email(to_email="who@example.test", template="invoice_issued", event="invoice.issued",
                                       context={"user": None, "invoice": type("I", (), {
                                           "number": "INV-1", "total": "10", "currency": "USD", "due_date": None,
                                           "reference": "INV-1", "client": None})(), "link": "https://portal.test/inv/9/"})
    message.refresh_from_db()
    links = list(message.links.all())
    assert links and all(f"/e/c/{message.track_token}/{link.pk}/" in message.body_html for link in links)
    assert "https://portal.test/inv/9/" in [link.url for link in links] or any("portal.test" in l.url for l in links)
    target = links[0]
    first = client.get(reverse("email_click", args=[message.track_token, target.pk]))
    assert first.status_code == 302 and first["Location"] == target.url
    client.get(reverse("email_click", args=[message.track_token, target.pk]))
    target.refresh_from_db()
    message.refresh_from_db()
    assert target.click_count == 2 and message.click_count == 2 and message.first_clicked_at
    assert message.events.filter(kind="clicked").count() == 1


def test_a_click_link_only_ever_goes_where_the_email_said(client):
    message = plain_email()
    other = plain_email()
    link = EmailLink.objects.create(message=other, url="https://portal.test/somewhere/")
    wrong_message = client.get(reverse("email_click", args=[message.track_token, link.pk]) + "?to=https://evil.test/")
    assert wrong_message.status_code == 302 and wrong_message["Location"] == "/"
    unknown = client.get(reverse("email_click", args=[message.track_token, 99999]) + "?url=https://evil.test/")
    assert unknown["Location"] == "/"
    assert not EmailLink.objects.filter(click_count__gt=0).exists()


def test_security_emails_and_switched_off_tracking_leave_links_alone(settings):
    sensitive = notifications.send_email(to_email="who@example.test", template="password_reset", event="account.password_reset",
                                         context={"user": None, "link": "https://portal.test/reset/abc/"})
    assert sensitive.links.count() == 0
    settings.EMAIL_CLICK_TRACKING = False
    message = notifications.send_email(to_email="who@example.test", template="client_welcome", event="account.password_changed_by_admin",
                                       context={"user": None, "client": None, "link": "https://portal.test/x/"})
    assert message.links.count() == 0


def test_the_open_pixel_adds_one_timeline_step(client):
    message = plain_email()
    for _ in range(3):
        client.get(reverse("email_open_pixel", args=[message.track_token]))
    message.refresh_from_db()
    assert message.open_count == 3 and message.events.filter(kind="opened").count() == 1


class Boom(Exception):
    pass


@pytest.fixture
def failing_mail(monkeypatch):
    from django.core.mail import EmailMultiAlternatives

    state = {"fail": True}
    real = EmailMultiAlternatives.send

    def send(self, fail_silently=False):
        if state["fail"]:
            raise TimeoutError("timed out")
        return real(self, fail_silently)

    monkeypatch.setattr(EmailMultiAlternatives, "send", send)
    return state


def test_without_a_worker_a_failing_mail_server_does_not_break_the_page(settings, failing_mail):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = False  # what local development uses
    message = notifications.send_email(to_email="who@example.test", template="client_welcome", event="client.welcome",
                                       context={"user": None, "client": None, "link": "https://x.test/"})
    message.refresh_from_db()
    assert message.status == "failed" and "TimeoutError" in message.last_error
    assert list(message.events.values_list("kind", flat=True)) == ["queued", "failed"]
    failing_mail["fail"] = False
    notifications.deliver(message.pk)
    message.refresh_from_db()
    assert message.status == "sent" and message.attempts == 2


def test_the_sweeper_retries_stale_failures_only_and_stops_after_eight_tries(settings, failing_mail):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = False
    stale = plain_email()
    fresh = plain_email()
    tired = plain_email()
    EmailMessage.objects.filter(pk__in=[stale.pk, fresh.pk, tired.pk]).update(status="failed", attempts=1)
    EmailMessage.objects.filter(pk=tired.pk).update(attempts=notifications.SWEEP_MAX_ATTEMPTS)
    old = timezone.now() - timedelta(minutes=30)
    EmailMessage.objects.filter(pk__in=[stale.pk, tired.pk]).update(updated_at=old)
    failing_mail["fail"] = False
    assert notifications.sweep_stuck() == 1
    stale.refresh_from_db()
    fresh.refresh_from_db()
    tired.refresh_from_db()
    assert (stale.status, fresh.status, tired.status) == ("sent", "failed", "failed")
    assert stale.events.filter(kind="resent", detail="Automatic retry").exists()


def test_deleting_an_email_needs_the_settings_permission_and_refuses_a_waiting_one(admin, manager, staff):
    sent = plain_email()
    EmailMessage.objects.filter(pk=sent.pk).update(status="sent")
    waiting = plain_email()
    EmailMessage.objects.filter(pk=waiting.pk).update(status="queued")
    with pytest.raises(ServiceError) as exc:
        notifications.delete_email(staff(Role.SUPPORT_AGENT), sent)
    assert denied(exc)
    with pytest.raises(ServiceError) as exc:
        notifications.delete_email(admin, waiting)
    assert exc.value.code == "email_queued"
    notifications.delete_email(admin, sent)
    assert not EmailMessage.objects.filter(pk=sent.pk).exists() and EmailMessage.objects.filter(pk=waiting.pk).exists()
    assert AuditEvent.objects.filter(action="email.deleted", actor=admin).exists()


def test_clearing_old_emails_keeps_recent_and_waiting_ones_and_is_audited(admin, staff):
    old_sent, old_failed, old_waiting, recent = (plain_email() for _ in range(4))
    EmailMessage.objects.filter(pk=old_sent.pk).update(status="sent")
    EmailMessage.objects.filter(pk=old_failed.pk).update(status="failed")
    EmailMessage.objects.filter(pk=old_waiting.pk).update(status="queued")
    EmailMessage.objects.filter(pk=recent.pk).update(status="sent")
    long_ago = timezone.now() - timedelta(days=100)
    EmailMessage.objects.filter(pk__in=[old_sent.pk, old_failed.pk, old_waiting.pk]).update(created_at=long_ago)
    with pytest.raises(ServiceError) as exc:
        notifications.purge_emails(admin, older_than_days=3)
    assert exc.value.code == "too_recent"
    with pytest.raises(ServiceError) as exc:
        notifications.purge_emails(staff(Role.MANAGER), older_than_days=90)
    assert denied(exc)
    assert notifications.purge_emails(admin, older_than_days=90, status="failed") == 1
    assert notifications.purge_emails(admin, older_than_days=90) == 1
    left = set(EmailMessage.objects.values_list("pk", flat=True))
    assert left == {old_waiting.pk, recent.pk}
    assert AuditEvent.objects.filter(action="email.purged").count() == 2


def test_the_email_log_pages_bulk_actions_and_warnings(admin, manager, failing_mail, settings):
    settings.CELERY_TASK_EAGER_PROPAGATES = False
    failed = [plain_email() for _ in range(2)]
    EmailMessage.objects.filter(pk__in=[m.pk for m in failed]).update(status="failed", last_error="TimeoutError: timed out")
    log = reverse("notifications_staff:emails")
    page = browser(admin).get(log).content.decode()
    assert "No email provider is active" in page and "Send all failed again" in page and "With selected" in page
    assert "With selected" not in browser(manager).get(log).content.decode()  # view-only staff cannot manage
    failing_mail["fail"] = False
    retry = browser(admin).post(reverse("notifications_staff:retry_failed"), follow=True)
    assert set(EmailMessage.objects.filter(pk__in=[m.pk for m in failed]).values_list("status", flat=True)) == {"sent"}
    assert b"queued to send again" in retry.content
    ids = [m.pk for m in failed]
    assert browser(manager).post(reverse("notifications_staff:bulk"), {"do": "delete", "ids": ids}).status_code == 403
    done = browser(admin).post(reverse("notifications_staff:bulk"), {"do": "delete", "ids": ids}, follow=True)
    assert b"deleted" in done.content and not EmailMessage.objects.filter(pk__in=ids).exists()
    nothing = browser(admin).post(reverse("notifications_staff:bulk"), {"do": "delete"}, follow=True)
    assert b"Tick at least one" in nothing.content


def test_the_email_detail_shows_the_timeline_links_and_delete(admin):
    message = notifications.send_email(to_email="who@example.test", template="invoice_issued", event="invoice.issued", context={
        "user": None, "invoice": type("I", (), {"number": "INV-1", "total": "10", "currency": "USD", "due_date": None,
                                                  "reference": "INV-1", "client": None})(), "link": "https://portal.test/inv/9/"})
    page = browser(admin).get(reverse("notifications_staff:email", args=[message.pk])).content.decode()
    assert "Timeline" in page and "Handed to the mail server" in page and "Links in this email" in page
    assert "did not leave this machine" in page and reverse("notifications_staff:delete", args=[message.pk]) in page
    stats = browser(admin).get(reverse("notifications_staff:overview")).content.decode()
    assert "Click rate" in stats and "No email provider is active" in stats


def test_a_person_can_delete_only_their_own_notifications(owner, boss):
    mine = [Notification.objects.create(user=owner, event="x", title=f"Mine {i}") for i in range(3)]
    theirs = Notification.objects.create(user=boss, event="x", title="Theirs")
    Notification.objects.filter(pk=mine[0].pk).update(read_at=timezone.now())
    url = reverse("notifications:delete")
    browser(owner).post(url, {"ids": [mine[1].pk, theirs.pk]})
    assert not Notification.objects.filter(pk=mine[1].pk).exists() and Notification.objects.filter(pk=theirs.pk).exists()
    browser(owner).post(url, {"clear": "read"})
    assert list(Notification.objects.filter(user=owner).values_list("pk", flat=True)) == [mine[2].pk]
    assert browser().post(url, {"clear": "read"}).status_code == 302
    page = browser(owner).get(reverse("notifications:inbox")).content.decode()
    assert "Delete selected" in page and "Clear read" in page


# --- Drafts and the audit log -------------------------------------------------------------------------------------------------

def test_draft_documents_can_be_deleted_in_bulk_but_issued_ones_cannot(manager, acme):
    drafts = [invoicing.create_invoice(manager, acme, lines=lines()) for _ in range(2)]
    issued_one = invoicing.issue_invoice(manager, invoicing.create_invoice(manager, acme, lines=lines()))
    response = browser(manager).post(reverse("billing_staff:invoice_bulk"),
                                     {"do": "delete", "ids": [d.pk for d in drafts] + [issued_one.pk]}, follow=True)
    assert not Invoice.objects.filter(pk__in=[d.pk for d in drafts]).exists() and Invoice.objects.filter(pk=issued_one.pk).exists()
    assert b"draft invoice(s) deleted" in response.content and b"could not be changed" in response.content
    quotes = [invoicing.create_quote(manager, acme, lines=lines()) for _ in range(2)]
    sent = invoicing.send_quote(manager, invoicing.create_quote(manager, acme, lines=lines()))
    browser(manager).post(reverse("billing_staff:quote_bulk"), {"do": "delete", "ids": [q.pk for q in quotes] + [sent.pk]})
    assert not Quote.objects.filter(pk__in=[q.pk for q in quotes]).exists() and Quote.objects.filter(pk=sent.pk).exists()
    one = invoicing.create_quote(manager, acme, lines=lines())
    assert browser(manager).post(reverse("billing_staff:quote_delete", args=[one.pk])).status_code == 302
    assert not Quote.objects.filter(pk=one.pk).exists()
    assert browser(manager).get(reverse("billing_staff:quote_list")).status_code == 200


def test_only_a_super_admin_can_clear_the_audit_log_and_the_clearing_is_recorded(boss, admin):
    old = AuditEvent.objects.create(action="email.sent_test", actor_repr="x")
    older = AuditEvent.objects.create(action="client.created", actor_repr="x")
    recent = AuditEvent.objects.create(action="email.recent_test", actor_repr="x")
    long_ago = timezone.now() - timedelta(days=400)
    AuditEvent.objects.filter(pk__in=[old.pk, older.pk]).update(created_at=long_ago)
    with pytest.raises(ServiceError) as exc:
        audit.purge(admin, older_than_days=365)
    assert denied(exc)
    with pytest.raises(ServiceError) as exc:
        audit.purge(boss, older_than_days=30)
    assert exc.value.code == "too_recent"
    assert audit.purge(boss, older_than_days=365, area="email") == 1
    assert AuditEvent.objects.filter(pk=older.pk).exists() and AuditEvent.objects.filter(pk=recent.pk).exists()
    assert audit.purge(boss, older_than_days=365) == 1
    record = AuditEvent.objects.filter(action="audit.purged").order_by("id")
    assert record.count() == 2 and record.last().actor == boss and record.last().metadata["entries"] == 1


def test_the_audit_page_offers_export_to_readers_and_clearing_to_a_super_admin_only(boss, admin):
    log = reverse("console:audit_log")
    assert reverse("console:audit_purge") in browser(boss).get(log).content.decode()
    as_admin = browser(admin).get(log).content.decode()
    assert reverse("console:audit_purge") not in as_admin and reverse("console:audit_export") in as_admin
    export = browser(admin).get(reverse("console:audit_export") + "?action=auth")
    assert export.status_code == 200 and export["Content-Type"].startswith("text/csv") and b"When,Who,Action" in export.content
    before = AuditEvent.objects.count()
    AuditEvent.objects.filter().update(created_at=timezone.now() - timedelta(days=500))
    browser(admin).post(reverse("console:audit_purge"), {"days": "365"})
    assert AuditEvent.objects.count() >= before  # an admin's direct POST deletes nothing
    browser(boss).post(reverse("console:audit_purge"), {"days": "365"})
    assert AuditEvent.objects.filter(action="audit.purged").count() == 1
    assert browser().post(reverse("console:audit_purge"), {"days": "365"}).status_code == 302
