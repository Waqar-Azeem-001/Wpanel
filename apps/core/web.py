"""Small helpers shared by server-rendered views (used by apps.billing and apps.orders)."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect

from .exceptions import ServiceError

ACTION_ERRORS = (ServiceError, ValidationError)


def error_text(exc):
    """A customer-safe one-line message for a service or validation error."""
    if isinstance(exc, ServiceError):
        return exc.message
    return "; ".join(exc.messages)


def apply_form_error(form, exc):
    """Attach a service/validation error to a form (field-level where possible)."""
    if isinstance(exc, ValidationError) and hasattr(exc, "error_dict"):
        for field, errors in exc.message_dict.items():
            form.add_error(field if field in form.fields else None, errors)
    elif isinstance(exc, ValidationError):
        form.add_error(None, exc)
    else:
        form.add_error(None, exc.message)


def query_id(request, name):
    """A positive whole number from the query string, or None. Missing or junk ("abc", "-1", a 30-digit number) counts as
    "not given": a hostile or mistyped link must never become a server error."""
    raw = (request.GET.get(name) or "").strip()
    return int(raw) if raw.isdigit() and len(raw) <= 9 else None


def run_action(request, action, view_name, **redirect_kwargs):
    """
    Run a POST action (``action`` takes no args and returns a success message or
    None), flash the outcome, and redirect to ``view_name``.
    """
    try:
        message = action()
    except ACTION_ERRORS as exc:
        messages.error(request, error_text(exc))
    else:
        if message:
            messages.success(request, message)
    return redirect(view_name, **redirect_kwargs)
