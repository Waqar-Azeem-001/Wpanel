"""Phase 11: the event registry, dispatch and preferences, secret-content protection, open tracking, and the wiring."""
import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.core import mail
from celery.exceptions import Retry
from django.utils import timezone

from apps.accounts.roles import Role
from apps.audit.models import AuditEvent
from apps.clients import services as client_services
from apps.clients.models import ContactRole
from apps.core import crypto
from apps.core.exceptions import ServiceError
from apps.notifications import events, services, tasks
from apps.notifications.models import EmailMessage, Notification, NotificationPreference

pytestmark = pytest.mark.django_db

SECRET = "Sup3r-Secret-Temp-Password"
DELIVERY_FAILED = (ConnectionError, Retry)  # eager Celery reports a failed delivery as a scheduled retry


@pytest.fixture
def manager(staff):
    return staff(Role.MANAGER)


@pytest.fixture
def admin(staff):
    return staff(Role.ADMIN)


@pytest.fixture
def client_obj(manager):
    return client_services.create_client(manager, {"first_name": "Ada", "email": "ada@acme.test",
                                                   "company_name": "Acme Ltd"})


@pytest.fixture
def owner(client_obj):
    return client_obj.contacts.get(role="owner").user


def reload(obj):
    obj.refresh_from_db()
    return obj


# --- The registry ---------------------------------------------------------------------------------------------

def test_every_event_used_in_code_is_registered():
    """A message that code sends but the catalogue does not know about would bypass preferences and protection."""
    root = Path(django_settings.BASE_DIR) / "apps"
    used = set()
    pattern = re.compile(r"(?:dispatch|dispatch_client|notify_team)\(\s*\"([a-z_]+\.[a-z_]+)\"")
    pattern_event = re.compile(r"event=\"([a-z_]+\.[a-z_]+)\"")
    for path in root.rglob("*.py"):
        if "tests" in path.parts or path.name.startswith("test_") or path.name == "tests.py":
            continue
        text = path.read_text(encoding="utf-8")
        used |= set(pattern.findall(text)) | set(pattern_event.findall(text))
    unknown = used - set(events.EVENTS)
    assert not unknown, f"events used in code but missing from the registry: {sorted(unknown)}"
    assert len(used) >= 20  # (the scan found the real call sites, not nothing)


def test_every_registered_email_has_its_templates():
    templates = Path(django_settings.BASE_DIR) / "templates" / "emails"
    for event in events.EVENTS.values():
        if event.email_template:
            assert (templates / f"{event.email_template}.txt").exists(), event.key
            assert (templates / f"{event.email_template}_subject.txt").exists(), event.key


def test_the_catalogue_is_consistent():
    assert events.sensitive_templates() == {"client_welcome", "verify_email", "password_reset", "hosting_welcome", "staff_welcome"}
    for event in events.EVENTS.values():
        assert event.category in events.CATEGORY_LABELS
        if event.essential:  # a message people cannot switch off is never in a category they can control...
            assert event.category not in (events.Category.TEAM,)
        if event.category == events.Category.TEAM:
            assert not event.email_template and event.default_email is False  # team alerts are in-app by default
    assert all(e.category in (events.Category.ACCOUNT, events.Category.BILLING) or e.category in events.USER_CONTROLLED
               or e.essential for e in events.EVENTS.values())
    with pytest.raises(events.UnknownEvent):
        events.get("nope.nothing")


# --- Dispatch and preferences ------------------------------------------------------------------------------------

def test_dispatch_sends_in_app_and_email_and_returns_both(owner):
    mail.outbox.clear()
    result = services.dispatch("ticket.reply", user=owner, title="Reply on T1", body="Subject", link="/x/",
                               context={"ticket": type("T", (), {"reference": "T1", "subject": "S"})(),
                                        "message": type("M", (), {"author_name": "Sam", "body": "Hi",
                                                                  "attachments": type("A", (), {"all": lambda s: []})()})(),
                                        "link": "/x/"})
    assert result.notification and result.notification.user == owner and result.message.to_email == owner.email
    assert len(mail.outbox) == 1


def test_an_unknown_event_is_refused(owner):
    with pytest.raises(events.UnknownEvent):
        services.dispatch("made.up", user=owner, title="x")


def test_essential_events_ignore_preferences(owner):
    for category in events.USER_CONTROLLED:
        NotificationPreference.objects.create(user=owner, category=category, email_enabled=False, in_app_enabled=False)
    for key in ("invoice.issued", "payment.received", "order.placed", "hosting.suspended", "hosting.welcome",
                "account.password_reset"):
        assert services.effective_channels(owner, events.get(key)) == (True, True), key  # switching things off changes nothing


def test_optional_events_respect_preferences(owner):
    optional = events.get("ticket.reply")
    assert services.effective_channels(owner, optional) == (True, True)  # defaults
    services.save_preferences(owner, {"support": (False, True)})
    assert services.effective_channels(owner, optional) == (False, True)
    mail.outbox.clear()
    ctx = {"ticket": type("T", (), {"reference": "T1", "subject": "S"})(), "link": "/x/",
           "message": type("M", (), {"author_name": "Sam", "body": "Hi", "attachments": type("A", (), {"all": lambda s: []})()})()}
    result = services.dispatch("ticket.reply", user=owner, title="Reply", link="/x/", context=ctx)
    assert result.message is None and result.notification is not None and mail.outbox == []
    services.save_preferences(owner, {"support": (True, False)})
    result = services.dispatch("ticket.reply", user=owner, title="Reply", link="/x/", context=ctx)
    assert result.notification is None and result.message is not None


def test_preferences_are_validated_audited_and_role_aware(owner, admin):
    with pytest.raises(ServiceError) as exc:
        services.save_preferences(owner, {"billing": (False, False)})  # essential: cannot be changed
    assert exc.value.code == "preference_invalid"
    with pytest.raises(ServiceError):
        services.save_preferences(owner, {"team": (True, True)})  # a customer has no team alerts
    assert [r["category"] for r in services.preference_rows(owner)] == ["orders", "services", "support"]
    assert [r["category"] for r in services.preference_rows(admin)] == ["orders", "services", "support", "team"]
    services.save_preferences(admin, {"team": (True, False)})
    assert AuditEvent.objects.filter(action="notification_preferences.updated").exists()
    team = [r for r in services.preference_rows(admin) if r["category"] == "team"][0]
    assert (team["email_enabled"], team["in_app_enabled"]) == (True, False)
    fresh = [r for r in services.preference_rows(owner) if r["category"] == "support"][0]
    assert fresh["email_enabled"] and fresh["in_app_enabled"]


def test_dispatch_to_an_address_uses_defaults_and_needs_an_address(owner):
    mail.outbox.clear()
    assert services.dispatch("hosting.suspended", context={"account": type("A", (), {"domain": "d.com"})(), "link": "/"}
                             ).message is None  # nobody to send to
    services.dispatch("hosting.suspended", email="someone@else.test",
                      context={"account": type("A", (), {"domain": "d.com"})(), "link": "/", "reason": "x"})
    assert mail.outbox[-1].to == ["someone@else.test"]


def test_dispatch_client_reaches_owner_and_billing_contacts_only(manager, owner, client_obj):
    client_services.add_contact(manager, client_obj, email="billing@acme.test", role=ContactRole.BILLING)
    client_services.add_contact(manager, client_obj, email="tech@acme.test", role=ContactRole.TECHNICAL)
    mail.outbox.clear()
    sent = services.dispatch_client("order.cancelled", client_obj, title="Cancelled", link="/o/",
                                    context={"order": type("O", (), {"billing_name": "A", "reference": "O1",
                                                                     "cancel_reason": ""})(), "link": "/o/"})
    recipients = {m.to[0] for m in mail.outbox}
    assert recipients == {owner.email, "billing@acme.test"} and len(sent) == 2
    assert not Notification.objects.filter(user__email="tech@acme.test").exists()


def test_dispatch_client_falls_back_to_the_clients_own_address(client_obj):
    from apps.clients.models import ClientContact

    ClientContact.objects.filter(client=client_obj).delete()
    mail.outbox.clear()
    services.dispatch_client("hosting.suspended", client_obj,
                             context={"account": type("A", (), {"domain": "d.com"})(), "link": "/", "reason": ""})
    assert [m.to for m in mail.outbox] == [[client_obj.email]]


def test_team_alerts_go_to_staff_with_the_permission_and_honour_their_choices(staff, admin, customer):
    agent, manager = staff(Role.SUPPORT_AGENT), staff(Role.MANAGER)
    services.save_preferences(manager, {"team": (False, False)})  # the manager opts out
    sent = services.notify_team("ticket.unassigned", "manage_support", title="New ticket", link="/t/")
    recipients = {d.notification.user for d in sent if d.notification}
    assert recipients == {agent, admin}  # not the customer, not the manager who opted out
    assert not Notification.objects.filter(user=customer).exists()
    sent = services.notify_team("ticket.unassigned", "manage_support", title="Again", exclude=agent)
    assert agent not in {d.notification.user for d in sent if d.notification}
    assert all(d.message is None for d in sent)  # team alerts are in-app only by default


# --- Secret content ---------------------------------------------------------------------------------------------

def hosting_welcome():
    return services.dispatch("hosting.welcome", email="client@acme.test",
                             context={"account": type("A", (), {"domain": "site.com"})(), "username": "site",
                                      "password": SECRET}).message


def stored_text(message):
    row = EmailMessage.objects.filter(pk=message.pk).values().get()
    return " ".join(str(v) for k, v in row.items() if k != "sensitive_body")


def test_a_password_never_sits_in_the_email_log(settings):
    mail.outbox.clear()
    message = hosting_welcome()
    assert SECRET in mail.outbox[0].body  # the customer receives it
    message.refresh_from_db()
    assert message.is_sensitive and message.status == "sent"
    assert SECRET not in stored_text(message) and message.sensitive_body == ""  # wiped once delivered
    assert "kept encrypted until delivered" in message.body_text
    assert not message.body_html


def test_a_secret_is_encrypted_while_waiting_and_survives_a_delivery_failure(monkeypatch):
    def fail(self, fail_silently=False):
        raise ConnectionError("smtp down")

    monkeypatch.setattr("django.core.mail.EmailMultiAlternatives.send", fail)
    with pytest.raises(DELIVERY_FAILED):
        hosting_welcome()
    message = EmailMessage.objects.get()
    assert message.status == "failed" and message.sensitive_body
    assert SECRET not in message.sensitive_body and SECRET not in stored_text(message)  # ciphertext only
    assert SECRET in crypto.decrypt(message.sensitive_body)


def test_a_failed_secret_email_can_be_resent_then_is_wiped(monkeypatch, admin):
    original = mail.EmailMultiAlternatives.send
    monkeypatch.setattr("django.core.mail.EmailMultiAlternatives.send",
                        lambda self, fail_silently=False: (_ for _ in ()).throw(ConnectionError("down")))
    with pytest.raises(DELIVERY_FAILED):
        hosting_welcome()
    monkeypatch.setattr("django.core.mail.EmailMultiAlternatives.send", original)
    message = EmailMessage.objects.get()
    mail.outbox.clear()
    services.resend(admin, message)
    message.refresh_from_db()
    assert message.status == "sent" and SECRET in mail.outbox[0].body and message.sensitive_body == ""
    with pytest.raises(ServiceError) as exc:
        services.resend(admin, message)
    assert exc.value.code == "invalid_status"


def test_undelivered_secrets_are_purged_after_a_day_and_cannot_then_be_resent(monkeypatch, admin):
    monkeypatch.setattr("django.core.mail.EmailMultiAlternatives.send",
                        lambda self, fail_silently=False: (_ for _ in ()).throw(ConnectionError("down")))
    with pytest.raises(DELIVERY_FAILED):
        hosting_welcome()
    message = EmailMessage.objects.get()
    assert services.purge_sensitive() == 0  # too recent
    EmailMessage.objects.filter(pk=message.pk).update(created_at=timezone.now() - timedelta(hours=25))
    assert tasks.purge_sensitive_emails_task() == 1
    message.refresh_from_db()
    assert message.sensitive_body == "" and SECRET not in stored_text(message)
    with pytest.raises(ServiceError) as exc:
        services.resend(admin, message)
    assert exc.value.code == "content_removed" and exc.value.status_code == 409


def test_verification_and_reset_links_are_protected_too(customer):
    from apps.accounts import services as account_services

    mail.outbox.clear()
    account_services.send_verification_email(customer)
    account_services.request_password_reset(customer.email)
    assert len(mail.outbox) == 2
    for message in EmailMessage.objects.all():
        link = re.search(r"https?://\S+", mail.outbox[0].body).group(0)
        assert message.is_sensitive and link not in stored_text(message) and not message.body_html


def test_the_admin_never_shows_the_secret_column():
    from apps.notifications.admin import EmailMessageAdmin

    assert "sensitive_body" in EmailMessageAdmin.exclude and "sensitive_body" not in EmailMessageAdmin.readonly_fields


# --- HTML and open tracking ---------------------------------------------------------------------------------------

def normal_email():
    return services.dispatch("ticket.opened", email="a@b.test",
                             context={"ticket": type("T", (), {"reference": "T1", "subject": "x <script>alert(1)</script>",
                                                               "department": type("D", (), {"name": "Tech"})()})(),
                                      "link": "/account/support/1/"}).message


def test_emails_get_an_html_version_with_a_tracking_pixel(settings):
    settings.EMAIL_OPEN_TRACKING = True
    mail.outbox.clear()
    message = normal_email()
    message.refresh_from_db()
    assert f"/e/o/{message.track_token}.gif" in message.body_html and 'width="1"' in message.body_html
    html = mail.outbox[0].alternatives[0][0]
    assert f"/e/o/{message.track_token}.gif" in html
    assert "<script>" not in html and "&lt;script&gt;" in html  # hostile text is escaped, never live
    assert '<a href="http' in html  # web addresses are clickable


def test_tracking_can_be_switched_off_and_is_never_on_security_emails(settings):
    settings.EMAIL_OPEN_TRACKING = False
    message = normal_email()
    message.refresh_from_db()
    assert "/e/o/" not in message.body_html and message.body_html  # still gets an HTML version
    settings.EMAIL_OPEN_TRACKING = True
    secret = hosting_welcome()
    assert not secret.body_html and "/e/o/" not in mail.outbox[-1].alternatives[0][0]


def test_the_pixel_records_opens_and_reveals_nothing(client, settings):
    settings.EMAIL_OPEN_TRACKING = True
    message = normal_email()
    url = f"/e/o/{message.track_token}.gif"
    first = client.get(url)
    assert first.status_code == 200 and first["Content-Type"] == "image/gif" and first.content.startswith(b"GIF89a")
    assert "no-store" in first["Cache-Control"]
    opened_at = reload(message).opened_at
    client.get(url)
    message = reload(message)
    assert message.open_count == 2 and message.opened_at == opened_at  # the first open time is kept
    import uuid

    unknown = client.get(f"/e/o/{uuid.uuid4()}.gif")
    assert unknown.status_code == 200 and unknown.content == first.content  # no way to tell a real token from a fake
    assert client.post(url).status_code == 405


def test_email_statistics(settings, owner):
    settings.EMAIL_OPEN_TRACKING = True
    EmailMessage.objects.all().delete()
    a = normal_email()
    normal_email()
    services.record_open(a.track_token)
    hosting_welcome()  # sensitive: sent, but not tracked
    failed = normal_email()
    EmailMessage.objects.filter(pk=failed.pk).update(status="failed")
    stats = services.email_stats(days=30)
    assert (stats["sent"], stats["failed"]) == (3, 1) and stats["opened"] == 1
    assert stats["tracked"] == 2 and stats["open_rate"] == 50.0  # the secret email is not part of the rate
    by_event = {row["event"]: row for row in stats["by_event"]}
    assert by_event["ticket.opened"]["opened"] == 1 and by_event["ticket.opened"]["event_label"] == "We received your ticket"
    assert services.email_stats(days=30, now=timezone.now() + timedelta(days=60))["total"] == 0  # outside the period


# --- Failure alerts ----------------------------------------------------------------------------------------------

def test_an_email_that_runs_out_of_retries_alerts_admins_once_an_hour(monkeypatch, admin, manager):
    monkeypatch.setattr("django.core.mail.EmailMultiAlternatives.send",
                        lambda self, fail_silently=False: (_ for _ in ()).throw(ConnectionError("smtp down")))
    with pytest.raises(DELIVERY_FAILED):
        normal_email()
    message = EmailMessage.objects.get()
    EmailMessage.objects.filter(pk=message.pk).update(attempts=services.FAILURE_ALERT_AFTER_ATTEMPTS - 1)
    with pytest.raises(DELIVERY_FAILED):
        services.deliver(message.pk)
    alerts = Notification.objects.filter(event="email.failed")
    assert set(alerts.values_list("user_id", flat=True)) == {admin.pk}  # admins can manage settings; the manager cannot
    assert "smtp down" in alerts.get().body
    with pytest.raises(DELIVERY_FAILED):
        services.deliver(message.pk)
    assert Notification.objects.filter(event="email.failed").count() == 1  # not again within the hour


def test_resend_needs_manage_settings_and_an_undelivered_message(monkeypatch, admin, manager):
    monkeypatch.setattr("django.core.mail.EmailMultiAlternatives.send",
                        lambda self, fail_silently=False: (_ for _ in ()).throw(ConnectionError("down")))
    with pytest.raises(DELIVERY_FAILED):
        normal_email()
    message = EmailMessage.objects.get()
    with pytest.raises(ServiceError) as exc:
        services.resend(manager, message)
    assert exc.value.code == "permission_denied"
