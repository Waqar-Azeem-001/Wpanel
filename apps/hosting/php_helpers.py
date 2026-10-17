"""
The small one-file PHP helpers WHPanel writes into a customer's website (installer, scanner, WordPress tool, copier, server tools).

Each already has a random name, checks a secret token with ``hash_equals`` and deletes itself the moment it is used. This module adds two more guards:

* **An expiry written into the file.** A helper that is not called within ``LIFETIME_SECONDS`` (the call never came, the panel was down) deletes itself the first time anyone
  asks for it, and refuses to do anything.
* **A nightly sweep** that deletes any ``whp-*.php`` helper still lying around in a website's folder after ``SWEEP_AFTER_SECONDS``, so a forgotten one cannot wait for a stranger.
"""
import logging
import re
import time
from datetime import datetime, timezone

from django.core.exceptions import ObjectDoesNotExist

LIFETIME_SECONDS = 15 * 60
SWEEP_AFTER_SECONDS = 60 * 60
HELPER_NAME = re.compile(r"^whp-(wp|scan|clone|tool|install)-[0-9a-f]+\.php$")

logger = logging.getLogger(__name__)

_CONST = "const WHP_TOKEN_HASH = '__TOKEN_HASH__';"
_CHECK = "if (!hash_equals(WHP_TOKEN_HASH,"


def harden(php):
    """A helper's PHP template with an expiry check added in front of the token check (idempotent). ``__EXPIRES__`` is filled in by ``fill``."""
    if "WHP_EXPIRES" in php:
        return php
    assert _CONST in php and _CHECK in php, "not a WHPanel helper template"
    php = php.replace(_CONST, _CONST + "\nconst WHP_EXPIRES = __EXPIRES__;", 1)
    return php.replace(_CHECK, "if (time() > WHP_EXPIRES) { @unlink(__FILE__); http_response_code(410); echo '{\"ok\":false}'; exit; }\n" + _CHECK, 1)


def fill(php, now=None):
    """The template with its expiry guard added and the expiry time filled in, ready for the token and result names."""
    return harden(php).replace("__EXPIRES__", str(int(now if now is not None else time.time()) + LIFETIME_SECONDS))


def _age(modified_at, now):
    """Seconds since the file changed; a file with no usable time counts as old (it is a helper, and not one to keep)."""
    try:
        moment = datetime.fromisoformat(modified_at)
    except (TypeError, ValueError):
        return SWEEP_AFTER_SECONDS + 1
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return now - moment.timestamp()


def sweep_account(account, *, now=None):
    """Delete helpers older than ``SWEEP_AFTER_SECONDS`` in the account's website folders. Returns how many were removed."""
    from apps.core.exceptions import ServiceError

    from . import site_apps
    from .adapters.capabilities import Capability
    from .care import _Io
    from .models import WebsiteApp

    now = now if now is not None else time.time()
    io = _Io(None, account)
    domains = [""] + [a.domain for a in WebsiteApp.objects.filter(account=account).exclude(domain="")]
    removed = 0
    for domain in domains:
        try:
            folder = site_apps.docroot_path(account, domain)
            entries = io.call(Capability.FILES, "list_files", folder)
        except (ServiceError, ObjectDoesNotExist):
            continue
        for entry in entries:
            if entry.is_dir or not HELPER_NAME.match(entry.name):
                continue
            if _age(entry.modified_at, now) >= SWEEP_AFTER_SECONDS:
                try:
                    io.call(Capability.FILES, "delete_path", entry.path)
                    removed += 1
                except ServiceError:
                    logger.info("could not remove leftover helper %s on account %s", entry.name, account.pk)
    return removed


def sweep_all():
    from .models import HostingAccount, HostingStatus

    total = 0
    for account in HostingAccount.objects.filter(status=HostingStatus.ACTIVE).select_related("server"):
        try:
            total += sweep_account(account)
        except Exception:  # noqa: BLE001 - one broken server must not stop the rest
            logger.info("helper sweep skipped account %s", account.pk)
    return total
