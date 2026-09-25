"""
The shared presentation vocabulary (roadmap Sections 9.3 and 9.4): one status badge, one way to write money, dates and
form fields. Templates use these instead of hand-colouring or hand-formatting, so the whole portal changes together.
"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django import forms, template
from django.utils import timezone
from django.utils.html import format_html

from apps.branding import services as branding

register = template.Library()

# --- Status badges (v2 Section 9.3): the colour of a status is decided here, once ------------------------------------
TONES = {
    "success": {"active", "paid", "resolved", "approved", "completed", "registered", "succeeded", "accepted", "done",
                "sent", "ok", "verified", "enabled"},
    "warning": {"pending", "pending_payment", "processing", "provisioning", "partially_paid", "customer_reply",
                "renewal_due", "grace", "queued", "pending_registration", "scheduled", "awaiting_review"},
    "danger": {"unpaid", "overdue", "fraud", "failed", "suspended", "rejected", "expired", "declined",
               "needs_attention", "error"},
    "info": {"open", "agent_reply", "in_progress", "pending_transfer_in", "draft", "new", "unread"},
    "neutral": {"cancelled", "terminated", "closed", "refunded", "collections", "withdrawn", "inactive", "void",
                "archived"},
}
TONE_OF = {status: tone for tone, statuses in TONES.items() for status in statuses}


def tone_of(value):
    key = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return TONE_OF.get(key, "neutral"), key


@register.simple_tag
def status_badge(value, label=None):
    """``{% status_badge obj.status obj.get_status_display %}``: the label is always shown, colour never the only signal."""
    tone, key = tone_of(value)
    text = label or key.replace("_", " ").capitalize() or "-"
    return format_html('<span class="status-badge is-{} status-{}">{}</span>', tone, key or "unknown", text)


# --- Money and dates, written the way the brand settings say -----------------------------------------------------------

def _number(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


@register.filter
def money(value, currency=""):
    """``{{ invoice.total|money:invoice.currency }}`` -> "USD 1,234.50" (or the format chosen in Brand settings)."""
    amount = _number(value)
    if amount is None:
        return value if value not in (None, "") else "-"
    text = f"{amount:,.2f}"
    style = branding.get().money_format
    if not currency or style == "plain":
        return text
    return f"{text} {currency}" if style == "code_after" else f"{currency} {text}"


@register.filter
def fdate(value):
    """A date (or the date of a moment, in the site's time zone) in the brand's format."""
    if not value:
        return "-"
    if isinstance(value, datetime):
        value = timezone.localtime(value) if timezone.is_aware(value) else value
    return value.strftime(branding.get().date_format) if isinstance(value, (date, datetime)) else str(value)


@register.filter
def fdatetime(value):
    if not value:
        return "-"
    if isinstance(value, datetime):
        value = timezone.localtime(value) if timezone.is_aware(value) else value
        return value.strftime(f"{branding.get().date_format} %H:%M")
    return fdate(value)


# --- Form fields -------------------------------------------------------------------------------------------------------

@register.filter
def bs_field(field):
    """Render a bound field with the Bootstrap class for its widget, the invalid state and an accessible error link."""
    widget = field.field.widget
    if isinstance(widget, forms.HiddenInput) or isinstance(widget, (forms.RadioSelect, forms.CheckboxSelectMultiple)):
        return field
    if isinstance(widget, forms.CheckboxInput):
        css = "form-check-input"
    elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
        css = "form-select"
    else:
        css = "form-control"
    attrs = {"class": " ".join(filter(None, [widget.attrs.get("class", ""), css, "is-invalid" if field.errors else ""]))}
    if field.errors:
        attrs["aria-invalid"] = "true"
        attrs["aria-describedby"] = f"{field.auto_id}_errors"
    return field.as_widget(attrs=attrs)


@register.filter
def is_checkbox(field):
    return isinstance(field.field.widget, forms.CheckboxInput)


@register.filter
def feature_lines(text):
    """A plan's description as a list: one feature per non-empty line."""
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


@register.filter
def cell(value):
    """A table cell's value in the site's formats: dates, moments and money as the brand writes them, nothing as a dash."""
    from decimal import Decimal

    if value is None or value == "":
        return "—"
    if isinstance(value, datetime):
        return fdatetime(value)
    if isinstance(value, date):
        return fdate(value)
    if isinstance(value, Decimal):
        return money(value)
    return value
