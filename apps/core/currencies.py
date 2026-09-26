"""
The currencies the business bills in (owner: USD, PKR and SAR).

A client has a default currency; staff may pick another one on a draft invoice or quote before it is shared. Amounts are never
converted between currencies: the prices typed on the document are in the currency chosen for it, and an issued document keeps
its currency for good.
"""
from django.core.exceptions import ValidationError

CURRENCIES = (
    ("USD", "US Dollar (USD)"),
    ("PKR", "Pakistani Rupee (PKR)"),
    ("SAR", "Saudi Riyal (SAR)"),
)
CODES = tuple(code for code, _ in CURRENCIES)
DEFAULT = "USD"


def choices(current=""):
    """The choices for a form field; a value already stored outside the list (old data) stays selectable."""
    current = (current or "").strip().upper()
    if current and current not in CODES:
        return [(current, f"{current} (no longer offered)"), *CURRENCIES]
    return list(CURRENCIES)


def clean(value, *, keep=""):
    """A supported currency code, upper-cased. ``keep`` is a value already stored, which is allowed to stay unchanged."""
    code = (value or "").strip().upper()
    if code in CODES or (keep and code == keep):
        return code
    raise ValidationError(f"Choose one of: {', '.join(CODES)}.", code="invalid_currency")
