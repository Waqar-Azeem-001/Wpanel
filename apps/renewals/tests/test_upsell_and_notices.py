"""Where the extras are suggested (dashboard to-dos, the renewal invoice) and the warnings before something ends."""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.lifecycle.expiry_notices import send_expiry_notices
from apps.lifecycle.models import ExpiryNotice
from apps.notifications.models import EmailMessage, Notification
from apps.products import services as product_services
from apps.products.models import AddonKind, BillingCycle
from apps.renewals import addon_orders, services

pytestmark = pytest.mark.django_db


def extra(manager, name, kind, price="12.00"):
    addon = product_services.create_addon(manager, {"name": name, "kind": kind})
    product_services.set_addon_status(manager, addon, "active")
    product_services.set_price(manager, addon, billing_cycle=BillingCycle.ANNUAL, price=price)
    return addon


def test_the_dashboard_todos_suggest_whois_privacy_and_ssl_only_once_staff_set_them_up(client, owner, account, domain, manager):
    client.force_login(owner)
    page = client.get(reverse("dashboard")).content.decode()
    assert "add WHOIS privacy to" not in page and "Show visitors a trust seal" not in page
    whois, ssl = extra(manager, "WHOIS Privacy", AddonKind.WHOIS_PRIVACY), extra(manager, "Rapid SSL", AddonKind.SSL)
    page = client.get(reverse("dashboard")).content.decode()
    assert "add WHOIS privacy to example.com" in page and "Show visitors a trust seal: see SSL certificates for example.com" in page
    addon_orders.buy_addon(owner, whois, domain=domain)
    addon_orders.buy_addon(owner, ssl, hosting_account=account)
    page = client.get(reverse("dashboard")).content.decode()
    assert "add WHOIS privacy to" not in page and "see SSL certificates for" not in page  # nothing left to suggest


def test_a_renewal_invoice_suggests_the_extra_for_that_service(client, owner, account, domain, manager):
    extra(manager, "WHOIS Privacy", AddonKind.WHOIS_PRIVACY)
    extra(manager, "Rapid SSL", AddonKind.SSL)
    hosting_renewal = services.create_hosting_renewal(owner, account).invoice
    domain_renewal = services.create_domain_renewal(owner, domain, 1).invoice
    client.force_login(owner)
    hosting_page = client.get(reverse("billing_customer:invoice_detail", args=[hosting_renewal.pk])).content.decode()
    domain_page = client.get(reverse("billing_customer:invoice_detail", args=[domain_renewal.pk])).content.decode()
    assert "While you renew" in hosting_page and "Rapid SSL" in hosting_page and "WHOIS Privacy" not in hosting_page
    assert "While you renew" in domain_page and "WHOIS Privacy" in domain_page and "Rapid SSL" not in domain_page


def test_a_domain_ending_soon_is_warned_once_per_step_and_a_renewal_starts_again(domain):
    domain.expires_at = timezone.now() + timedelta(days=20)
    domain.save(update_fields=["expires_at"])
    assert send_expiry_notices()["domain"] == 1
    assert send_expiry_notices()["domain"] == 0  # the same step is never repeated
    assert EmailMessage.objects.filter(template="service_expiring").count() >= 1
    domain.expires_at = timezone.now() + timedelta(days=5)
    domain.save(update_fields=["expires_at"])
    assert send_expiry_notices()["domain"] == 1  # nearer: the next step
    domain.expires_at = timezone.now() + timedelta(days=400)
    domain.save(update_fields=["expires_at"])
    assert send_expiry_notices()["domain"] == 0  # far away again (renewed): quiet
    assert ExpiryNotice.objects.count() == 2


def test_a_domain_with_auto_renew_says_it_renews_by_itself(domain):
    domain.expires_at, domain.auto_renew = timezone.now() + timedelta(days=10), True
    domain.save(update_fields=["expires_at", "auto_renew"])
    send_expiry_notices()
    assert "renew automatically" in EmailMessage.objects.filter(template="service_expiring").latest("id").body_text


def test_a_plan_far_from_its_end_is_not_warned(account):
    assert send_expiry_notices()["hosting"] == 0  # the fixture's plan has 265 days left


def test_a_plan_ending_soon_is_warned(account):
    account.expires_at = timezone.now() + timedelta(days=6)
    account.save(update_fields=["expires_at"])
    assert send_expiry_notices()["hosting"] == 1
    assert Notification.objects.filter(event="service.expiring").exists()
