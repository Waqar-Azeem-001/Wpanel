"""
Webhook-based cache invalidation (Step 11 of the spec - optional, no longer deferred): signing, the SSRF guard,
one delivery attempt, and the two trigger points (publishing/unpublishing a plan, and a signal when an
already-published plan's own name/description/status/price changes).
"""
import socket
from unittest import mock

import pytest
from django.db import transaction

from apps.accounts.roles import Role
from apps.products import services as product_services
from apps.products.models import CatalogStatus, ProductType
from apps.storefronts import services, webhooks
from apps.storefronts.models import StorefrontWebhookDelivery, WebhookDeliveryStatus

pytestmark = pytest.mark.django_db

PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _commit_callbacks_run_immediately(monkeypatch):
    """Webhook delivery is queued from ``transaction.on_commit`` (a broker outage must not break the save that
    triggered it) - pytest-django's own test transaction is rolled back, not committed, so without this a queued
    delivery would simply never run. Same idiom as ``apps/core/test_crawler.py``'s ``crawled`` fixture."""
    monkeypatch.setattr(transaction, "on_commit", lambda func, using=None, robust=False: func())


@pytest.fixture
def admin(staff):
    return staff(Role.ADMIN)


@pytest.fixture
def storefront(admin):
    row, _secret = services.create_storefront(admin, {
        "name": "Web Host Era UK", "site_url": "https://example.co.uk",
        "webhook_url": "https://uk.example/wp-json/whe/v1/catalog-changed"})
    return row


@pytest.fixture
def wired_storefront(admin, storefront):
    """Fully configured: a webhook URL (from the fixture above) and a generated signing secret."""
    services.rotate_webhook_secret(admin, storefront)
    storefront.refresh_from_db()
    return storefront


@pytest.fixture
def published_product(admin):
    product = product_services.create_product(admin, {"name": "Business Hosting", "type": ProductType.SHARED_HOSTING,
                                                       "publish_externally": True})
    product_services.set_product_status(admin, product, CatalogStatus.ACTIVE)
    return product


def _resolves_to_public_ip(monkeypatch):
    monkeypatch.setattr(webhooks.socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", (PUBLIC_IP, 0))])


def _ok_response():
    return mock.Mock(status_code=200, ok=True)


# --- SSRF guard ------------------------------------------------------------------------------------------------

def test_assert_safe_webhook_url_rejects_plain_http():
    with pytest.raises(webhooks.WebhookConfigError):
        webhooks.assert_safe_webhook_url("http://example.com/hook")


def test_assert_safe_webhook_url_rejects_loopback():
    with pytest.raises(webhooks.WebhookConfigError):
        webhooks.assert_safe_webhook_url("https://127.0.0.1/hook")


def test_assert_safe_webhook_url_rejects_a_private_network_address(monkeypatch):
    monkeypatch.setattr(webhooks.socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("10.0.0.5", 0))])
    with pytest.raises(webhooks.WebhookConfigError):
        webhooks.assert_safe_webhook_url("https://internal.example/hook")


def test_assert_safe_webhook_url_rejects_an_unresolvable_host(monkeypatch):
    def boom(host, port):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(webhooks.socket, "getaddrinfo", boom)
    with pytest.raises(webhooks.WebhookConfigError):
        webhooks.assert_safe_webhook_url("https://nowhere.invalid/hook")


def test_assert_safe_webhook_url_accepts_a_genuinely_public_address(monkeypatch):
    _resolves_to_public_ip(monkeypatch)
    webhooks.assert_safe_webhook_url("https://uk.example/hook")  # does not raise


# --- Signing -----------------------------------------------------------------------------------------------------

def test_sign_matches_the_project_s_own_t_v1_hmac_shape():
    import hashlib
    import hmac

    body = b'{"event": "product.updated"}'
    value = webhooks.sign("s3cret", body, timestamp=1700000000)
    expected = hmac.new(b"s3cret", b"1700000000." + body, hashlib.sha256).hexdigest()
    assert value == f"t=1700000000,v1={expected}"


# --- One delivery attempt ------------------------------------------------------------------------------------------

def test_send_delivers_and_records_a_signed_successful_attempt(wired_storefront, monkeypatch):
    _resolves_to_public_ip(monkeypatch)
    delivery = StorefrontWebhookDelivery.objects.create(storefront=wired_storefront, event="product.updated")
    with mock.patch("apps.storefronts.webhooks.requests.post", return_value=_ok_response()) as posted:
        webhooks.send(delivery)
    delivery.refresh_from_db()
    assert delivery.status == WebhookDeliveryStatus.DELIVERED
    assert delivery.attempts == 1 and delivery.response_status == 200
    assert posted.call_args.kwargs["headers"][webhooks.SIGNATURE_HEADER].startswith("t=")


def test_send_raises_and_records_failure_on_a_non_2xx_response(wired_storefront, monkeypatch):
    _resolves_to_public_ip(monkeypatch)
    delivery = StorefrontWebhookDelivery.objects.create(storefront=wired_storefront, event="product.updated")
    with mock.patch("apps.storefronts.webhooks.requests.post", return_value=mock.Mock(status_code=503, ok=False)):
        with pytest.raises(webhooks.WebhookDeliveryError):
            webhooks.send(delivery)
    delivery.refresh_from_db()
    assert delivery.status == WebhookDeliveryStatus.FAILED and delivery.response_status == 503


def test_send_never_calls_out_for_an_unsafe_url(storefront, admin):
    services.rotate_webhook_secret(admin, storefront)
    storefront.webhook_url = "https://127.0.0.1/hook"
    storefront.save(update_fields=["webhook_url"])
    delivery = StorefrontWebhookDelivery.objects.create(storefront=storefront, event="product.updated")
    with mock.patch("apps.storefronts.webhooks.requests.post") as posted:
        with pytest.raises(webhooks.WebhookConfigError):
            webhooks.send(delivery)
    posted.assert_not_called()


def test_send_without_a_signing_secret_fails_without_calling_out(storefront, monkeypatch):
    _resolves_to_public_ip(monkeypatch)
    delivery = StorefrontWebhookDelivery.objects.create(storefront=storefront, event="product.updated")
    with mock.patch("apps.storefronts.webhooks.requests.post") as posted:
        with pytest.raises(webhooks.WebhookConfigError):
            webhooks.send(delivery)
    posted.assert_not_called()


# --- notify_catalog_changed -------------------------------------------------------------------------------------

def test_notify_is_a_noop_until_both_a_url_and_a_secret_exist(storefront):
    services.notify_catalog_changed(storefront, "product.updated")  # url set, no secret yet
    assert not StorefrontWebhookDelivery.objects.exists()


def test_notify_queues_and_delivers_once_fully_configured(wired_storefront, monkeypatch):
    _resolves_to_public_ip(monkeypatch)
    with mock.patch("apps.storefronts.webhooks.requests.post", return_value=_ok_response()):
        services.notify_catalog_changed(wired_storefront, "product.updated")
    delivery = StorefrontWebhookDelivery.objects.get()
    assert delivery.event == "product.updated" and delivery.status == WebhookDeliveryStatus.DELIVERED


# --- Trigger points (unit-level: did we decide to notify, not the HTTP call itself) ---------------------------------

def test_publishing_a_plan_notifies_its_storefront(admin, wired_storefront, published_product):
    with mock.patch("apps.storefronts.services.notify_catalog_changed") as notify:
        services.publish_product(admin, published_product, wired_storefront)
    notify.assert_called_once_with(wired_storefront, "product.published", request=None)


def test_unpublishing_a_plan_notifies_its_storefront(admin, wired_storefront, published_product):
    services.publish_product(admin, published_product, wired_storefront)
    with mock.patch("apps.storefronts.services.notify_catalog_changed") as notify:
        services.unpublish_product(admin, published_product, wired_storefront)
    notify.assert_called_once_with(wired_storefront, "product.unpublished", request=None)


def test_republishing_with_unchanged_overrides_does_not_notify_again(admin, wired_storefront, published_product):
    services.publish_product(admin, published_product, wired_storefront, featured=True)
    with mock.patch("apps.storefronts.services.notify_catalog_changed") as notify:
        services.publish_product(admin, published_product, wired_storefront, featured=True)
    notify.assert_not_called()


# --- Signals: changing an already-published plan notifies too --------------------------------------------------

def test_saving_an_already_published_product_notifies_its_storefronts(admin, wired_storefront, published_product):
    services.publish_product(admin, published_product, wired_storefront)
    with mock.patch("apps.storefronts.services.notify_catalog_changed") as notify:
        product_services.update_product(admin, published_product, {"description": "Now with more disk space"})
    notify.assert_called_once_with(wired_storefront, "product.updated")


def test_a_new_price_on_an_already_published_product_notifies_its_storefronts(admin, wired_storefront, published_product):
    services.publish_product(admin, published_product, wired_storefront)
    with mock.patch("apps.storefronts.services.notify_catalog_changed") as notify:
        product_services.set_price(admin, published_product, billing_cycle="monthly", price="9.99")
    notify.assert_called_once_with(wired_storefront, "product.updated")


def test_saving_a_product_with_no_storefront_links_notifies_nobody(admin):
    product = product_services.create_product(admin, {"name": "Unlinked Plan", "type": ProductType.SHARED_HOSTING})
    with mock.patch("apps.storefronts.services.notify_catalog_changed") as notify:
        product_services.update_product(admin, product, {"description": "Still nobody's business"})
    notify.assert_not_called()


# --- The signing secret itself never leaks ------------------------------------------------------------------------

def test_webhook_secret_rotation_is_audited_without_ever_recording_the_raw_value(admin, storefront):
    from apps.audit.models import AuditEvent

    raw = services.rotate_webhook_secret(admin, storefront)
    assert AuditEvent.objects.filter(action="storefront.webhook_secret_rotated").exists()
    for event in AuditEvent.objects.filter(target_id=str(storefront.pk)):
        assert raw not in str(event.metadata)
    storefront.refresh_from_db()
    assert storefront.get_webhook_secret() == raw
    assert raw not in storefront.webhook_secret_encrypted  # stored encrypted, not plaintext
