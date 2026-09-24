"""
The single notification/email entry point.

Modules call ``send_email`` or ``notify``; delivery happens asynchronously in the
``deliver_email`` Celery task after the surrounding transaction commits.
"""
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import transaction
from django.template.loader import render_to_string
from django.template import TemplateDoesNotExist
from django.utils import timezone

from .models import EmailMessage, EmailProvider, Notification

logger = logging.getLogger(__name__)


def get_active_provider():
    return EmailProvider.objects.filter(is_active=True).first()


def _connection_and_sender():
    provider = get_active_provider()
    if provider is None:
        return get_connection(), settings.DEFAULT_FROM_EMAIL
    connection = get_connection(
        "django.core.mail.backends.smtp.EmailBackend",
        host=provider.host,
        port=provider.port,
        username=provider.username or None,
        password=provider.get_password() or None,
        use_tls=provider.use_tls,
        use_ssl=provider.use_ssl,
        timeout=provider.timeout,
    )
    return connection, provider.from_email


def render_email(template, context):
    """Render ``emails/<template>_subject.txt``, ``.txt`` and optional ``.html``."""
    ctx = {"site_name": settings.SITE_NAME, "site_url": settings.SITE_URL, **context}
    subject = " ".join(render_to_string(f"emails/{template}_subject.txt", ctx).split())
    body_text = render_to_string(f"emails/{template}.txt", ctx)
    try:
        body_html = render_to_string(f"emails/{template}.html", ctx)
    except TemplateDoesNotExist:
        body_html = ""
    return subject, body_text, body_html


def send_email(*, to_email, template, context=None, user=None, event=""):
    """Record an email and queue it for delivery once the current transaction commits."""
    subject, body_text, body_html = render_email(template, context or {})
    message = EmailMessage.objects.create(
        user=user,
        event=event,
        template=template,
        to_email=to_email,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
    )
    from .tasks import deliver_email

    transaction.on_commit(lambda: deliver_email.delay(message.pk))
    return message


def deliver(message_id):
    """
    Deliver a recorded email. Idempotent: an already-sent message is never re-sent.

    Returns True when sent; raises on failure so the Celery task can retry.
    """
    with transaction.atomic():
        message = EmailMessage.objects.select_for_update().get(pk=message_id)
        if message.status == EmailMessage.Status.SENT:
            return True
        message.attempts += 1
        message.save(update_fields=["attempts", "updated_at"])

    try:
        connection, sender = _connection_and_sender()
        mail = EmailMultiAlternatives(
            subject=message.subject,
            body=message.body_text,
            from_email=sender,
            to=[message.to_email],
            connection=connection,
        )
        if message.body_html:
            mail.attach_alternative(message.body_html, "text/html")
        mail.send(fail_silently=False)
    except Exception as exc:
        message.status = EmailMessage.Status.FAILED
        message.last_error = f"{type(exc).__name__}: {exc}"[:2000]
        message.save(update_fields=["status", "last_error", "updated_at"])
        logger.warning("Email %s delivery failed (attempt %s): %s", message.pk, message.attempts, message.last_error)
        raise

    message.status = EmailMessage.Status.SENT
    message.from_email = sender
    message.sent_at = timezone.now()
    message.last_error = ""
    message.save(update_fields=["status", "from_email", "sent_at", "last_error", "updated_at"])
    return True


def notify(user, *, event, title, body="", link="", email_template=None, context=None):
    """Create an in-app notification and, optionally, send the matching email."""
    notification = Notification.objects.create(user=user, event=event, title=title, body=body, link=link)
    if email_template:
        send_email(
            to_email=user.email,
            template=email_template,
            context={"user": user, **(context or {})},
            user=user,
            event=event,
        )
    return notification


def mark_read(user, notification_ids=None):
    """Mark the user's notifications read (all unread ones when no IDs are given)."""
    qs = Notification.objects.filter(user=user, read_at__isnull=True)
    if notification_ids is not None:
        qs = qs.filter(pk__in=notification_ids)
    return qs.update(read_at=timezone.now())
