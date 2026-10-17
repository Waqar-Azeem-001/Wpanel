"""
Telling customers before something they have runs out: a domain, a hosting plan, an SSL certificate.

A daily job. Each warning is sent once per thing, per expiry date and per step (30, 14, 7 and 1 days before; 14 and 7 for a
certificate), so a second run never repeats it, and a renewal (a new expiry date) starts the steps again. A domain or plan with
automatic renewal says that it will renew by itself; the others say how to renew in one click from the page the link opens.
"""
import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.core.exceptions import ServiceError
from apps.domains.models import Domain, DomainStatus
from apps.hosting.models import HostingAccount, HostingStatus
from apps.notifications import services as notifications

from .models import ExpiryNotice

logger = logging.getLogger(__name__)

STEPS = (30, 14, 7, 1)
SSL_STEPS = (14, 7)
SSL_ACCOUNTS_PER_RUN = 200


def _step(days_left, steps):
    """The smallest step the thing has reached (``days_left`` at or below it), or None. 20 days left is the 30 step; 5 days left is the 7 step."""
    reached = [s for s in steps if days_left <= s]
    return min(reached) if reached else None


def _once(subject, step):
    """Record that this warning is being sent; False when it was already sent (or another run just did)."""
    try:
        with transaction.atomic():
            ExpiryNotice.objects.create(subject=subject, step=step)
        return True
    except IntegrityError:
        return False


def _send(client, *, kind, name, ends, days, auto, url):
    when = f"in {days} days" if days > 1 else ("tomorrow" if days == 1 else "today")
    if auto:
        text = "It is set to renew automatically: we will send the renewal invoice and you only need to pay it."
    else:
        text = "Renew it in one click from the page below so it does not stop working."
    notifications.dispatch_client(
        "service.expiring", client, title=f"{name} {kind} ends {when}", body=text, link=url,
        context={"kind": kind, "name": name, "ends": ends, "when": when, "advice": text, "link": url})


def send_expiry_notices(*, now=None):
    now = now or timezone.now()
    sent = {"domain": 0, "hosting": 0, "ssl": 0}
    for domain in Domain.objects.filter(status=DomainStatus.ACTIVE, expires_at__isnull=False, expires_at__gt=now,
                                        expires_at__lte=now + timedelta(days=max(STEPS))).select_related("client"):
        days = max((domain.expires_at - now).days, 0)
        step = _step(days, STEPS)
        if step and _once(f"domain:{domain.pk}:{domain.expires_at:%Y-%m-%d}", step):
            _send(domain.client, kind="domain", name=domain.name, ends=f"{domain.expires_at:%d %b %Y}", days=days, auto=domain.auto_renew,
                  url=reverse("domains_customer:renew_tab", args=[domain.pk]))
            sent["domain"] += 1
    for account in HostingAccount.objects.filter(status=HostingStatus.ACTIVE, expires_at__isnull=False, expires_at__gt=now,
                                                 expires_at__lte=now + timedelta(days=max(STEPS))).select_related("client"):
        days = max((account.expires_at - now).days, 0)
        step = _step(days, STEPS)
        if step and _once(f"hosting:{account.pk}:{account.expires_at:%Y-%m-%d}", step):
            _send(account.client, kind="hosting plan", name=account.domain, ends=f"{account.expires_at:%d %b %Y}", days=days, auto=False,
                  url=reverse("hosting_customer:detail", args=[account.pk]))
            sent["hosting"] += 1
    sent["ssl"] = _ssl_notices(now)
    return sent


def _ssl_notices(now):
    """Certificates that end soon, read from the server (only for plans whose server can report them). One slow or broken server never stops the rest."""
    from apps.hosting import control
    from apps.core.system import SYSTEM

    count = 0
    for account in HostingAccount.objects.filter(status=HostingStatus.ACTIVE).select_related("client", "server")[:SSL_ACCOUNTS_PER_RUN]:
        try:
            if "ssl" not in control.capabilities_for(account):
                continue
            rows = control.perform(SYSTEM, account, "ssl", "list_ssl_status")
        except (ServiceError, Exception):  # noqa: BLE001 - a server that does not answer is not ours to fix here
            logger.info("expiry notices: could not read SSL for %s", account.pk)
            continue
        for row in rows:
            if row.status not in ("expiring", "expired") or row.days_left is None or row.days_left < 0:
                continue
            step = _step(row.days_left, SSL_STEPS)
            if step and _once(f"ssl:{account.pk}:{row.domain}:{(row.expires_at or '')[:10]}", step):
                _send(account.client, kind="SSL certificate", name=row.domain, ends=(row.expires_at or "")[:10], days=row.days_left,
                      auto=bool(row.auto_renew), url=reverse("hosting_customer:ssl", args=[account.pk]))
                count += 1
    return count
