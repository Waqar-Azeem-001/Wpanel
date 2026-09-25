"""
The single notification/email entry point (roadmap Phase 11).

    Business event -> ``dispatch`` -> in-app notification and/or email -> configured provider

Business code names an *event* from ``events.EVENTS``; this module decides the rest: whether the person has switched
that kind of message off (essential events cannot be switched off), whether an in-app notification and/or an email is
sent, and how the email is recorded, protected, tracked and delivered.

Email is recorded first and delivered asynchronously by the ``deliver_email`` Celery task after the surrounding
transaction commits, so a rolled-back business action never sends a message, and a slow mail server never blocks a
request. An email carrying a secret (a temporary password, a one-time link) is stored encrypted until delivered and
then wiped: the email log must never become a second copy of a credential.
"""
import html
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import transaction
from django.db.models import Count, F, Q, Value
from django.db.models.functions import Coalesce
from django.template import TemplateDoesNotExist
from django.template.loader import render_to_string
from django.utils import timezone
from rest_framework import status as http

from apps.accounts.roles import Role, perm
from apps.core import crypto
from apps.core.exceptions import ServiceError

from . import events
from .models import EmailMessage, EmailProvider, Notification, NotificationPreference

logger = logging.getLogger(__name__)

SENSITIVE_PLACEHOLDER = "[This message contained a secret. It is kept encrypted until delivered and then removed.]"
SENSITIVE_KEEP_FAILED = timedelta(hours=24)
FAILURE_ALERT_AFTER_ATTEMPTS = 6
URL_RE = re.compile(r"(https?://[^\s<>\"]+)")


# --- Provider and rendering ------------------------------------------------------------------------------------

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
    from apps.branding import services as branding

    ctx = {"site_name": branding.get().name, "site_url": settings.SITE_URL, **context}
    subject = " ".join(render_to_string(f"emails/{template}_subject.txt", ctx).split())
    body_text = render_to_string(f"emails/{template}.txt", ctx)
    try:
        body_html = render_to_string(f"emails/{template}.html", ctx)
    except TemplateDoesNotExist:
        body_html = ""
    return subject, body_text, body_html


def html_from_text(text):
    """A safe HTML version of a plain-text email in the branded email layout: escaped, paragraphs kept, links live."""
    from django.template.loader import render_to_string

    from apps.branding import services as branding

    paragraphs = []
    for block in text.replace("\r", "").strip().split("\n\n"):
        escaped = html.escape(block.strip())
        linked = URL_RE.sub(lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', escaped)
        paragraphs.append(f"<p>{linked.replace(chr(10), '<br>')}</p>")
    brand = branding.get()
    logo_url = (settings.SITE_URL.rstrip("/") + reverse("brand_logo") + f"?v={brand.version}") if brand.has_logo else ""
    return render_to_string("layouts/email.html", {"brand": brand, "logo_url": logo_url, "body": "".join(paragraphs)})


def _with_open_pixel(body_html, token):
    if not settings.EMAIL_OPEN_TRACKING:
        return body_html
    pixel = (f'<img src="{settings.SITE_URL.rstrip("/")}{reverse("email_open_pixel", args=[token])}" width="1" height="1" alt="" '
             'style="display:none">')
    return body_html.replace("</body>", pixel + "</body>", 1) if "</body>" in body_html else body_html + pixel


# --- Sending ----------------------------------------------------------------------------------------------------

def send_email(*, to_email, template, context=None, user=None, event=""):
    """Record an email and queue it for delivery once the current transaction commits."""
    subject, body_text, body_html = render_email(template, context or {})
    body_html = body_html or html_from_text(body_text)
    sensitive = template in events.sensitive_templates()
    token = uuid.uuid4()
    fields = {}
    if sensitive:
        # No open-tracking pixel on security emails, and the content is kept encrypted only until delivery.
        fields = {"is_sensitive": True,
                  "sensitive_body": crypto.encrypt(json.dumps({"text": body_text, "html": body_html})),
                  "body_text": SENSITIVE_PLACEHOLDER, "body_html": ""}
    else:
        fields = {"body_text": body_text, "body_html": _with_open_pixel(body_html, token)}
    message = EmailMessage.objects.create(user=user, event=event, template=template, to_email=to_email,
                                          subject=subject, track_token=token, **fields)
    from .tasks import deliver_email

    transaction.on_commit(lambda: deliver_email.delay(message.pk))
    return message


def _content(message):
    if message.is_sensitive:
        if not message.sensitive_body:
            raise ServiceError("The secret content of this email has been removed, so it cannot be sent again.",
                               code="content_removed", status_code=http.HTTP_409_CONFLICT)
        data = json.loads(crypto.decrypt(message.sensitive_body))
        return data["text"], data["html"]
    return message.body_text, message.body_html


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
        body_text, body_html = _content(message)
        connection, sender = _connection_and_sender()
        mail = EmailMultiAlternatives(
            subject=message.subject,
            body=body_text,
            from_email=sender,
            to=[message.to_email],
            connection=connection,
        )
        if body_html:
            mail.attach_alternative(body_html, "text/html")
        mail.send(fail_silently=False)
    except Exception as exc:
        message.status = EmailMessage.Status.FAILED
        message.last_error = f"{type(exc).__name__}: {exc}"[:2000]
        message.save(update_fields=["status", "last_error", "updated_at"])
        logger.warning("Email %s delivery failed (attempt %s): %s", message.pk, message.attempts, message.last_error)
        if message.attempts >= FAILURE_ALERT_AFTER_ATTEMPTS:
            _alert_email_failing(message)
        raise

    message.status = EmailMessage.Status.SENT
    message.from_email = sender
    message.sent_at = timezone.now()
    message.last_error = ""
    update = ["status", "from_email", "sent_at", "last_error", "updated_at"]
    if message.is_sensitive:  # delivered: the secret is no longer kept anywhere in our database
        message.sensitive_body = ""
        message.body_text = SENSITIVE_PLACEHOLDER
        update += ["sensitive_body", "body_text"]
    message.save(update_fields=update)
    return True


def _alert_email_failing(message):
    """Tell the people who can fix it (at most once an hour) that an email has run out of retries."""
    try:
        recent = Notification.objects.filter(event="email.failed", created_at__gte=timezone.now() - timedelta(hours=1))
        if not recent.exists():
            notify_team("email.failed", "manage_settings", title="Emails are failing to send",
                        body=f"'{message.subject}' to {message.to_email} failed after {message.attempts} attempts: "
                             f"{message.last_error[:150]}", link=reverse("notifications_staff:emails") + "?status=failed")
    except Exception:  # noqa: BLE001 - an alert must never make a delivery failure worse
        logger.exception("Could not raise the email-failure alert")


# --- Preferences --------------------------------------------------------------------------------------------------

def effective_channels(user, event):
    """(send email, send in-app) for ``user`` and this event: essential events ignore preferences."""
    if event.essential:
        return True, event.default_in_app
    if user is None:
        return event.default_email, event.default_in_app
    pref = NotificationPreference.objects.filter(user=user, category=event.category).first()
    return (pref.email_enabled, pref.in_app_enabled) if pref else (event.default_email, event.default_in_app)


def is_staff_user(user):
    return bool(user and getattr(user, "role", Role.CUSTOMER) != Role.CUSTOMER)


def preference_rows(user):
    """The optional categories this person can control, with the current effective settings."""
    rows = []
    for category in events.USER_CONTROLLED:
        if category == events.Category.TEAM and not is_staff_user(user):
            continue
        items = events.events_in(category)
        pref = NotificationPreference.objects.filter(user=user, category=category).first()
        first = items[0]
        rows.append({
            "category": category, "label": events.CATEGORY_LABELS[category], "events": items,
            "email_enabled": pref.email_enabled if pref else first.default_email,
            "in_app_enabled": pref.in_app_enabled if pref else first.default_in_app})
    return rows


def save_preferences(user, choices):
    """``choices``: {category: (email_enabled, in_app_enabled)}. Only optional categories can be changed."""
    from apps.audit import services as audit

    allowed = {row["category"] for row in preference_rows(user)}
    changed = {}
    for category, (email_on, in_app_on) in choices.items():
        if category not in allowed:
            raise ServiceError("That kind of message cannot be changed.", code="preference_invalid")
        NotificationPreference.objects.update_or_create(
            user=user, category=category, defaults={"email_enabled": bool(email_on), "in_app_enabled": bool(in_app_on)})
        changed[category] = {"email": bool(email_on), "in_app": bool(in_app_on)}
    if changed:
        audit.record("notification_preferences.updated", actor=user, metadata={"categories": changed})
    return preference_rows(user)


# --- Dispatch -----------------------------------------------------------------------------------------------------

@dataclass
class Dispatched:
    notification: Notification | None = None
    message: EmailMessage | None = None


def dispatch(event, *, user=None, email=None, title="", body="", link="", context=None, in_app_only=False):
    """
    Send one business event to one person: an in-app notification (if ``user`` and a ``title`` are given and they
    have not switched it off) and the event's email (to ``email``, else the user's address) if it has a template and
    the person wants it (``in_app_only`` skips the email). Essential events are always sent. Returns what was created.
    """
    definition = events.get(event)
    want_email, want_in_app = effective_channels(user, definition)
    result = Dispatched()
    if user is not None and title and want_in_app:
        result.notification = Notification.objects.create(user=user, event=definition.key, title=title[:255],
                                                         body=body, link=link)
    address = email or (user.email if user is not None else "")
    if definition.email_template and want_email and address and not in_app_only:
        result.message = send_email(to_email=address, template=definition.email_template,
                                    context={"user": user, **(context or {})}, user=user, event=definition.key)
    return result


def staff_with(codename):
    """Active staff who hold a portal permission (the audience for a team alert)."""
    from apps.accounts.models import User

    return [u for u in User.objects.filter(is_active=True).exclude(role=Role.CUSTOMER) if u.has_perm(perm(codename))]


def notify_team(event, codename, *, title, body="", link="", context=None, exclude=None):
    """A team alert to every staff member holding ``codename``, each honouring their own preferences."""
    sent = []
    for person in staff_with(codename):
        if exclude is not None and person.pk == getattr(exclude, "pk", None):
            continue
        sent.append(dispatch(event, user=person, title=title, body=body, link=link, context=context))
    return sent


def dispatch_client(event, client, *, title="", body="", link="", context=None, email=None):
    """
    Reach a client about something on their account: every owner and billing contact gets it (each honouring their
    own preferences); if the account has no such contact, the client's own address is emailed instead.

    ``email`` sends the email once to that address (an invoice's billing address, say) and gives the contacts the
    in-app notification only.
    """
    from apps.clients.models import ClientContact, ContactRole

    contacts = list(ClientContact.objects.filter(client=client, role__in=(ContactRole.OWNER, ContactRole.BILLING))
                    .select_related("user"))
    if email:
        sent = [dispatch(event, email=email, context=context)]
        return sent + [dispatch(event, user=c.user, title=title, body=body, link=link, in_app_only=True)
                       for c in contacts]
    if not contacts:
        return [dispatch(event, email=client.email, context=context)]
    return [dispatch(event, user=c.user, title=title, body=body, link=link, context=context) for c in contacts]


def notify(user, *, event, title, body="", link="", email_template=None, context=None):
    """Create an in-app notification and, optionally, send the matching email (low-level; prefer ``dispatch``)."""
    notification = Notification.objects.create(user=user, event=event, title=title, body=body, link=link)
    if email_template:
        send_email(to_email=user.email, template=email_template, context={"user": user, **(context or {})},
                   user=user, event=event)
    return notification


# --- The in-app inbox ---------------------------------------------------------------------------------------------

def unread_count(user):
    if user is None or not user.is_authenticated:
        return 0
    return Notification.objects.filter(user=user, read_at__isnull=True).count()


def mark_read(user, notification_ids=None):
    """Mark the user's notifications read (all unread ones when no IDs are given)."""
    qs = Notification.objects.filter(user=user, read_at__isnull=True)
    if notification_ids is not None:
        qs = qs.filter(pk__in=notification_ids)
    return qs.update(read_at=timezone.now())


def safe_link(link):
    """A notification's link, only if it is a path on this site (never an external address)."""
    return link if link.startswith("/") and not link.startswith("//") and "\\" not in link else "/account/notifications/"


# --- Tracking, log, stats ---------------------------------------------------------------------------------------

def record_open(token):
    """Note that the tracking pixel was fetched. A signal, not proof of reading (proxies and scanners fetch it too)."""
    return EmailMessage.objects.filter(track_token=token).update(
        open_count=F("open_count") + 1, opened_at=Coalesce("opened_at", Value(timezone.now())))


def email_stats(*, days=30, now=None):
    """Sent / failed / opened counts and the open rate, overall and per event, for the last ``days`` days."""
    now = now or timezone.now()
    since = now - timedelta(days=days)
    rows = (EmailMessage.objects.filter(created_at__gte=since).values("event").annotate(
        total=Count("id"),
        sent=Count("id", filter=Q(status=EmailMessage.Status.SENT)),
        failed=Count("id", filter=Q(status=EmailMessage.Status.FAILED)),
        queued=Count("id", filter=Q(status=EmailMessage.Status.QUEUED)),
        tracked=Count("id", filter=Q(status=EmailMessage.Status.SENT, is_sensitive=False)),
        opened=Count("id", filter=Q(status=EmailMessage.Status.SENT, is_sensitive=False, opened_at__isnull=False)),
    ).order_by("-total"))

    def rate(opened, tracked):
        return round(100 * opened / tracked, 1) if tracked else None

    by_event = [{**row, "event_label": events.EVENTS[row["event"]].label if row["event"] in events.EVENTS
                 else (row["event"] or "(other)"), "open_rate": rate(row["opened"], row["tracked"])} for row in rows]
    totals = {key: sum(r[key] for r in by_event) for key in ("total", "sent", "failed", "queued", "tracked", "opened")}
    return {**totals, "open_rate": rate(totals["opened"], totals["tracked"]), "days": days, "by_event": by_event,
            "tracking_enabled": settings.EMAIL_OPEN_TRACKING}


@transaction.atomic
def resend(actor, message, *, request=None):
    """Staff (``manage_settings``): send a failed email again."""
    from apps.audit import services as audit

    if not actor.has_perm(perm("manage_settings")):
        raise ServiceError("You do not have permission to perform this action.", code="permission_denied",
                           status_code=http.HTTP_403_FORBIDDEN)
    message = EmailMessage.objects.select_for_update().get(pk=message.pk)
    if message.status == EmailMessage.Status.SENT:
        raise ServiceError("This email was already delivered.", code="invalid_status")
    _content(message)  # refuses if a secret has already been removed
    message.status = EmailMessage.Status.QUEUED
    message.save(update_fields=["status", "updated_at"])
    audit.record("email.resent", actor=actor, target=message, metadata={"to": message.to_email}, request=request)
    from .tasks import deliver_email

    transaction.on_commit(lambda: deliver_email.delay(message.pk))
    return message


def purge_sensitive(*, now=None):
    """Remove the secret content of emails that never got delivered (run daily). Returns how many were wiped."""
    cutoff = (now or timezone.now()) - SENSITIVE_KEEP_FAILED
    stale = EmailMessage.objects.filter(is_sensitive=True, status__in=(EmailMessage.Status.FAILED,
                                                                        EmailMessage.Status.QUEUED),
                                        created_at__lt=cutoff).exclude(sensitive_body="")
    return stale.update(sensitive_body="", body_text=SENSITIVE_PLACEHOLDER)


# --- The email provider (Setup > Email Provider) ---------------------------------------------------------------------

PROVIDER_FIELDS = ("name", "kind", "host", "port", "username", "use_tls", "use_ssl", "timeout", "from_email")


def _require_providers(actor, codename="manage_providers"):
    if not actor.has_perm(perm(codename)):
        raise ServiceError("You do not have permission to perform this action.", code="permission_denied",
                           status_code=http.HTTP_403_FORBIDDEN)


@transaction.atomic
def save_email_provider(actor, provider, data, *, password=None, request=None):
    """
    Create or change an email provider (``provider=None`` creates). The password is optional: blank keeps the stored one;
    it is encrypted and never shown again. Making a provider active switches every other one off, in the same transaction
    (only one may be active). Everything is validated before anything is written.
    """
    from apps.audit import services as audit

    _require_providers(actor)
    created = provider is None
    provider = provider or EmailProvider()
    before = {f: getattr(provider, f) for f in PROVIDER_FIELDS} if not created else {}
    for field in PROVIDER_FIELDS:
        if field in data:
            setattr(provider, field, data[field])
    make_active = bool(data.get("is_active", provider.is_active))
    if password:
        provider.set_password(password)
    provider.full_clean(exclude=["is_active"])
    if make_active:
        EmailProvider.objects.exclude(pk=provider.pk).filter(is_active=True).update(is_active=False)
    provider.is_active = make_active
    provider.save()
    changed = sorted(f for f in PROVIDER_FIELDS if getattr(provider, f) != before.get(f))
    audit.record("email_provider.created" if created else "email_provider.updated", actor=actor, target=provider,
                 metadata={"fields": changed, "password_changed": bool(password), "active": provider.is_active},
                 request=request)
    return provider


@transaction.atomic
def delete_email_provider(actor, provider, *, request=None):
    from apps.audit import services as audit

    _require_providers(actor)
    if provider.is_active:
        raise ServiceError("Switch to another provider (or none) before deleting the active one.", code="provider_active")
    audit.record("email_provider.deleted", actor=actor, target=provider, metadata={"name": provider.name}, request=request)
    provider.delete()


def send_test_email(actor, provider, to_email, *, request=None):
    """
    Send one short message through ``provider`` (active or not) to prove its settings work. Returns ``(ok, detail)``; a
    failure is a result to show, not an exception. The message is not stored, and no password ever appears in the result.
    """
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    from apps.audit import services as audit
    from apps.branding import services as branding

    _require_providers(actor)
    try:
        validate_email(to_email or "")
    except ValidationError:
        raise ServiceError("Enter a valid email address to send the test to.", code="invalid_email")
    name = branding.get().name
    try:
        connection = get_connection("django.core.mail.backends.smtp.EmailBackend", host=provider.host, port=provider.port,
                                    username=provider.username or None, password=provider.get_password() or None,
                                    use_tls=provider.use_tls, use_ssl=provider.use_ssl, timeout=provider.timeout)
        sent = EmailMultiAlternatives(f"{name}: test email", f"This is a test message from {name}. If you can read it, "
                                      "your email provider settings work.", provider.from_email, [to_email],
                                      connection=connection).send()
        ok, detail = bool(sent), "Sent." if sent else "The server accepted no message."
    except Exception as exc:  # noqa: BLE001 - the point is to report why sending failed
        ok, detail = False, f"{type(exc).__name__}: {str(exc)[:200]}"
        if provider.get_password() and provider.get_password() in detail:
            detail = type(exc).__name__  # never echo a credential
    audit.record("email_provider.tested", actor=actor, target=provider, metadata={"ok": ok, "to": to_email}, request=request)
    return ok, detail
