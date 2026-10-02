"""
Outbound "something changed, please refetch" pings (Step 11 of the owner's spec - deliberately a notify-only
signal, never a payload carrying catalog data: the plugin always refetches from the read API in ``apps.api``,
the one source of truth, rather than trusting what a webhook body says).

Two kinds of failure, so the Celery task (``apps/storefronts/tasks.py``) knows which ones are worth retrying:
``WebhookConfigError`` (bad/unsafe URL, DNS failure, no signing secret) will not be fixed by trying again;
anything else (a network error, a non-2xx response) might be, and is retried with backoff.
"""
import hashlib
import hmac
import ipaddress
import json
import socket
import time
from urllib.parse import urlparse

import requests

TIMEOUT_SECONDS = 5
SIGNATURE_HEADER = "X-Storefront-Signature"


class WebhookError(Exception):
    """Base for every webhook delivery failure."""


class WebhookConfigError(WebhookError):
    """Permanent - the URL, or the storefront's configuration, is the problem, not the network."""


class WebhookDeliveryError(WebhookError):
    """Transient - a network failure or a non-2xx response; worth retrying."""


def _resolved_ips(hostname):
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise WebhookConfigError(f"Could not resolve {hostname}.") from exc
    return {info[4][0] for info in infos}


def assert_safe_webhook_url(url):
    """SSRF guard (Step 14): checked again right before every send, not only when the URL was saved, since a
    DNS record a staff member trusted when they typed it can change at any time afterwards."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise WebhookConfigError("Webhook URL must be an https address.")
    for ip in _resolved_ips(parsed.hostname):
        addr = ipaddress.ip_address(ip)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved or \
           addr.is_multicast or addr.is_unspecified:
            raise WebhookConfigError(f"Webhook URL resolves to a non-public address ({ip}).")


def sign(secret, body, timestamp=None):
    """Same ``t=<unix time>,v1=<hmac-sha256 hex>`` shape as the project's existing gateway-webhook signing
    (``apps.billing.gateways``) - one convention for "how this project proves it sent something", not two."""
    timestamp = int(timestamp if timestamp is not None else time.time())
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def send(delivery):
    """One delivery attempt for ``delivery`` (a StorefrontWebhookDelivery). Updates and saves it, and re-raises
    on any failure so the caller's retry policy (the Celery task) decides what happens next."""
    from .models import WebhookDeliveryStatus

    storefront = delivery.storefront
    delivery.attempts += 1
    try:
        assert_safe_webhook_url(storefront.webhook_url)
        secret = storefront.get_webhook_secret()
        if not secret:
            raise WebhookConfigError("This storefront has no webhook signing secret configured.")
        body = json.dumps({"event": delivery.event, "storefront": storefront.public_id}).encode()
        headers = {"Content-Type": "application/json", SIGNATURE_HEADER: sign(secret, body)}
        try:
            response = requests.post(storefront.webhook_url, data=body, headers=headers, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise WebhookDeliveryError(str(exc)) from exc
        delivery.response_status = response.status_code
        if not response.ok:
            raise WebhookDeliveryError(f"Storefront responded with HTTP {response.status_code}.")
    except WebhookError as exc:
        delivery.status = WebhookDeliveryStatus.FAILED
        delivery.error = str(exc)[:255]
        delivery.save(update_fields=["attempts", "status", "error", "response_status", "updated_at"])
        raise
    delivery.status = WebhookDeliveryStatus.DELIVERED
    delivery.error = ""
    delivery.save(update_fields=["attempts", "status", "error", "response_status", "updated_at"])
    return delivery
