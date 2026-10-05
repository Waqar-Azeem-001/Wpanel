"""
The one door to hosting control (next-generation roadmap, H0): customers, staff and the API ask for a capability
operation on an account, and this module decides whether they may, finds the account's provider, calls it, and records
what happened. Nothing else may call a capability on an adapter.

    from apps.hosting import control
    control.capabilities_for(account)
    control.perform(user, account, Capability.DNS, "list_dns_records", account.domain)

What this guarantees for every capability, so each later slice (files, databases, email, ...) only adds its own
contract and UI, never its own authorization or audit code:

* Ownership and permission are checked here, server-side, on every call. Reading needs ``view_hosting`` or being a
  contact of the account's client; changing anything needs ``manage_hosting`` or being such a contact (the same
  self-service rule as the rest of the hosting services).
* Only the operations listed in ``capabilities.OPERATIONS`` can be reached. There is no way to name an arbitrary
  method, and no shell.
* The account must still be live (pending, active or suspended) and have a server; a provider that does not implement
  the capability gives a clear "not supported" answer, not a crash.
* Provider failures become ``ServiceError``; every change is audited (who, which account, which operation, result),
  with only the non-secret arguments the caller chooses to record.
* Nothing in a response or an audit record carries the server's credentials.
"""
from rest_framework import status

from apps.audit import services as audit
from apps.core.exceptions import ServiceError

from .adapters.base import HostingError
from .adapters.capabilities import (OPERATIONS, READ_OPERATIONS, Capability, CapabilityNotSupported,
                                    supported_capabilities)
from .models import LIVE_STATUSES
from .services import can_self_service, get_adapter, visible_hosting_accounts_for_user


def _denied():
    return ServiceError("You do not have permission to perform this action.", code="permission_denied",
                        status_code=status.HTTP_403_FORBIDDEN)


def _provider(account):
    if account.server is None:
        raise ServiceError("This account has no server assigned.", code="no_server_assigned")
    return get_adapter(account.server)


def capabilities_for(account) -> list:
    """The capability names this account's provider supports, in a stable order. Empty if it has no server yet."""
    if account.server is None:
        return []
    found = supported_capabilities(_provider(account))
    return [c.value for c in Capability if c in found]


def _authorize(user, account, operation):
    if operation in READ_OPERATIONS:
        allowed = visible_hosting_accounts_for_user(user).filter(pk=account.pk).exists()
    else:
        allowed = can_self_service(user, account)
    if not allowed:
        raise _denied()


def perform(user, account, capability, operation, *args, audit_metadata=None, request=None, **kwargs):
    """
    Run one allowlisted ``operation`` of ``capability`` on ``account`` as ``user``; returns what the provider returns.

    ``audit_metadata`` is the non-secret description of a change to keep in the audit trail (a path, a record name -
    never a password or file content).
    """
    capability = Capability(capability)
    if operation not in OPERATIONS.get(capability, frozenset()):
        raise ServiceError("That operation is not available.", code="operation_not_allowed")
    _authorize(user, account, operation)
    if account.status not in LIVE_STATUSES:
        raise ServiceError("This hosting account is not active.", code="account_not_live")
    adapter = _provider(account)
    if capability not in supported_capabilities(adapter):
        raise ServiceError("This hosting account does not support that feature yet.",
                           code="capability_not_supported", status_code=status.HTTP_501_NOT_IMPLEMENTED)
    changing = operation not in READ_OPERATIONS
    metadata = {"capability": capability.value, "operation": operation, **(audit_metadata or {})}
    try:
        result = getattr(adapter, operation)(account.username, *args, **kwargs)
    except CapabilityNotSupported as exc:
        raise ServiceError("This hosting account does not support that feature yet.",
                           code="capability_not_supported", status_code=status.HTTP_501_NOT_IMPLEMENTED) from exc
    except HostingError as exc:
        if changing:
            audit.record("hosting.control_failed", actor=user, target=account, metadata=metadata, request=request)
        raise ServiceError(f"The hosting server could not complete that: {exc}", code="adapter_error") from exc
    if changing:
        audit.record("hosting.control", actor=user, target=account, metadata=metadata, request=request)
    return result
