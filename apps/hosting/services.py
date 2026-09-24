"""
Hosting business logic. Staff API, customer API, staff web pages and customer
web pages all call these functions; none of them talks to a hosting adapter
directly (see apps.hosting.adapters for why).

Reliability: provisioning is safe to retry - it checks the account's current
status before calling the adapter, so a repeated call never provisions twice.

A note on ``@transaction.atomic``: functions that call an adapter and, on
failure, must persist a FAILED status/last_error and an audit record before
re-raising are deliberately NOT wrapped in ``@transaction.atomic`` - Django
rolls back an entire atomic block when an exception escapes it, which would
silently discard exactly the failure record staff need to see and retry from.
This is the same bug (and fix) as apps.domains.services; see the Phase 04 gap
report for how it was found.
"""
import secrets

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status

from apps.accounts.roles import perm
from apps.audit import services as audit
from apps.clients.services import contact_role
from apps.core.exceptions import ServiceError
from apps.notifications import services as notifications
from apps.products.models import ServerStatus

from .adapters.base import HostingError
from .adapters.registry import build_adapter
from .models import HostingAccount, HostingStatus


def _denied(message="You do not have permission to perform this action."):
    return ServiceError(message, code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)


def _require(actor, codename):
    if not actor.has_perm(perm(codename)):
        raise _denied()


def can_self_service(user, account):
    """Any contact of the account's client - mirrors apps.domains.services.can_self_service."""
    if user and user.is_authenticated and user.has_perm(perm("manage_hosting")):
        return True
    return bool(user and user.is_authenticated and contact_role(user, account.client) is not None)


def get_adapter(server):
    return build_adapter(server)


def _generate_username(domain):
    """A cPanel-safe username derived from the domain, unique among live accounts."""
    base = "".join(ch for ch in domain.split(".")[0].lower() if ch.isalnum())[:16] or "acct"
    if not base[0].isalpha():
        base = "a" + base[:15]
    candidate = base
    suffix = 2
    while HostingAccount.objects.filter(username=candidate, status__in=("pending", "active", "suspended")).exists():
        tail = str(suffix)
        candidate = f"{base[:16 - len(tail)]}{tail}"
        suffix += 1
    return candidate


def visible_hosting_accounts_for_user(user):
    queryset = HostingAccount.objects.select_related("client", "product", "server")
    if user and user.is_authenticated and user.has_perm(perm("view_hosting")):
        return queryset
    if user and user.is_authenticated:
        return queryset.filter(client__contacts__user=user).distinct()
    return queryset.none()


def search_hosting_accounts(queryset, term):
    term = (term or "").strip()
    if not term:
        return queryset
    return queryset.filter(Q(domain__icontains=term) | Q(username__icontains=term)
                           | Q(client__company_name__icontains=term) | Q(client__email__icontains=term))


# --- Request & provisioning ----------------------------------------------------------------

@transaction.atomic
def request_hosting(actor, client, product, domain, *, server=None, request=None):
    if not (actor.has_perm(perm("manage_hosting")) or contact_role(actor, client) is not None):
        raise _denied()
    if not product.whm_package_name:
        raise ServiceError("This product has no WHM package configured.", code="no_package_configured")

    mapped_servers = list(product.servers.all())
    if server is None and len(mapped_servers) == 1:
        server = mapped_servers[0]
    elif server is not None and mapped_servers and server not in mapped_servers:
        raise ServiceError("This server is not mapped to the selected product.", code="server_not_mapped")

    account = HostingAccount(client=client, product=product, server=server, domain=domain,
                             username=_generate_username(domain), package_name=product.whm_package_name,
                             status=HostingStatus.PENDING)
    account.full_clean()
    account.save()
    audit.record("hosting.requested", actor=actor, target=account,
                 metadata={"client_id": client.pk, "product_id": product.pk}, request=request)
    return account


@transaction.atomic
def assign_server(actor, account, server, *, request=None):
    _require(actor, "manage_hosting")
    if account.status != HostingStatus.PENDING:
        raise ServiceError("A server can only be assigned before provisioning.", code="invalid_status")
    mapped_servers = list(account.product.servers.all())
    if mapped_servers and server not in mapped_servers:
        raise ServiceError("This server is not mapped to the product.", code="server_not_mapped")
    account.server = server
    account.save(update_fields=["server", "updated_at"])
    audit.record("hosting.server_assigned", actor=actor, target=account, metadata={"server": server.name},
                 request=request)
    return account


def complete_provisioning(actor, account, *, request=None):
    """Staff: provision the account against its assigned server. Idempotent."""
    _require(actor, "manage_hosting")
    if account.status == HostingStatus.ACTIVE:
        return account
    if account.status not in (HostingStatus.PENDING, HostingStatus.FAILED):
        raise ServiceError("This account is not pending provisioning.", code="invalid_status")
    if account.server is None:
        raise ServiceError("Assign a server before provisioning.", code="no_server_assigned")
    if account.server.status != ServerStatus.ACTIVE:
        raise ServiceError("The assigned server is not active.", code="server_not_active")

    adapter = get_adapter(account.server)
    raw_password = secrets.token_urlsafe(16)
    try:
        result = adapter.create_account(username=account.username, domain=account.domain,
                                        package=account.package_name, contact_email=account.client.email,
                                        password=raw_password)
    except HostingError as exc:
        account.status, account.last_error = HostingStatus.FAILED, str(exc)
        account.save(update_fields=["status", "last_error", "updated_at"])
        audit.record("hosting.provisioning_failed", actor=actor, target=account, metadata={"error": str(exc)},
                     request=request)
        raise ServiceError(f"Provisioning failed: {exc}", code="adapter_error") from exc

    account.status = HostingStatus.ACTIVE
    account.last_error = ""
    account.save(update_fields=["status", "last_error", "updated_at"])
    audit.record("hosting.provisioned", actor=actor, target=account,
                 metadata={"provider_ref": result.get("provider_ref", "")}, request=request)
    # The account's password lives only in WHM from here on and in this one-time
    # email - it is never written to our own database (see the module docstring
    # in apps.hosting.adapters.whm_api and roadmap section 23, "safe credentials").
    notifications.send_email(
        to_email=account.client.email, template="hosting_welcome",
        context={"account": account, "username": account.username, "password": raw_password},
        event="hosting.welcome",
    )
    return account


@transaction.atomic
def cancel_request(actor, account, *, reason="", request=None):
    _require(actor, "manage_hosting")
    if account.status not in (HostingStatus.PENDING, HostingStatus.FAILED):
        raise ServiceError("Only a pending or failed request can be cancelled.", code="invalid_status")
    account.status = HostingStatus.CANCELLED
    account.save(update_fields=["status", "updated_at"])
    audit.record("hosting.cancelled", actor=actor, target=account, metadata={"reason": reason[:500]},
                 request=request)
    return account


# --- Lifecycle: suspend, unsuspend, terminate, change package -------------------------------

def suspend_account(actor, account, *, reason="", request=None):
    _require(actor, "manage_hosting")
    if account.status != HostingStatus.ACTIVE:
        raise ServiceError("Only an active account can be suspended.", code="invalid_status")
    adapter = get_adapter(account.server)
    try:
        adapter.suspend_account(account.username, reason)
    except HostingError as exc:
        account.last_error = str(exc)
        account.save(update_fields=["last_error", "updated_at"])
        audit.record("hosting.suspend_failed", actor=actor, target=account, metadata={"error": str(exc)},
                     request=request)
        raise ServiceError(f"Suspend failed: {exc}", code="adapter_error") from exc

    account.status, account.suspend_reason, account.last_error = HostingStatus.SUSPENDED, reason, ""
    account.save(update_fields=["status", "suspend_reason", "last_error", "updated_at"])
    audit.record("hosting.suspended", actor=actor, target=account, metadata={"reason": reason[:500]},
                 request=request)
    return account


def unsuspend_account(actor, account, *, request=None):
    _require(actor, "manage_hosting")
    if account.status != HostingStatus.SUSPENDED:
        raise ServiceError("Only a suspended account can be unsuspended.", code="invalid_status")
    adapter = get_adapter(account.server)
    try:
        adapter.unsuspend_account(account.username)
    except HostingError as exc:
        account.last_error = str(exc)
        account.save(update_fields=["last_error", "updated_at"])
        audit.record("hosting.unsuspend_failed", actor=actor, target=account, metadata={"error": str(exc)},
                     request=request)
        raise ServiceError(f"Unsuspend failed: {exc}", code="adapter_error") from exc

    account.status, account.suspend_reason, account.last_error = HostingStatus.ACTIVE, "", ""
    account.save(update_fields=["status", "suspend_reason", "last_error", "updated_at"])
    audit.record("hosting.unsuspended", actor=actor, target=account, request=request)
    return account


def terminate_account(actor, account, *, keep_dns=False, request=None):
    _require(actor, "manage_hosting")
    if account.status not in (HostingStatus.ACTIVE, HostingStatus.SUSPENDED):
        raise ServiceError("Only an active or suspended account can be terminated.", code="invalid_status")
    adapter = get_adapter(account.server)
    try:
        adapter.terminate_account(account.username, keep_dns=keep_dns)
    except HostingError as exc:
        account.last_error = str(exc)
        account.save(update_fields=["last_error", "updated_at"])
        audit.record("hosting.terminate_failed", actor=actor, target=account, metadata={"error": str(exc)},
                     request=request)
        raise ServiceError(f"Termination failed: {exc}", code="adapter_error") from exc

    account.status, account.last_error = HostingStatus.TERMINATED, ""
    account.save(update_fields=["status", "last_error", "updated_at"])
    audit.record("hosting.terminated", actor=actor, target=account, metadata={"keep_dns": keep_dns},
                 request=request)
    return account


def change_package(actor, account, new_product, *, request=None):
    _require(actor, "manage_hosting")
    if account.status != HostingStatus.ACTIVE:
        raise ServiceError("Only an active account can change package.", code="invalid_status")
    if not new_product.whm_package_name:
        raise ServiceError("The new product has no WHM package configured.", code="no_package_configured")
    adapter = get_adapter(account.server)
    try:
        adapter.change_package(account.username, new_product.whm_package_name)
    except HostingError as exc:
        account.last_error = str(exc)
        account.save(update_fields=["last_error", "updated_at"])
        audit.record("hosting.change_package_failed", actor=actor, target=account, metadata={"error": str(exc)},
                     request=request)
        raise ServiceError(f"Package change failed: {exc}", code="adapter_error") from exc

    previous = account.package_name
    account.product, account.package_name, account.last_error = new_product, new_product.whm_package_name, ""
    account.save(update_fields=["product", "package_name", "last_error", "updated_at"])
    audit.record("hosting.package_changed", actor=actor, target=account,
                 metadata={"from": previous, "to": account.package_name}, request=request)
    return account


# --- Sync (staff) --------------------------------------------------------------------------

def sync_status(actor, account, *, request=None):
    _require(actor, "manage_hosting")
    if not account.server:
        raise ServiceError("This account has no server assigned.", code="no_server_assigned")
    adapter = get_adapter(account.server)
    try:
        result = adapter.get_status(account.username)
    except HostingError as exc:
        raise ServiceError(f"Could not sync with the server: {exc}", code="adapter_error") from exc
    account.status = HostingStatus.SUSPENDED if result.get("suspended") else HostingStatus.ACTIVE
    account.last_synced_at = timezone.now()
    account.save(update_fields=["status", "last_synced_at", "updated_at"])
    audit.record("hosting.synced", actor=actor, target=account, request=request)
    return account


def sync_usage(actor, account, *, request=None):
    _require(actor, "manage_hosting")
    if not account.server:
        raise ServiceError("This account has no server assigned.", code="no_server_assigned")
    adapter = get_adapter(account.server)
    try:
        usage = adapter.get_usage(account.username)
    except HostingError as exc:
        raise ServiceError(f"Could not fetch usage: {exc}", code="adapter_error") from exc
    account.disk_used_mb = usage.get("disk_used_mb")
    account.disk_limit_mb = usage.get("disk_limit_mb")
    account.bandwidth_used_mb = usage.get("bandwidth_used_mb")
    account.bandwidth_limit_mb = usage.get("bandwidth_limit_mb")
    account.last_synced_at = timezone.now()
    account.save(update_fields=["disk_used_mb", "disk_limit_mb", "bandwidth_used_mb", "bandwidth_limit_mb",
                                "last_synced_at", "updated_at"])
    return account
