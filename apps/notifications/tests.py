from unittest import mock

import pytest
from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError

from apps.notifications import services
from apps.notifications.backends import NoProviderConfiguredBackend
from apps.notifications.models import EmailMessage, EmailProvider, Notification
from apps.notifications.tasks import deliver_email

pytestmark = pytest.mark.django_db


def test_send_email_records_and_delivers_through_celery(customer):
    message = services.send_email(
        to_email=customer.email, template="verify_email",
        context={"user": customer, "link": "https://x/verify"}, user=customer, event="test",
    )
    message.refresh_from_db()
    assert message.status == EmailMessage.Status.SENT
    assert message.attempts == 1 and message.sent_at
    assert len(mail.outbox) == 1 and "https://x/verify" in mail.outbox[0].body


def test_delivery_is_idempotent(customer):
    message = services.send_email(to_email=customer.email, template="verify_email",
                                  context={"user": customer, "link": "l"})
    deliver_email.delay(message.pk)
    services.deliver(message.pk)
    assert len(mail.outbox) == 1


def test_delivery_failure_is_recorded_and_raised(customer):
    message = EmailMessage.objects.create(to_email=customer.email, template="t", subject="s", body_text="b")
    with mock.patch("django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("smtp down")):
        with pytest.raises(OSError):
            services.deliver(message.pk)
    message.refresh_from_db()
    assert message.status == EmailMessage.Status.FAILED
    assert "smtp down" in message.last_error


def test_active_provider_builds_smtp_connection_with_decrypted_password():
    provider = EmailProvider(name="Main", host="smtp.example.com", port=2525, username="u",
                             from_email="Billing <b@example.com>", is_active=True)
    provider.set_password("pw-123")
    provider.save()
    assert "pw-123" not in provider.password_encrypted

    connection, sender = services._connection_and_sender()
    assert sender == "Billing <b@example.com>"
    assert (connection.host, connection.port, connection.username, connection.password) == (
        "smtp.example.com", 2525, "u", "pw-123")


def test_only_one_provider_can_be_active():
    EmailProvider.objects.create(name="A", host="a", from_email="a@x.com", is_active=True)
    with pytest.raises(IntegrityError):
        EmailProvider.objects.create(name="B", host="b", from_email="b@x.com", is_active=True)


def test_production_fallback_refuses_to_send():
    with pytest.raises(ImproperlyConfigured):
        NoProviderConfiguredBackend().send_messages([object()])


def test_notify_creates_notification_and_email(customer):
    services.notify(customer, event="test.event", title="Hello", email_template="verify_email",
                    context={"link": "l"})
    assert Notification.objects.filter(user=customer, title="Hello").exists()
    assert len(mail.outbox) == 1


def test_notifications_api_is_scoped_to_owner(api, customer, make_user):
    other = make_user("other@example.com")
    mine = services.notify(customer, event="e", title="Mine")
    theirs = services.notify(other, event="e", title="Theirs")
    api.force_authenticate(customer)
    results = api.get("/api/v1/notifications/").json()["results"]
    assert [n["title"] for n in results] == ["Mine"]
    assert api.get(f"/api/v1/notifications/{theirs.pk}/").status_code == 404
    assert api.get("/api/v1/notifications/?unread=true").json()["count"] == 1

    assert api.post("/api/v1/notifications/mark-read/", {"ids": [mine.pk, theirs.pk]}).json() == {"updated": 1}
    theirs.refresh_from_db()
    assert theirs.read_at is None
    assert api.get("/api/v1/notifications/?unread=true").json()["count"] == 0
