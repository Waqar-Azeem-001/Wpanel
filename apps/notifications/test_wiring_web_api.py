"""Phase 11: the business events reach the right people; payment reminders; the inbox, preferences and email-log pages; the API."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts.roles import Role
from apps.billing import invoicing, payments, reminders
from apps.billing import services as billing
from apps.billing.models import BillingSettings, Invoice, InvoiceReminder
from apps.clients import services as client_services
from apps.notifications import services, tasks
from apps.notifications.models import EmailMessage, Notification, NotificationPreference

pytestmark = pytest.mark.django_db

D = Decimal


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def admin(staff):
    return staff(Role.ADMIN)


@pytest.fixture
def client_obj(manager):
    return client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test",
                                                   "company_name": "Acme Ltd", "country": "US"})


@pytest.fixture
def owner(client_obj):
    return client_obj.contacts.get(role="owner").user


@pytest.fixture
def bank(manager):
    return billing.save_payment_method(manager, "bank-transfer", name="Bank transfer")


def issued(manager, client_obj, price="100.00"):
    return invoicing.issue_invoice(manager, invoicing.create_invoice(
        manager, client_obj, lines=[{"description": "Hosting", "quantity": 1, "unit_price": price}]))


def reload(obj):
    obj.refresh_from_db()
    return obj


def emails_to(address):
    return [m for m in mail.outbox if address in m.to]


# --- Business events reach the right people -----------------------------------------------------------------------

def test_a_reported_payment_alerts_the_billing_team_not_the_customer(manager, admin, staff, owner, client_obj, bank):
    invoice = issued(manager, client_obj)
    mail.outbox.clear()
    payments.report_payment(owner, invoice, method=bank, reference="TT-1")
    alerted = set(Notification.objects.filter(event="payment.reported").values_list("user__email", flat=True))
    assert manager.email in alerted and admin.email in alerted and owner.email not in alerted
    assert staff(Role.SUPPORT_AGENT).email not in alerted  # agents cannot manage billing
    assert mail.outbox == []  # a team alert is in-app; no email by default


def test_rejecting_and_refunding_tell_the_customer(manager, owner, client_obj, bank):
    invoice = issued(manager, client_obj)
    tx = payments.report_payment(owner, invoice, method=bank)
    mail.outbox.clear()
    payments.reject_payment(manager, tx, reason="Not received")
    assert [m.subject for m in emails_to(client_obj.email)] == [f"We could not find your payment for invoice {invoice.number} - Wpanel"]
    assert "Not received" in mail.outbox[0].body
    paid = payments.record_payment(manager, invoice, amount="100.00")
    mail.outbox.clear()
    payments.refund_payment(manager, paid, amount="40.00")
    assert "refunded USD 40.00" in mail.outbox[0].body and invoice.number in mail.outbox[0].subject


def test_a_failed_gateway_payment_tells_the_customer(manager, owner, client_obj, bank):
    from apps.billing.models import PaymentProvider
    from apps.billing.tests.conftest import signed_event

    provider = PaymentProvider(name="Test", kind="test", is_active=True)
    provider.set_webhook_secret("whsec_x")
    provider.save()
    method = billing.save_payment_method(manager, "card", name="Card")
    method.provider = provider
    method.save()
    invoice = issued(manager, client_obj)
    tx, _ = payments.start_gateway_payment(owner, invoice, method)
    mail.outbox.clear()
    payments.handle_webhook(provider, *signed_event(provider, "e1", "payment.failed", tx.external_id, "100.00"))
    assert len(mail.outbox) == 1 and "did not go through" in mail.outbox[0].subject


def test_new_tickets_alert_the_team_unless_someone_already_owns_them(manager, admin, staff, owner, client_obj):
    from apps.support import services as support
    from apps.support.models import Department

    agent = staff(Role.SUPPORT_AGENT)
    technical = Department.objects.get(slug="technical")
    support.open_ticket(owner, client_obj, department=technical, subject="Nobody owns this", body="help")
    unowned = Notification.objects.filter(event="ticket.unassigned")
    assert {n.user for n in unowned} == {agent, manager, admin}
    Notification.objects.all().delete()
    support.save_department(agent, technical, name=technical.name, default_assignee=agent)
    support.open_ticket(owner, client_obj, department=technical, subject="Owned", body="help")
    assert not Notification.objects.filter(event="ticket.unassigned").exists()
    assert {n.user for n in Notification.objects.filter(event="ticket.assigned")} == {agent}


def test_a_customer_can_turn_off_ticket_reply_emails_but_not_invoice_emails(manager, staff, owner, client_obj):
    from apps.support import services as support
    from apps.support.models import Department

    agent = staff(Role.SUPPORT_AGENT)
    ticket = support.open_ticket(owner, client_obj, department=Department.objects.get(slug="technical"),
                                 subject="s", body="b")
    services.save_preferences(owner, {"support": (False, True)})
    mail.outbox.clear()
    support.reply(agent, ticket, "An answer.")
    assert emails_to(owner.email) == []  # no reply email...
    assert Notification.objects.filter(user=owner, event="ticket.reply").exists()  # ...but still in the inbox
    issued(manager, client_obj)
    assert len(emails_to(client_obj.email)) == 1  # invoices always arrive


def test_orders_cancelled_and_failed_notify_the_right_people(manager, admin, owner, client_obj, monkeypatch):
    from apps.orders import lifecycle
    from apps.orders.models import Order, OrderStatus

    order = Order.objects.create(client=client_obj, currency="USD", subtotal=D("10"), total=D("10"),
                                 status=OrderStatus.PENDING_PAYMENT, billing_name="Ada", billing_email=client_obj.email)
    mail.outbox.clear()
    lifecycle.transition(order, OrderStatus.CANCELLED, reason="Changed my mind")
    assert any("was cancelled" in m.subject for m in emails_to(owner.email))
    assert Notification.objects.filter(user=owner, event="order.cancelled").exists()

    failing = Order.objects.create(client=client_obj, currency="USD", subtotal=D("10"), total=D("10"),
                                   status=OrderStatus.PROVISIONING)
    lifecycle.transition(failing, OrderStatus.FAILED, reason="No server")
    team = Notification.objects.filter(event="order.failed")
    assert {n.user for n in team} >= {manager, admin} and owner not in {n.user for n in team}
    assert "No server" in team.first().body


def test_hosting_suspension_reactivation_and_termination_tell_the_owner(manager, owner, client_obj):
    from apps.hosting import services as hosting
    from apps.products import services as product_services
    from apps.products.models import ProductType

    server = product_services.create_server(manager, {"name": "s", "hostname": "s.example.com"})
    product = product_services.create_product(manager, {"name": "P", "type": ProductType.SHARED_HOSTING,
                                                        "whm_package_name": "p"})
    product_services.set_product_servers(manager, product, [server.pk])
    account = hosting.request_hosting(manager, client_obj, product, "site.com")
    hosting.complete_provisioning(manager, account)
    mail.outbox.clear()
    hosting.suspend_account(manager, account, reason="Abuse report")
    assert "suspended" in mail.outbox[-1].subject and "Abuse report" in mail.outbox[-1].body
    hosting.unsuspend_account(manager, account)
    assert "active again" in mail.outbox[-1].subject
    hosting.terminate_account(manager, account)
    assert "terminated" in mail.outbox[-1].subject
    assert set(Notification.objects.filter(user=owner).values_list("event", flat=True)) >= {
        "hosting.suspended", "hosting.unsuspended", "hosting.terminated"}
    assert all(m.to == [owner.email] for m in mail.outbox[-3:])


def test_registering_a_domain_and_renewing_services_send_confirmations(manager, owner, client_obj):
    from apps.domains import services as domains
    from apps.domains.models import RegistrarProvider

    RegistrarProvider.objects.create(name="T", kind="manual", is_active=True)
    domains.set_tld_pricing(manager, ".com", register_price="12.00", renew_price="14.00", transfer_price="9.00")
    domain = domains.request_registration(manager, client_obj, "example.com", 1)
    mail.outbox.clear()
    domains.complete_registration(manager, domain)
    assert "is now registered to you" in mail.outbox[-1].subject
    from apps.renewals import services as renewals

    change = renewals.create_domain_renewal(owner, domain, 1)
    mail.outbox.clear()
    payments.record_payment(manager, change.invoice, amount=str(change.invoice.total))
    assert any("has been renewed" in m.subject for m in mail.outbox)
    assert Notification.objects.filter(user=owner, event="service.renewed").exists()


def test_quote_decisions_alert_the_billing_team(manager, admin, owner, client_obj):
    quote = invoicing.send_quote(manager, invoicing.create_quote(
        manager, client_obj, lines=[{"description": "Build", "quantity": 1, "unit_price": "50"}]))
    invoicing.accept_quote(owner, quote)
    other = invoicing.send_quote(manager, invoicing.create_quote(
        manager, client_obj, lines=[{"description": "More", "quantity": 1, "unit_price": "10"}]))
    invoicing.decline_quote(owner, other)
    assert {n.event for n in Notification.objects.filter(user=manager)} >= {"quote.accepted", "quote.declined"}
    assert not Notification.objects.filter(user=owner, event__startswith="quote.").exists()


# --- Payment reminders --------------------------------------------------------------------------------------------

def overdue(manager, client_obj, days):
    invoice = issued(manager, client_obj)
    Invoice.objects.filter(pk=invoice.pk).update(due_date=timezone.localdate() - timedelta(days=days))
    return reload(invoice)


@pytest.mark.parametrize("days,kind", [(-4, None), (-3, "due_soon"), (-1, "due_soon"), (0, "due_soon"), (1, "overdue_1"),
                                       (6, "overdue_1"), (7, "overdue_7"), (13, "overdue_7"), (14, "overdue_14"),
                                       (90, "overdue_14")])
def test_which_reminder_is_due(manager, client_obj, days, kind):
    invoice = overdue(manager, client_obj, days)
    assert reminders.due_reminder(invoice, today=timezone.localdate())[0] == kind


def test_reminders_go_out_once_and_only_the_most_advanced(manager, owner, client_obj):
    invoice = overdue(manager, client_obj, 8)  # missed the day-1 reminder: only the day-7 one is sent
    mail.outbox.clear()
    assert reminders.send_invoice_reminders() == {"sent": 1, "skipped": 0}
    assert [r.kind for r in InvoiceReminder.objects.filter(invoice=invoice)] == ["overdue_7"]
    assert len(mail.outbox) == 1 and "8 days overdue" in mail.outbox[0].subject and "overdue" in mail.outbox[0].body
    assert reminders.send_invoice_reminders() == {"sent": 0, "skipped": 1}  # the daily job is safe to run again
    assert len(mail.outbox) == 1
    Invoice.objects.filter(pk=invoice.pk).update(due_date=timezone.localdate() - timedelta(days=15))
    assert reminders.send_invoice_reminders()["sent"] == 1  # the next stage goes out when it is reached
    assert Notification.objects.filter(user=owner, event="invoice.overdue").count() == 2


def test_a_due_soon_reminder_says_when_it_is_due(manager, client_obj):
    invoice = overdue(manager, client_obj, -2)
    mail.outbox.clear()
    reminders.send_invoice_reminders()
    assert "is due on" in mail.outbox[0].subject and invoice.number in mail.outbox[0].subject


def test_nothing_is_sent_for_paid_cancelled_draft_or_settled_invoices(manager, client_obj):
    paid = overdue(manager, client_obj, 20)
    payments.record_payment(manager, paid, amount="100.00")
    cancelled = overdue(manager, client_obj, 20)
    invoicing.cancel_invoice(manager, cancelled)
    invoicing.create_invoice(manager, client_obj, lines=[{"description": "d", "quantity": 1, "unit_price": "5"}])
    mail.outbox.clear()
    assert reminders.send_invoice_reminders() == {"sent": 0, "skipped": 0}
    assert mail.outbox == []


def test_a_part_paid_invoice_is_reminded_of_the_balance(manager, client_obj):
    invoice = overdue(manager, client_obj, 2)
    payments.record_payment(manager, invoice, amount="30.00")
    mail.outbox.clear()
    reminders.send_invoice_reminders()
    assert "USD 70.00" in mail.outbox[0].body


def test_reminders_can_be_switched_off_and_run_from_the_task_and_command(manager, client_obj):
    from io import StringIO

    from django.core.management import call_command

    overdue(manager, client_obj, 3)
    row = BillingSettings.load()
    row.send_payment_reminders = False
    row.save()
    assert reminders.send_invoice_reminders()["disabled"] is True and not InvoiceReminder.objects.exists()
    out = StringIO()
    call_command("send_invoice_reminders", stdout=out)
    assert "switched off" in out.getvalue()
    row.send_payment_reminders = True
    row.save()
    from apps.billing import tasks as billing_tasks

    assert billing_tasks.send_invoice_reminders_task()["sent"] == 1
    out = StringIO()
    call_command("send_invoice_reminders", stdout=out)
    assert "already sent: 1" in out.getvalue()


# --- Inbox and preferences pages ----------------------------------------------------------------------------------

def test_the_inbox_lists_marks_read_and_only_follows_local_links(client, owner, customer):
    a = Notification.objects.create(user=owner, event="order.active", title="Order O1 is active", link="/account/orders/1/")
    evil = Notification.objects.create(user=owner, event="order.active", title="Click me", link="https://evil.example/x")
    Notification.objects.create(user=customer, event="order.active", title="Not yours", link="/x/")
    client.force_login(owner)
    page = client.get("/account/notifications/").content
    assert b"Order O1 is active" in page and b"Not yours" not in page and b"Notifications" in page
    response = client.get(f"/account/notifications/{a.pk}/open/")
    assert response.status_code == 302 and response["Location"] == "/account/orders/1/" and reload(a).is_read
    external = client.get(f"/account/notifications/{evil.pk}/open/")
    assert external["Location"] == "/account/notifications/"  # never sent to another site
    unread = client.get("/account/notifications/?show=unread").content
    assert b"caught up" in unread and b"Click me" not in unread  # opening one marks it read, even with an unsafe link
    assert client.get(f"/account/notifications/{Notification.objects.get(user=customer).pk}/open/").status_code == 404


def test_mark_all_read_and_the_navigation_count(client, owner):
    for i in range(3):
        Notification.objects.create(user=owner, event="order.active", title=f"n{i}")
    client.force_login(owner)
    assert b'class="badge unread">3<' in client.get("/account/notifications/").content
    client.post("/account/notifications/mark-read/")
    assert services.unread_count(owner) == 0
    assert b'class="badge unread"' not in client.get("/account/notifications/").content.replace(b'class="badge unread">New', b"")


def test_the_inbox_requires_login(client):
    assert client.get("/account/notifications/").status_code == 302
    assert client.post("/account/notifications/mark-read/").status_code == 302


def test_the_preferences_page_saves_choices_and_lists_the_essentials(client, owner, admin):
    client.force_login(owner)
    page = client.get("/account/notifications/preferences/").content
    assert b"Support tickets" in page and b"Always sent" in page and b"Team alerts" not in page
    assert b"A new invoice" in page and b"Your hosting account login details" in page
    client.post("/account/notifications/preferences/", {"in_app_support": "on", "email_orders": "on", "in_app_orders": "on"})
    prefs = {p.category: (p.email_enabled, p.in_app_enabled) for p in NotificationPreference.objects.filter(user=owner)}
    assert prefs == {"orders": (True, True), "services": (False, False), "support": (False, True)}
    assert b"checked" in client.get("/account/notifications/preferences/").content
    client.force_login(admin)
    assert b"Team alerts" in client.get("/account/notifications/preferences/").content


# --- Email log and statistics (staff) --------------------------------------------------------------------------

def test_the_email_log_is_for_settings_staff_only(client, owner, manager, admin):
    for user in (owner, manager):
        client.force_login(user)
        for path in ("/staff/notifications/", "/staff/notifications/emails/"):
            assert client.get(path).status_code == 403
    client.force_login(admin)
    assert client.get("/staff/notifications/").status_code == 200


def test_the_log_filters_and_shows_secrets_as_removed(client, admin, manager, client_obj):
    from apps.hosting.services import notifications as hosting_notifications

    hosting_notifications.dispatch("hosting.welcome", email="c@acme.test", context={
        "account": type("A", (), {"domain": "site.com"})(), "username": "u", "password": "PLAINTEXT-PW"})
    issued(manager, client_obj)
    failed = EmailMessage.objects.filter(event="invoice.issued").first()
    EmailMessage.objects.filter(pk=failed.pk).update(status="failed", last_error="smtp down")
    client.force_login(admin)
    listing = client.get("/staff/notifications/emails/?status=failed").content
    assert failed.subject.encode() in listing and b"c@acme.test" not in listing  # the filter kept only the failed one
    assert client.get("/staff/notifications/emails/?event=hosting.welcome").content.count(b"Secret") >= 1
    assert b"c@acme.test" in client.get("/staff/notifications/emails/?q=acme.test").content
    secret = EmailMessage.objects.get(event="hosting.welcome")
    detail = client.get(f"/staff/notifications/emails/{secret.pk}/").content
    assert b"PLAINTEXT-PW" not in detail and b"removed" in detail
    page = client.get("/staff/notifications/").content
    assert b"Open rate" in page and b"signal, not proof of reading" in page and b"failed to send" in page


def test_resend_from_the_log(client, admin, manager, client_obj):
    issued(manager, client_obj)
    message = EmailMessage.objects.filter(event="invoice.issued").first()
    EmailMessage.objects.filter(pk=message.pk).update(status="failed")
    client.force_login(admin)
    assert b"Send again" in client.get(f"/staff/notifications/emails/{message.pk}/").content
    mail.outbox.clear()
    response = client.post(f"/staff/notifications/emails/{message.pk}/resend/", follow=True)
    assert b"queued to send again" in response.content and reload(message).status == "sent" and len(mail.outbox) == 1
    again = client.post(f"/staff/notifications/emails/{message.pk}/resend/", follow=True)
    assert b"already delivered" in again.content
    assert b"Send again" not in client.get(f"/staff/notifications/emails/{message.pk}/").content


# --- API -----------------------------------------------------------------------------------------------------------

def test_api_unread_count_and_preferences(api, owner, admin):
    Notification.objects.create(user=owner, event="order.active", title="a")
    Notification.objects.create(user=owner, event="order.active", title="b", read_at=timezone.now())
    api.force_authenticate(owner)
    assert api.get("/api/v1/notifications/unread-count/").json() == {"unread": 1}
    rows = api.get("/api/v1/notification-preferences/").json()
    assert [r["category"] for r in rows] == ["orders", "services", "support"]
    updated = api.put("/api/v1/notification-preferences/", [
        {"category": "support", "email_enabled": False, "in_app_enabled": True}], format="json")
    assert updated.status_code == 200
    assert {r["category"]: r["email_enabled"] for r in updated.json()}["support"] is False
    bad = api.put("/api/v1/notification-preferences/", [
        {"category": "billing", "email_enabled": False, "in_app_enabled": False}], format="json")
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "preference_invalid"
    api.force_authenticate(None)
    assert api.get("/api/v1/notification-preferences/").status_code == 401


def test_api_email_log_stats_and_resend(api, owner, manager, admin, client_obj):
    issued(manager, client_obj)
    message = EmailMessage.objects.filter(event="invoice.issued").first()
    api.force_authenticate(owner)
    assert api.get("/api/v1/email-messages/").status_code == 403
    api.force_authenticate(manager)
    assert api.get("/api/v1/email-messages/").status_code == 403  # managers cannot see the email log
    api.force_authenticate(admin)
    listing = api.get("/api/v1/email-messages/?event=invoice.issued").json()
    assert listing["count"] == 1 and listing["results"][0]["event_label"] == "A new invoice"
    assert api.get("/api/v1/email-messages/?search=acme").json()["count"] >= 1
    stats = api.get("/api/v1/email-messages/stats/?days=7").json()
    assert stats["days"] == 7 and stats["sent"] >= 1 and "by_event" in stats
    EmailMessage.objects.filter(pk=message.pk).update(status="failed")
    resent = api.post(f"/api/v1/email-messages/{message.pk}/resend/")
    assert resent.status_code == 200 and resent.json()["status"] == "queued"
    assert reload(message).status == "sent"  # delivery ran after the request committed
    again = api.post(f"/api/v1/email-messages/{message.pk}/resend/")
    assert again.status_code == 400 and again.json()["error"]["code"] == "invalid_status"


def test_the_email_log_api_never_exposes_a_secret(api, admin):
    services.dispatch("hosting.welcome", email="c@acme.test", context={
        "account": type("A", (), {"domain": "site.com"})(), "username": "u", "password": "PLAINTEXT-PW"})
    api.force_authenticate(admin)
    body = api.get("/api/v1/email-messages/?event=hosting.welcome").content
    assert b"PLAINTEXT-PW" not in body and b"sensitive_body" not in body


def test_the_celery_tasks_exist_and_are_scheduled(settings):
    assert tasks.purge_sensitive_emails_task() == 0
    assert {"send-invoice-reminders", "purge-sensitive-emails"} <= set(settings.CELERY_BEAT_SCHEDULE)


def test_an_issued_invoice_reaches_the_customer_in_app_and_by_email_once(manager, owner, client_obj):
    mail.outbox.clear()
    invoice = issued(manager, client_obj)
    assert len(mail.outbox) == 1 and mail.outbox[0].to == [client_obj.email]
    row = Notification.objects.get(event="invoice.issued")
    assert row.user == owner and invoice.number in row.title and row.link.endswith(f"/{invoice.pk}/")
    assert not Notification.objects.filter(user=manager).exists()  # the staff member who issued it is not the audience
    assert EmailMessage.objects.get(event="invoice.issued").user is None  # and the email is not filed under them
