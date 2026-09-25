"""
Payment gateway adapters (roadmap: PaymentService -> PaymentProvider -> Gateway Adapter).

Everything gateway-specific lives behind ``PaymentAdapter``: how to start a
payment, how to verify and read a webhook, and how to refund. The rest of
billing never mentions a gateway by name, so connecting a real one is one new
adapter class plus one ``PaymentProvider.Kind`` choice.

The shipped ``TestGatewayAdapter`` simulates a hosted checkout and signs its
own webhooks (Stripe-style ``t=<unix time>,v1=<HMAC-SHA256>``), so the real
verification, idempotency and amount-checking code is exercised end to end
without moving money. It refuses to run unless ``ALLOW_TEST_PAYMENT_GATEWAY``.
"""
import hashlib
import hmac
import json
import secrets
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.urls import reverse

from .models import PaymentProvider

SIGNATURE_HEADER = "X-Test-Signature"
SIGNATURE_TOLERANCE_SECONDS = 300


class PaymentError(Exception):
    """The gateway could not do what was asked (message is safe to show)."""


class WebhookVerificationError(Exception):
    """The webhook is not authentic (bad or missing signature, stale timestamp, malformed body)."""


@dataclass
class PaymentSession:
    external_id: str
    redirect_url: str


@dataclass
class PaymentEvent:
    event_id: str
    type: str  # "payment.succeeded" | "payment.failed" | anything else (ignored)
    external_id: str = ""
    amount: Decimal = None
    currency: str = ""
    reason: str = ""


def header_value(headers, name):
    """Case-insensitive header lookup that works for Django's request.headers and plain dicts."""
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return ""


class PaymentAdapter(ABC):
    def __init__(self, provider):
        self.provider = provider

    @abstractmethod
    def create_payment(self, *, reference, amount, currency, return_url):
        """Start a payment; returns a ``PaymentSession`` (raise ``PaymentError`` on failure)."""

    @abstractmethod
    def checkout_url(self, external_id):
        """Where to send the customer to pay an existing (pending) payment."""

    @abstractmethod
    def parse_webhook(self, body, headers):
        """Verify the webhook and return a ``PaymentEvent`` (raise ``WebhookVerificationError``)."""

    @abstractmethod
    def refund(self, *, external_id, amount, currency):
        """Refund part or all of a payment; returns the gateway's refund id (raise ``PaymentError``)."""


class TestGatewayAdapter(PaymentAdapter):
    __test__ = False  # not a pytest class

    def create_payment(self, *, reference, amount, currency, return_url):
        external_id = "tgw_" + secrets.token_urlsafe(18)
        return PaymentSession(external_id=external_id, redirect_url=self.checkout_url(external_id))

    def checkout_url(self, external_id):
        return reverse("billing_customer:test_gateway", args=[external_id])

    def sign(self, body, timestamp=None):
        """The signature header value for ``body`` (used by the simulated hosted page and by tests)."""
        secret = self.provider.get_webhook_secret()
        if not secret:
            raise PaymentError("This gateway has no webhook secret configured.")
        timestamp = int(timestamp if timestamp is not None else time.time())
        digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
        return f"t={timestamp},v1={digest}"

    def simulate_event(self, event_type, external_id, amount, currency, reason=""):
        """(body, headers) of a correctly signed event - what this gateway's hosted page "sends" to us."""
        body = json.dumps({"id": "evt_" + secrets.token_urlsafe(12), "type": event_type,
                           "data": {"payment_id": external_id, "amount": str(amount), "currency": currency,
                                    "reason": reason}}).encode()
        return body, {SIGNATURE_HEADER: self.sign(body)}

    def parse_webhook(self, body, headers):
        secret = self.provider.get_webhook_secret()
        if not secret:
            raise WebhookVerificationError("No webhook secret is configured for this provider.")
        header = header_value(headers, SIGNATURE_HEADER)
        parts = dict(part.split("=", 1) for part in header.split(",") if "=" in part)
        try:
            timestamp = int(parts.get("t", ""))
        except ValueError:
            raise WebhookVerificationError("Missing or malformed signature.")
        expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, parts.get("v1", "")):
            raise WebhookVerificationError("Signature does not match.")
        if abs(time.time() - timestamp) > SIGNATURE_TOLERANCE_SECONDS:
            raise WebhookVerificationError("The signature timestamp is outside the allowed window.")
        try:
            payload = json.loads(body)
            data = payload.get("data") or {}
            event = PaymentEvent(event_id=str(payload["id"])[:128], type=str(payload.get("type", ""))[:100],
                                 external_id=str(data.get("payment_id", ""))[:128],
                                 currency=str(data.get("currency", "")).upper(), reason=str(data.get("reason", ""))[:500])
            if data.get("amount") not in (None, ""):
                event.amount = Decimal(str(data["amount"]))
        except (ValueError, KeyError, TypeError, AttributeError, InvalidOperation):
            raise WebhookVerificationError("The event body is malformed.")
        return event

    def refund(self, *, external_id, amount, currency):
        return "trf_" + secrets.token_urlsafe(12)


ADAPTERS = {PaymentProvider.Kind.TEST: TestGatewayAdapter}


def get_adapter(provider):
    """The adapter for ``provider``; raises ``PaymentError`` if the provider can't be used here and now."""
    adapter_class = ADAPTERS.get(provider.kind)
    if adapter_class is None:
        raise PaymentError("This payment gateway is not supported.")
    if provider.kind == PaymentProvider.Kind.TEST and not settings.ALLOW_TEST_PAYMENT_GATEWAY:
        raise PaymentError("The test payment gateway is disabled in this environment.")
    return adapter_class(provider)
