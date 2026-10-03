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
from django.core.mail.message import make_msgid
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
from .models import EmailEvent, EmailLink, EmailMessage, EmailProvider, Notification, NotificationPreference

logger = logging.getLogger(__name__)

SENSITIVE_PLACEHOLDER = "[This message contained a secret. It is kept encrypted until delivered and then removed.]"
SENSITIVE_KEEP_FAILED = timedelta(hours=24)
FAILURE_ALERT_AFTER_ATTEMPTS = 6
URL_RE = re.compile(r"(https?://[^\s<>\"]+)")
HREF_RE = re.compile(r'(<a\b[^>]*?\shref=")(https?://[^"]+)(")', re.IGNORECASE)
SWEEP_AFTER = timedelta(minutes=10)  # an email still queued or failed this long after its last change is picked up again
SWEEP_MAX_ATTEMPTS = 8


# --- Provider and rendering ------------------------------------------------------------------------------------

def get_active_provider():
    return EmailProvider.objects.filter(is_active=True).first()


def _connection_and_sender():
    provider = get_active_provider()
    if provider is None:
        return get_connection(), settings.DEFAULT_FROM_EMAIL
    return _provider_connection(provider), provider.from_email


def _provider_connection(provider):
    return get_connection(
        "apps.notifications.backends.EnvelopeSMTPBackend",
        host=provider.host,
        port=provider.port,
        username=provider.username or None,
        password=provider.get_password() or None,
        use_tls=provider.use_tls,
        use_ssl=provider.use_ssl,
        timeout=provider.timeout,
        envelope_from=provider.envelope_from or None,
    )


CTA_LABELS = {
    "invoice_issued": "View and pay the invoice", "invoice_due_soon": "Pay the invoice", "invoice_overdue": "Pay the invoice now",
    "quote_sent": "Review the quote", "payment_received": "View the invoice", "payment_refunded": "View the invoice",
    "team_alert": "Open it in the portal",
}
# Emails that carry a PDF of the document they are about, made when the email is sent: an invoice email carries the invoice as it
# stands (unpaid while something is owed, paid once it is settled), a quote email the quote.
INVOICE_PDF_TEMPLATES = {"invoice_issued", "invoice_due_soon", "invoice_overdue", "payment_received", "payment_refunded"}
QUOTE_PDF_TEMPLATES = {"quote_sent"}


def _brand_context():
    from apps.branding import services as branding

    brand = branding.get()
    logo_url = (settings.SITE_URL.rstrip("/") + reverse("brand_logo") + f"?v={brand.version}") if brand.has_logo else ""
    return {"brand": brand, "logo_url": logo_url}


def render_email(template, context):
    """Render ``emails/<template>_subject.txt``, ``.txt`` and optional ``.html``."""
    from apps.branding import services as branding

    ctx = {"site_name": branding.get().name, "site_url": settings.SITE_URL, **_brand_context(), **context}
    invoice = ctx.get("invoice")
    if invoice is not None and getattr(invoice, "pk", None) and "pay_methods" not in ctx:
        from apps.billing.models import PaymentMethod

        if getattr(invoice, "balance_due", 0):  # only while something is owed: where to send the money
            ctx["pay_methods"] = [m for m in PaymentMethod.objects.filter(is_active=True, provider__isnull=True) if m.detail_rows or m.instructions]
    if ctx.get("link") and template in CTA_LABELS:
        ctx["cta_url"] = settings.SITE_URL.rstrip("/") + ctx["link"]
        ctx["cta_label"] = CTA_LABELS[template]
    from . import mail_templates

    override = mail_templates.customised(template)
    if override is not None:  # a Super Admin's own wording; the HTML layout is built from the text
        try:
            subject, body_text, design = mail_templates.render_override(override, ctx)
            if not design:
                return subject, body_text, ""
            # A Super Admin's own design goes inside the branded shell (logo, footer); its own button is part of the design.
            return subject, body_text, render_to_string("emails/_custom.html", {**ctx, "custom_html": design, "heading": "", "page_title": subject, "cta_url": ""})
        except ServiceError:  # the wording no longer renders (say a variable it needed is gone): send the default, never nothing
            logger.exception("Customised wording for %s failed; the default was used", template)
    subject = " ".join(render_to_string(f"emails/{template}_subject.txt", ctx).split())
    ctx["heading"] = subject
    body_text = render_to_string(f"emails/{template}.txt", ctx)
    try:
        body_html = render_to_string(f"emails/{template}.html", ctx)
    except TemplateDoesNotExist:
        body_html = ""
    return subject, body_text, body_html


def _split_cta(text):
    """
    Take the first line that holds an address off the text to become the email's button: ``Label: <address>`` gives the label and
    the address, a line that is only an address gives the address. Returns ``(text without that line, address, label)``.
    """
    lines = text.replace("\r", "").split("\n")
    for index, line in enumerate(lines):
        match = re.fullmatch(r"\s*(?:(?P<label>[^:]{3,60}?)\s*:\s*)?(?P<url>https?://\S+)\s*", line)
        if match:
            label = (match.group("label") or "").strip().rstrip(":")
            rest = lines[:index] + lines[index + 1:]
            return "\n".join(rest), match.group("url"), (label[:1].upper() + label[1:]) if label else "Open the link"
    return text, "", ""


def html_from_text(text, *, heading=""):
    """A safe HTML version of a plain-text email in the branded email layout: escaped, paragraphs kept, links live, the main link a button."""
    from django.template.loader import render_to_string

    body, cta_url, cta_label = _split_cta(text)
    paragraphs = []
    for block in re.split(r"\n\s*\n", body.strip()):
        block = block.strip()
        if not block:
            continue
        if not any(line.lstrip().startswith(("- ", "* ")) for line in block.splitlines()):
            block = " ".join(part.strip() for part in block.splitlines())  # a hard-wrapped paragraph flows as one
        escaped = html.escape(block)
        linked = URL_RE.sub(lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', escaped)
        paragraphs.append(f'<p style="margin:0 0 14px 0">{linked.replace(chr(10), "<br>")}</p>')
    return render_to_string("layouts/email.html", {**_brand_context(), "heading": heading, "body": "".join(paragraphs),
                                                     "cta_url": cta_url, "cta_label": cta_label})


def _with_open_pixel(body_html, token):
    if not settings.EMAIL_OPEN_TRACKING:
        return body_html
    pixel = (f'<img src="{settings.SITE_URL.rstrip("/")}{reverse("email_open_pixel", args=[token])}" width="1" height="1" alt="" '
             'style="display:none">')
    return body_html.replace("</body>", pixel + "</body>", 1) if "</body>" in body_html else body_html + pixel


def log_event(message, kind, detail=""):
    """Add a step to an email's timeline (what the log's detail page shows, oldest first)."""
    return EmailEvent.objects.create(message=message, kind=kind, detail=detail[:500])


def _track_links(message):
    """Send the links in the HTML body through our click page (one stored, numbered link each), so clicks can be counted."""
    if not settings.EMAIL_CLICK_TRACKING or message.is_sensitive or not message.body_html:
        return

    def replace(match):
        link = EmailLink.objects.create(message=message, url=html.unescape(match.group(2)))
        target = settings.SITE_URL.rstrip("/") + reverse("email_click", args=[message.track_token, link.pk])
        return f"{match.group(1)}{target}{match.group(3)}"

    message.body_html = HREF_RE.sub(replace, message.body_html)
    message.save(update_fields=["body_html", "updated_at"])


def _enqueue(message_id):
    """
    Hand the email to the delivery task. With a worker (Redis) it is queued and retried with back-off. Without one (local
    development runs tasks inline), the attempt happens here and a failure never breaks the page that caused it: the row keeps
    the error and can be sent again from the email log.
    """
    from .tasks import deliver_email

    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False) and not getattr(settings, "CELERY_TASK_EAGER_PROPAGATES", False):
        try:
            deliver(message_id)
        except Exception:  # noqa: BLE001 - already recorded on the message
            logger.info("Email %s was not delivered now; it stays in the log to send again.", message_id)
        return
    deliver_email.delay(message_id)


# --- Sending ----------------------------------------------------------------------------------------------------

def send_email(*, to_email, template, context=None, user=None, event=""):
    """Record an email and queue it for delivery once the current transaction commits."""
    subject, body_text, body_html = render_email(template, context or {})
    body_html = body_html or html_from_text(body_text, heading=subject)
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
    attach = {}
    document = (context or {}).get("invoice") if template in INVOICE_PDF_TEMPLATES else (context or {}).get("quote") if template in QUOTE_PDF_TEMPLATES else None
    if document is not None and getattr(document, "pk", None):
        attach = {"attachment_kind": "invoice" if template in INVOICE_PDF_TEMPLATES else "quote", "attachment_id": document.pk}
    message = EmailMessage.objects.create(user=user, event=event, template=template, to_email=to_email,
                                          subject=subject, track_token=token, **fields, **attach)
    _track_links(message)
    log_event(message, EmailEvent.Kind.QUEUED)
    transaction.on_commit(lambda: _enqueue(message.pk))
    return message


def _content(message):
    if message.is_sensitive:
        if not message.sensitive_body:
            raise ServiceError("The secret content of this email has been removed, so it cannot be sent again.",
                               code="content_removed", status_code=http.HTTP_409_CONFLICT)
        data = json.loads(crypto.decrypt(message.sensitive_body))
        return data["text"], data["html"]
    return message.body_text, message.body_html


def _inline_logo(body_html):
    """
    Put the logo inside the email (a picture the message carries) instead of a web address the reader's mail program must fetch: it
    shows even when the portal is not reachable from the internet, and when the mail program blocks remote pictures.
    Returns (html with the picture's address swapped, the picture part or None). Never raises: without it the address stays.
    """
    if not body_html or "brand/logo/" not in body_html:
        return body_html, None
    try:
        from email.mime.image import MIMEImage

        from apps.branding import services as branding

        found = branding.image("logo")
        if found is None:
            return body_html, None
        data, content_type = found
        subtype = content_type.split("/")[-1] if content_type.startswith("image/") else "png"
        part = MIMEImage(data, _subtype=subtype)
        part.add_header("Content-ID", "<brand-logo>")
        part.add_header("Content-Disposition", "inline", filename=f"logo.{'jpg' if subtype == 'jpeg' else subtype}")
        swapped = re.sub(r'src="[^"]*brand/logo/[^"]*"', 'src="cid:brand-logo"', body_html)
        return swapped, part
    except Exception:  # noqa: BLE001 - a picture that cannot be embedded never stops the email
        logger.exception("Could not embed the logo in an email")
        return body_html, None


def _attach_pdf(mail, message):
    """
    Attach the invoice or quote PDF the email is about, made now from the stored document: an unpaid invoice goes out marked UNPAID, a
    settled one PAID. A document that cannot be found or drawn never stops the email: it is sent without the file. Returns the name.
    """
    if not message.attachment_kind or not message.attachment_id:
        return ""
    try:
        from apps.billing import pdf
        from apps.billing.models import Invoice, Quote

        if message.attachment_kind == "invoice":
            invoice = Invoice.objects.select_related("client", "order").get(pk=message.attachment_id)
            content = pdf.render_invoice_pdf(invoice)
            state = {"paid": "paid", "partially_paid": "partly-paid", "unpaid": "unpaid"}.get(invoice.status, invoice.status)
            name = f"{invoice.number or invoice.reference}-{state}.pdf"
        else:
            quote = Quote.objects.select_related("client").get(pk=message.attachment_id)
            content = pdf.render_quote_pdf(quote)
            name = f"{quote.number or quote.reference}.pdf"
        mail.attach(name, content, "application/pdf")
        return name
    except Exception:  # noqa: BLE001 - see the docstring
        logger.exception("Could not attach the PDF to email %s", message.pk)
        return ""


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
        message.last_attempt_at = timezone.now()
        message.save(update_fields=["attempts", "last_attempt_at", "updated_at"])

    try:
        body_text, body_html = _content(message)
        body_html, inline_logo = _inline_logo(body_html)
        connection, sender = _connection_and_sender()
        message_id = message.message_id or make_msgid(domain=(sender.rsplit("@", 1)[-1].strip("> ") or None))
        from .models import NotificationSettings

        reply_to = NotificationSettings.load().reply_to
        mail = EmailMultiAlternatives(
            subject=message.subject,
            body=body_text,
            from_email=sender,
            to=[message.to_email],
            connection=connection,
            headers={"Message-ID": message_id},
            reply_to=[reply_to] if reply_to else None,
        )
        if body_html:
            mail.attach_alternative(body_html, "text/html")
        if inline_logo is not None:
            mail.mixed_subtype = "related"  # the logo is part of the page, not a file to download
            mail.attach(inline_logo)
        attachment_name = _attach_pdf(mail, message)
        mail.send(fail_silently=False)
    except Exception as exc:
        message.status = EmailMessage.Status.FAILED
        message.last_error = f"{type(exc).__name__}: {exc}"[:2000]
        message.save(update_fields=["status", "last_error", "updated_at"])
        log_event(message, EmailEvent.Kind.FAILED, f"Attempt {message.attempts}: {message.last_error}")
        logger.warning("Email %s delivery failed (attempt %s): %s", message.pk, message.attempts, message.last_error)
        if message.attempts >= FAILURE_ALERT_AFTER_ATTEMPTS:
            _alert_email_failing(message)
        raise

    message.status = EmailMessage.Status.SENT
    message.from_email = sender
    message.message_id = message_id
    message.attachment_name = attachment_name
    message.delivery = "smtp" if get_active_provider() is not None else "fallback"
    message.sent_at = timezone.now()
    message.last_error = ""
    update = ["status", "from_email", "sent_at", "last_error", "message_id", "delivery", "attachment_name", "updated_at"]
    if message.is_sensitive:  # delivered: the secret is no longer kept anywhere in our database
        message.sensitive_body = ""
        message.body_text = SENSITIVE_PLACEHOLDER
        update += ["sensitive_body", "body_text"]
    message.save(update_fields=update)
    provider = get_active_provider()
    log_event(message, EmailEvent.Kind.SENT, f"Accepted by {provider.host}" if provider is not None
              else "No provider is set up: it went to the fallback and did not leave this machine")
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


# --- Email wording (Super Admin) --------------------------------------------------------------------------------------

@transaction.atomic
def save_template(actor, key, subject, body_text, html="", *, request=None):
    """Super Admin only: use this wording for one email instead of the default. Checked before it is stored."""
    from apps.audit import services as audit

    from . import mail_templates
    from .models import EmailTemplateOverride

    if not actor.is_superuser:
        raise ServiceError("Only a Super Admin can change email wording.", code="permission_denied",
                           status_code=http.HTTP_403_FORBIDDEN)
    subject, body_text, html = mail_templates.validate(key, subject, body_text, html)
    _, created = EmailTemplateOverride.objects.update_or_create(
        template=key, defaults={"subject": subject, "body_text": body_text, "body_html": html, "updated_by": actor})
    audit.record("email_template.saved", actor=actor, metadata={"template": key, "first_time": created, "subject": subject[:120],
                                                                 "characters": len(body_text), "design_characters": len(html)},
                 request=request)


@transaction.atomic
def reset_template(actor, key, *, request=None):
    """Super Admin only: go back to the default wording for one email."""
    from apps.audit import services as audit

    from .models import EmailTemplateOverride

    if not actor.is_superuser:
        raise ServiceError("Only a Super Admin can change email wording.", code="permission_denied",
                           status_code=http.HTTP_403_FORBIDDEN)
    removed, _ = EmailTemplateOverride.objects.filter(template=key).delete()
    if removed:
        audit.record("email_template.reset", actor=actor, metadata={"template": key}, request=request)
    return bool(removed)


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


ALERT_SETTINGS = {
    "order.new": "alert_new_order", "order.failed": "alert_new_order", "payment.reported": "alert_payment_reported",
    "ticket.unassigned": "alert_tickets", "ticket.customer_reply": "alert_tickets", "client.new": "alert_new_client",
    "affiliate.applied": "alert_affiliates", "cancellation.requested_team": "alert_cancellations",
    "invoice.team_update": "alert_invoices", "order.team_update": "alert_order_updates",
}


def _recipients(setting):
    """The alert addresses; with none set, the Super Admins' own email addresses, so an alert is never lost for want of a setting."""
    if setting.addresses:
        return list(setting.addresses)
    from apps.accounts.models import User

    return list(dict.fromkeys(User.objects.filter(is_active=True, is_superuser=True).exclude(email="").values_list("email", flat=True)))


def money(currency, amount):
    return f"{currency} {amount:,.2f}"


def describe_order(order):
    """The order as a few readable lines for a team alert: who, what, how much, how they will pay."""
    client = order.client
    lines = [f"Client: {client.display_name} <{order.billing_email or client.email}>" + (f", {client.phone}" if client.phone else ""),
             f"Order {order.reference}: {order.get_status_display()}"]
    for item in order.items.all().order_by("id"):
        lines.append(f"  - {item.description}" + (f" ({item.domain_name})" if item.domain_name and item.domain_name not in item.description else "")
                     + f": {money(order.currency, item.line_total)}")
    if order.discount_total:
        lines.append(f"Discount: -{money(order.currency, order.discount_total)}" + (f" ({order.coupon_code})" if order.coupon_code else ""))
    if order.tax_total:
        lines.append(f"{order.tax_name or 'Tax'}: {money(order.currency, order.tax_total)}")
    lines.append(f"Total: {money(order.currency, order.total)}")
    if order.payment_method_name:
        lines.append(f"Payment method: {order.payment_method_name}")
    return "\n".join(lines)


def describe_invoice(invoice):
    """The invoice as a few readable lines for a team alert."""
    client = invoice.client
    lines = [f"Client: {client.display_name} <{invoice.billing_email or client.email}>",
             f"Invoice {invoice.number or invoice.reference}: {invoice.display_status_label}"]
    for item in invoice.items.all():
        lines.append(f"  - {item.description}: {money(invoice.currency, item.amount)}")
    lines += [f"Total: {money(invoice.currency, invoice.total)}", f"Paid: {money(invoice.currency, invoice.amount_paid)}",
              f"Balance due: {money(invoice.currency, invoice.balance_due)}"]
    if invoice.due_date:
        lines.append(f"Due: {invoice.due_date:%d %b %Y}")
    return "\n".join(lines)


def team_invoice_update(invoice, what, *, exclude=None):
    """Tell the team an invoice changed (issued, payment received, refunded, cancelled): the bell, and the alert copies. Never raises."""
    from django.urls import reverse

    try:
        notify_team("invoice.team_update", "view_billing", exclude=exclude,
                    title=f"{invoice.number or invoice.reference} {what}: {invoice.client.display_name}",
                    body=describe_invoice(invoice), link=reverse("billing_staff:invoice_detail", args=[invoice.pk]))
    except Exception:  # noqa: BLE001 - an alert must never break the payment or the invoice
        logger.exception("Could not send the invoice alert")


def team_order_update(order, what, *, exclude=None):
    from django.urls import reverse

    try:
        notify_team("order.team_update", "view_orders", exclude=exclude, title=f"Order {order.reference} {what}: {order.client.display_name}",
                    body=describe_order(order), link=reverse("orders_staff:detail", args=[order.pk]))
    except Exception:  # noqa: BLE001
        logger.exception("Could not send the order alert")


def alert_addresses(event, *, title, body="", link=""):
    """Copy an alert to the addresses set in Setup > Email provider (if that kind of alert is switched on). Never raises."""
    from .models import NotificationSettings

    try:
        setting = NotificationSettings.load()
        flag = ALERT_SETTINGS.get(event)
        if not flag or not getattr(setting, flag):
            return []
        return [send_email(to_email=address, template="team_alert", event="team.alert",
                           context={"title": title, "body": body, "body_lines": body.splitlines(), "link": link})
                for address in _recipients(setting)]
    except Exception:  # noqa: BLE001 - an alert copy must never break the action that caused it
        logger.exception("Could not copy the %s alert to the alert addresses", event)
        return []


def notify_team(event, codename, *, title, body="", link="", context=None, exclude=None):
    """A team alert to every staff member holding ``codename`` (each honouring their own preferences), and to the alert addresses."""
    sent = []
    for person in staff_with(codename):
        if exclude is not None and person.pk == getattr(exclude, "pk", None):
            continue
        sent.append(dispatch(event, user=person, title=title, body=body, link=link, context=context))
    alert_addresses(event, title=title, body=body, link=link)
    return sent


def _alternate_copy(event, client, context, *already_sent_to):
    """A client's optional ``alternate_email`` gets its own copy of the same email, unless that address already
    received this one (e.g. it happens to equal the primary address)."""
    address = client.alternate_email
    if not address or address in already_sent_to:
        return []
    return [dispatch(event, email=address, context=context)]


def dispatch_client(event, client, *, title="", body="", link="", context=None, email=None):
    """
    Reach a client about something on their account: every owner and billing contact gets it (each honouring their
    own preferences); if the account has no such contact, the client's own address is emailed instead. Either way,
    the account's optional ``alternate_email`` (a second address kept purely for this - never a login) gets its own
    copy of the email too.

    ``email`` sends the email once to that address (an invoice's billing address, say) and gives the contacts the
    in-app notification only.
    """
    from apps.clients.models import ClientContact, ContactRole

    contacts = list(ClientContact.objects.filter(client=client, role__in=(ContactRole.OWNER, ContactRole.BILLING))
                    .select_related("user"))
    if email:
        sent = [dispatch(event, email=email, context=context)]
        sent += _alternate_copy(event, client, context, email)
        return sent + [dispatch(event, user=c.user, title=title, body=body, link=link, in_app_only=True)
                       for c in contacts]
    if not contacts:
        return [dispatch(event, email=client.email, context=context)] + _alternate_copy(event, client, context, client.email)
    sent = [dispatch(event, user=c.user, title=title, body=body, link=link, context=context) for c in contacts]
    return sent + _alternate_copy(event, client, context, *(c.user.email for c in contacts))


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


def delete_notifications(user, notification_ids=None, *, read_only=False):
    """Delete some of the person's own notifications: the ticked ones, or every one they have already read."""
    qs = Notification.objects.filter(user=user)
    if read_only:
        qs = qs.filter(read_at__isnull=False)
    if notification_ids is not None:
        qs = qs.filter(pk__in=notification_ids)
    count = qs.count()
    qs.delete()
    return count


def safe_link(link):
    """A notification's link, only if it is a path on this site (never an external address)."""
    return link if link.startswith("/") and not link.startswith("//") and "\\" not in link else "/account/notifications/"


# --- Tracking, log, stats ---------------------------------------------------------------------------------------

def record_open(token):
    """Note that the tracking pixel was fetched. A signal, not proof of reading (proxies and scanners fetch it too)."""
    message = EmailMessage.objects.filter(track_token=token).first()
    if message is None:
        return 0
    first = message.opened_at is None
    EmailMessage.objects.filter(pk=message.pk).update(
        open_count=F("open_count") + 1, opened_at=Coalesce("opened_at", Value(timezone.now())))
    if first:
        log_event(message, EmailEvent.Kind.OPENED, "The tracking image was loaded")
    return 1


def record_click(token, link_id):
    """Count a click and return where it goes: only ever an address that was in the email (never one from the request)."""
    link = EmailLink.objects.select_related("message").filter(pk=link_id, message__track_token=token).first()
    if link is None:
        return None
    now = timezone.now()
    EmailLink.objects.filter(pk=link.pk).update(click_count=F("click_count") + 1,
                                                first_clicked_at=Coalesce("first_clicked_at", Value(now)), last_clicked_at=now)
    first = link.message.first_clicked_at is None
    EmailMessage.objects.filter(pk=link.message_id).update(
        click_count=F("click_count") + 1, first_clicked_at=Coalesce("first_clicked_at", Value(now)))
    if first:
        log_event(link.message, EmailEvent.Kind.CLICKED, link.url[:300])
    return link.url


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
        clicked=Count("id", filter=Q(status=EmailMessage.Status.SENT, is_sensitive=False, first_clicked_at__isnull=False)),
    ).order_by("-total"))

    def rate(opened, tracked):
        return round(100 * opened / tracked, 1) if tracked else None

    by_event = [{**row, "event_label": events.EVENTS[row["event"]].label if row["event"] in events.EVENTS
                 else (row["event"] or "(other)"), "open_rate": rate(row["opened"], row["tracked"]),
                 "click_rate": rate(row["clicked"], row["tracked"])} for row in rows]
    totals = {key: sum(r[key] for r in by_event) for key in ("total", "sent", "failed", "queued", "tracked", "opened", "clicked")}
    return {**totals, "open_rate": rate(totals["opened"], totals["tracked"]),
            "click_rate": rate(totals["clicked"], totals["tracked"]), "days": days, "by_event": by_event,
            "tracking_enabled": settings.EMAIL_OPEN_TRACKING, "click_tracking_enabled": settings.EMAIL_CLICK_TRACKING,
            "no_provider": get_active_provider() is None,
            "fallback_sent": EmailMessage.objects.filter(created_at__gte=since, delivery="fallback").count()}


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
    log_event(message, EmailEvent.Kind.RESENT, f"By {actor.email}")
    audit.record("email.resent", actor=actor, target=message, metadata={"to": message.to_email}, request=request)
    transaction.on_commit(lambda: _enqueue(message.pk))
    return message


def _require_settings(actor):
    if not actor.has_perm(perm("manage_settings")):
        raise ServiceError("You do not have permission to perform this action.", code="permission_denied",
                           status_code=http.HTTP_403_FORBIDDEN)


@transaction.atomic
def delete_email(actor, message, *, request=None):
    """Staff (``manage_settings``): remove an email from the log. Its timeline and link counts go with it; an email still
    waiting to be sent cannot be removed (send it or wait), so nothing that was meant to go out disappears."""
    from apps.audit import services as audit

    _require_settings(actor)
    message = EmailMessage.objects.select_for_update().get(pk=message.pk)
    if message.status == EmailMessage.Status.QUEUED:
        raise ServiceError("This email is still waiting to be sent. Wait for the attempt, or delete it once it has "
                           "been sent or has failed.", code="email_queued")
    audit.record("email.deleted", actor=actor, metadata={"to": message.to_email, "subject": message.subject[:120],
                                                          "status": message.status, "event": message.event}, request=request)
    message.delete()


@transaction.atomic
def purge_emails(actor, *, older_than_days, status="", request=None):
    """
    Staff (``manage_settings``): delete the sent or failed emails older than N days (at least 7), optionally only one status,
    to keep the log small. Queued emails are never touched. Returns how many were deleted; the count is audited.
    """
    from apps.audit import services as audit

    _require_settings(actor)
    if not isinstance(older_than_days, int) or older_than_days < 7:
        raise ServiceError("Choose 7 days or more, so recent history is never lost by accident.", code="too_recent")
    if status and status not in (EmailMessage.Status.SENT, EmailMessage.Status.FAILED):
        raise ServiceError("You can delete sent or failed emails only.", code="invalid_status")
    doomed = EmailMessage.objects.filter(created_at__lt=timezone.now() - timedelta(days=older_than_days)).exclude(
        status=EmailMessage.Status.QUEUED)
    if status:
        doomed = doomed.filter(status=status)
    count = doomed.count()
    doomed.delete()  # the timeline and link rows go with each message
    audit.record("email.purged", actor=actor, metadata={"older_than_days": older_than_days, "status": status or "any",
                                                         "emails": count}, request=request)
    return count


def sweep_stuck(*, now=None):
    """
    Run every few minutes: pick up emails that are still queued or failed a while after their last change (a worker was down, a
    mail server was slow, the process ended mid-send) and try them again, up to ``SWEEP_MAX_ATTEMPTS`` tries in all.
    """
    cutoff = (now or timezone.now()) - SWEEP_AFTER
    stuck = EmailMessage.objects.filter(status__in=(EmailMessage.Status.QUEUED, EmailMessage.Status.FAILED),
                                        updated_at__lt=cutoff, attempts__lt=SWEEP_MAX_ATTEMPTS)
    retried = 0
    for message in stuck[:200]:
        if message.is_sensitive and not message.sensitive_body:
            continue  # the secret was wiped: it can never be sent, so leave it for staff
        log_event(message, EmailEvent.Kind.RESENT, "Automatic retry")
        _enqueue(message.pk)
        retried += 1
    return retried


def purge_sensitive(*, now=None):
    """Remove the secret content of emails that never got delivered (run daily). Returns how many were wiped."""
    cutoff = (now or timezone.now()) - SENSITIVE_KEEP_FAILED
    stale = EmailMessage.objects.filter(is_sensitive=True, status__in=(EmailMessage.Status.FAILED,
                                                                        EmailMessage.Status.QUEUED),
                                        created_at__lt=cutoff).exclude(sensitive_body="")
    return stale.update(sensitive_body="", body_text=SENSITIVE_PLACEHOLDER)


# --- The email provider (Setup > Email Provider) ---------------------------------------------------------------------

PROVIDER_FIELDS = ("name", "kind", "host", "port", "username", "use_tls", "use_ssl", "timeout", "from_email",
                   "envelope_from")


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
        connection = _provider_connection(provider)
        sent = EmailMultiAlternatives(f"{name}: test email", f"This is a test message from {name}. If you can read it, "
                                      "your email provider settings work.", provider.from_email, [to_email],
                                      connection=connection).send()
        ok = bool(sent)
        detail = ("The mail server accepted it. Accepted is not the same as delivered: if it does not arrive, look in Spam "
                  "and run \"Check deliverability\" for this provider." if sent else "The server accepted no message.")
    except Exception as exc:  # noqa: BLE001 - the point is to report why sending failed
        ok, detail = False, f"{type(exc).__name__}: {str(exc)[:200]}"
        if provider.get_password() and provider.get_password() in detail:
            detail = type(exc).__name__  # never echo a credential
    audit.record("email_provider.tested", actor=actor, target=provider, metadata={"ok": ok, "to": to_email}, request=request)
    return ok, detail
