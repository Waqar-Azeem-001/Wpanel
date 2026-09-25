"""
Domain business logic. Staff API, customer API, staff web pages and customer
web pages all call these functions; none of them talks to a registrar adapter
directly (see apps.domains.adapters for why).

Reliability: registration, renewal and transfer are all safe to retry - each
checks the domain's current status before calling the adapter, so a repeated
call (a double click, a retried request) never registers/renews twice.
"""
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status

from apps.accounts.roles import perm
from apps.audit import services as audit
from apps.clients.services import contact_role
from apps.core.exceptions import ServiceError

from .adapters.base import RegistrarError
from .adapters.registry import build_adapter
from .models import (Domain, DomainStatus, DnsRecord, RegistrarProvider, TldPricing, domain_tld)

DNS_FIELDS = ("record_type", "name", "content", "ttl", "priority")


def _denied(message="You do not have permission to perform this action."):
    return ServiceError(message, code="permission_denied", status_code=status.HTTP_403_FORBIDDEN)


def _require(actor, codename):
    if not actor.has_perm(perm(codename)):
        raise _denied()


def can_self_service(user, domain):
    """Any contact of the domain's client may perform non-financial self-service actions."""
    if user and user.is_authenticated and user.has_perm(perm("manage_domains")):
        return True
    return bool(user and user.is_authenticated and contact_role(user, domain.client) is not None)


def _require_self_service(actor, domain):
    if not can_self_service(actor, domain):
        raise _denied()


# --- Registrar / adapter -----------------------------------------------------------------

def get_active_registrar():
    return RegistrarProvider.objects.filter(is_active=True).first()


def get_adapter():
    provider = get_active_registrar()
    if provider is None:
        raise ServiceError("No active registrar is configured.", code="no_registrar_configured",
                           status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return provider, build_adapter(provider)


# --- Pricing / availability ---------------------------------------------------------------

def get_tld_pricing(tld, *, require_active=True):
    queryset = TldPricing.objects.filter(tld=tld)
    if require_active:
        queryset = queryset.filter(is_active=True)
    pricing = queryset.first()
    if pricing is None:
        raise ServiceError(f"'{tld}' is not a supported TLD.", code="tld_not_supported")
    return pricing


def check_availability(domain_name):
    """Public: is ``domain_name`` free to register? Raises if the TLD isn't supported."""
    domain_name = domain_name.strip().lower()
    from .models import DOMAIN_NAME_VALIDATOR

    DOMAIN_NAME_VALIDATOR(domain_name)
    pricing = get_tld_pricing(domain_tld(domain_name))
    _, adapter = get_adapter()
    return adapter.check_availability(domain_name), pricing


def visible_domains_for_user(user):
    queryset = Domain.objects.select_related("client", "registrar").prefetch_related("dns_records")
    if user and user.is_authenticated and user.has_perm(perm("view_domains")):
        return queryset
    if user and user.is_authenticated:
        return queryset.filter(client__contacts__user=user).distinct()
    return queryset.none()


def search_domains(queryset, term):
    term = (term or "").strip()
    if not term:
        return queryset
    return queryset.filter(Q(name__icontains=term) | Q(client__company_name__icontains=term)
                           | Q(client__email__icontains=term))


# --- Registration ---------------------------------------------------------------------------

@transaction.atomic
def request_registration(actor, client, domain_name, years, nameservers=None, *, request=None):
    domain_name = domain_name.strip().lower()
    # Customers buy domains through the cart; only staff (or the system, fulfilling a paid order) register them.
    _require(actor, "manage_domains")

    pricing = get_tld_pricing(domain_tld(domain_name))
    if not (pricing.min_years <= years <= pricing.max_years):
        raise ServiceError(f"Choose a term between {pricing.min_years} and {pricing.max_years} years.",
                           code="invalid_term")
    _, adapter = get_adapter()
    if not adapter.check_availability(domain_name):
        raise ServiceError("This domain is not available.", code="domain_unavailable")

    domain = Domain(client=client, name=domain_name, years=years, nameservers=nameservers or [],
                    status=DomainStatus.PENDING_REGISTRATION)
    domain.full_clean()
    domain.save()
    audit.record("domain.registration_requested", actor=actor, target=domain,
                 metadata={"client_id": client.pk, "years": years}, request=request)
    return domain


def complete_registration(actor, domain, *, request=None):
    """
    Staff: complete a pending registration against the active registrar. Idempotent.

    Deliberately not wrapped in ``@transaction.atomic``: on failure we must
    persist the FAILED status and error message so staff can see and retry it,
    which an enclosing atomic block would roll back along with the exception.
    """
    _require(actor, "manage_domains")
    if domain.status == DomainStatus.ACTIVE:
        return domain
    if domain.status not in (DomainStatus.PENDING_REGISTRATION, DomainStatus.FAILED):
        raise ServiceError("This domain is not pending registration.", code="invalid_status")

    provider, adapter = get_adapter()
    try:
        result = adapter.register_domain(domain.name, domain.years, domain.nameservers)
    except RegistrarError as exc:
        domain.status, domain.last_error = DomainStatus.FAILED, str(exc)
        domain.save(update_fields=["status", "last_error", "updated_at"])
        audit.record("domain.registration_failed", actor=actor, target=domain, metadata={"error": str(exc)},
                     request=request)
        raise ServiceError(f"Registration failed: {exc}", code="registrar_error") from exc

    domain.status = DomainStatus.ACTIVE
    domain.registrar = provider
    domain.provider_ref = result["provider_ref"]
    domain.registered_at = timezone.now()
    domain.expires_at = result["expires_at"]
    domain.last_error = ""
    domain.save(update_fields=["status", "registrar", "provider_ref", "registered_at", "expires_at", "last_error",
                               "updated_at"])
    audit.record("domain.registered", actor=actor, target=domain,
                 metadata={"provider_ref": domain.provider_ref, "expires_at": domain.expires_at.isoformat()},
                 request=request)
    return domain


@transaction.atomic
def request_transfer_in(actor, client, domain_name, auth_code, years=1, *, request=None):
    domain_name = domain_name.strip().lower()
    _require(actor, "manage_domains")
    if not auth_code:
        raise ServiceError("An authorization code is required.", code="auth_code_required")

    pricing = get_tld_pricing(domain_tld(domain_name))
    if not (1 <= years <= pricing.max_years):
        raise ServiceError(f"Choose a term up to {pricing.max_years} years.", code="invalid_term")

    domain = Domain(client=client, name=domain_name, years=years, status=DomainStatus.PENDING_TRANSFER_IN)
    domain.set_auth_code(auth_code)
    domain.full_clean()
    domain.save()
    audit.record("domain.transfer_requested", actor=actor, target=domain, metadata={"client_id": client.pk},
                 request=request)
    return domain


def complete_transfer(actor, domain, *, request=None):
    """Staff: complete a pending inbound transfer. Idempotent. Not atomic - see ``complete_registration``."""
    _require(actor, "manage_domains")
    if domain.status == DomainStatus.ACTIVE:
        return domain
    if domain.status not in (DomainStatus.PENDING_TRANSFER_IN, DomainStatus.FAILED):
        raise ServiceError("This domain is not pending transfer.", code="invalid_status")

    provider, adapter = get_adapter()
    auth_code = domain.get_auth_code()
    try:
        result = adapter.transfer_domain(domain.name, auth_code, domain.years)
    except RegistrarError as exc:
        domain.status, domain.last_error = DomainStatus.FAILED, str(exc)
        domain.save(update_fields=["status", "last_error", "updated_at"])
        audit.record("domain.transfer_failed", actor=actor, target=domain, metadata={"error": str(exc)},
                     request=request)
        raise ServiceError(f"Transfer failed: {exc}", code="registrar_error") from exc

    domain.status = DomainStatus.ACTIVE
    domain.registrar = provider
    domain.provider_ref = result["provider_ref"]
    domain.registered_at = timezone.now()
    domain.expires_at = result["expires_at"]
    domain.last_error = ""
    domain.set_auth_code("")  # No longer needed once the transfer is done.
    domain.save(update_fields=["status", "registrar", "provider_ref", "registered_at", "expires_at", "last_error",
                               "auth_code_encrypted", "updated_at"])
    audit.record("domain.transfer_completed", actor=actor, target=domain,
                 metadata={"provider_ref": domain.provider_ref}, request=request)
    return domain


@transaction.atomic
def cancel_domain_request(actor, domain, *, reason="", request=None):
    _require(actor, "manage_domains")
    if domain.status not in (DomainStatus.PENDING_REGISTRATION, DomainStatus.PENDING_TRANSFER_IN,
                             DomainStatus.FAILED):
        raise ServiceError("Only a pending or failed request can be cancelled.", code="invalid_status")
    domain.status = DomainStatus.CANCELLED
    domain.save(update_fields=["status", "updated_at"])
    audit.record("domain.cancelled", actor=actor, target=domain, metadata={"reason": reason[:500]}, request=request)
    return domain


# --- Renewal ------------------------------------------------------------------------------

def renew_domain(actor, domain, years, *, request=None):
    """
    Staff: extend an active registration. Not yet wired to billing (Phase 07/08)
    - once invoices exist, this will be called automatically on payment, the
    same way Phase 05's WHM provisioning fires on a paid order. Not atomic -
    see ``complete_registration`` (the failure audit record must survive).
    """
    _require(actor, "manage_domains")
    return _renew_domain(actor, domain, years, request=request)


def renew_domain_as_system(domain, years):
    """Renew on behalf of the system (a paid renewal invoice): no user, audited with no actor."""
    return _renew_domain(None, domain, years)


def _renew_domain(actor, domain, years, *, request=None):
    if domain.status != DomainStatus.ACTIVE:
        raise ServiceError("Only an active domain can be renewed.", code="invalid_status")
    pricing = get_tld_pricing(domain.tld, require_active=False)
    if not (1 <= years <= pricing.max_years):
        raise ServiceError(f"Choose a term up to {pricing.max_years} years.", code="invalid_term")

    _, adapter = get_adapter()
    try:
        result = adapter.renew_domain(domain.name, domain.provider_ref, years)
    except RegistrarError as exc:
        audit.record("domain.renewal_failed", actor=actor, target=domain, metadata={"error": str(exc)},
                     request=request)
        raise ServiceError(f"Renewal failed: {exc}", code="registrar_error") from exc

    previous = domain.expires_at
    domain.expires_at = result["expires_at"]
    domain.save(update_fields=["expires_at", "updated_at"])
    audit.record("domain.renewed", actor=actor, target=domain,
                 metadata={"years": years, "from": previous.isoformat() if previous else None,
                          "to": domain.expires_at.isoformat()}, request=request)
    return domain


# --- Self-service: auto-renew, nameservers, lock ---------------------------------------------

def set_auto_renew(actor, domain, enabled, *, request=None):
    _require_self_service(actor, domain)
    if domain.auto_renew == enabled:
        return domain
    domain.auto_renew = enabled
    domain.save(update_fields=["auto_renew", "updated_at"])
    audit.record("domain.auto_renew_changed", actor=actor, target=domain, metadata={"auto_renew": enabled},
                 request=request)
    return domain


@transaction.atomic
def update_nameservers(actor, domain, nameservers, *, request=None):
    _require_self_service(actor, domain)
    previous = domain.nameservers
    domain.nameservers = [ns.strip().lower() for ns in nameservers]
    domain.full_clean()
    if domain.status == DomainStatus.ACTIVE and domain.provider_ref:
        _, adapter = get_adapter()
        try:
            adapter.update_nameservers(domain.name, domain.provider_ref, domain.nameservers)
        except RegistrarError as exc:
            raise ServiceError(f"Could not update nameservers: {exc}", code="registrar_error") from exc
    domain.save(update_fields=["nameservers", "updated_at"])
    audit.record("domain.nameservers_changed", actor=actor, target=domain,
                 metadata={"from": previous, "to": domain.nameservers}, request=request)
    return domain


def _set_lock(actor, domain, locked, *, request=None):
    _require_self_service(actor, domain)
    if domain.is_locked == locked:
        return domain
    if domain.status == DomainStatus.ACTIVE and domain.provider_ref:
        _, adapter = get_adapter()
        try:
            (adapter.lock_domain if locked else adapter.unlock_domain)(domain.name, domain.provider_ref)
        except RegistrarError as exc:
            raise ServiceError(f"Could not update the transfer lock: {exc}", code="registrar_error") from exc
    domain.is_locked = locked
    domain.save(update_fields=["is_locked", "updated_at"])
    audit.record("domain.lock_changed", actor=actor, target=domain, metadata={"is_locked": locked}, request=request)
    return domain


def lock_domain(actor, domain, *, request=None):
    return _set_lock(actor, domain, True, request=request)


def unlock_domain(actor, domain, *, request=None):
    return _set_lock(actor, domain, False, request=request)


def sync_domain(actor, domain, *, request=None):
    """Staff: refresh local status/expiry/nameservers/lock from the registrar."""
    _require(actor, "manage_domains")
    if not domain.provider_ref:
        raise ServiceError("This domain has no registrar reference yet.", code="not_registered")
    _, adapter = get_adapter()
    try:
        result = adapter.get_domain(domain.name, domain.provider_ref)
    except RegistrarError as exc:
        raise ServiceError(f"Could not sync with the registrar: {exc}", code="registrar_error") from exc
    domain.expires_at = result.get("expires_at", domain.expires_at)
    domain.nameservers = result.get("nameservers", domain.nameservers)
    domain.is_locked = result.get("is_locked", domain.is_locked)
    domain.save(update_fields=["expires_at", "nameservers", "is_locked", "updated_at"])
    audit.record("domain.synced", actor=actor, target=domain, request=request)
    return domain


# --- DNS ------------------------------------------------------------------------------------

def _push_dns(domain):
    if domain.status == DomainStatus.ACTIVE and domain.provider_ref:
        _, adapter = get_adapter()
        records = list(domain.dns_records.values(*DNS_FIELDS))
        try:
            adapter.update_dns(domain.name, domain.provider_ref, records)
        except RegistrarError as exc:
            raise ServiceError(f"Could not update DNS: {exc}", code="registrar_error") from exc


@transaction.atomic
def add_dns_record(actor, domain, data, *, request=None):
    _require_self_service(actor, domain)
    record = DnsRecord(domain=domain, **{k: v for k, v in data.items() if k in DNS_FIELDS})
    record.full_clean()
    record.save()
    _push_dns(domain)
    audit.record("domain.dns_record_added", actor=actor, target=domain,
                 metadata={"record_type": record.record_type, "name": record.name}, request=request)
    return record


@transaction.atomic
def update_dns_record(actor, domain, record, data, *, request=None):
    _require_self_service(actor, domain)
    for field in DNS_FIELDS:
        if field in data:
            setattr(record, field, data[field])
    record.full_clean()
    record.save()
    _push_dns(domain)
    audit.record("domain.dns_record_updated", actor=actor, target=domain,
                 metadata={"record_id": record.pk}, request=request)
    return record


@transaction.atomic
def delete_dns_record(actor, domain, record, *, request=None):
    _require_self_service(actor, domain)
    meta = {"record_type": record.record_type, "name": record.name}
    record.delete()
    _push_dns(domain)
    audit.record("domain.dns_record_removed", actor=actor, target=domain, metadata=meta, request=request)


# --- TLD pricing (staff) ------------------------------------------------------------------

def visible_tlds_for_user(user):
    queryset = TldPricing.objects.all()
    if user and user.is_authenticated and user.has_perm(perm("view_domains")):
        return queryset
    return queryset.filter(is_active=True)


@transaction.atomic
def set_tld_pricing(actor, tld, *, register_price, renew_price, transfer_price, redemption_price=0,
                    min_years=1, max_years=10, request=None):
    _require(actor, "manage_domains")
    # Look up, validate, then write. (get_or_create-then-validate would INSERT unvalidated input first,
    # and PostgreSQL rejects an over-long value with a raw database error instead of a validation error.)
    pricing = TldPricing.objects.filter(tld=tld).first()
    created = pricing is None
    if created:
        pricing = TldPricing(tld=tld)
    pricing.register_price, pricing.renew_price = register_price, renew_price
    pricing.transfer_price, pricing.redemption_price = transfer_price, redemption_price
    pricing.min_years, pricing.max_years = min_years, max_years
    pricing.full_clean()
    pricing.save()
    audit.record("tld_pricing.added" if created else "tld_pricing.changed", actor=actor, target=pricing,
                 metadata={"tld": tld}, request=request)
    return pricing


def set_tld_pricing_active(actor, pricing, is_active, *, request=None):
    _require(actor, "manage_domains")
    if pricing.is_active == is_active:
        return pricing
    pricing.is_active = is_active
    pricing.save(update_fields=["is_active", "updated_at"])
    audit.record("tld_pricing.status_changed", actor=actor, target=pricing, metadata={"is_active": is_active},
                 request=request)
    return pricing
