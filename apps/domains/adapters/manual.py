"""
Manual adapter: simulates a registrar entirely within our own database.

This is NOT a real registrar integration - it makes no network calls. It
exists so the full domain lifecycle (search, register, renew, transfer,
nameservers, DNS, lock/unlock) can be built, demonstrated and tested end to
end before a real registrar contract and API are available. A production
deployment must configure a real ``RegistrarProvider`` backed by a real
adapter before going live; see the Phase 04 gap report.

Availability here means only "not already registered in our own database" -
it cannot tell whether a name is genuinely free at a real registry.
"""
import uuid
from datetime import timedelta

from django.utils import timezone

from .base import RegistrarAdapter, RegistrarError


class ManualAdapter(RegistrarAdapter):
    def check_availability(self, domain_name: str) -> bool:
        from apps.domains.models import Domain, LIVE_STATUSES

        return not Domain.objects.filter(name=domain_name, status__in=LIVE_STATUSES).exists()

    def register_domain(self, domain_name: str, years: int, nameservers: list[str]) -> dict:
        return {
            "provider_ref": f"manual-{uuid.uuid4().hex[:12]}",
            "expires_at": timezone.now() + timedelta(days=365 * years),
        }

    def renew_domain(self, domain_name: str, provider_ref: str, years: int) -> dict:
        from apps.domains.models import Domain

        domain = Domain.objects.filter(provider_ref=provider_ref).first()
        base = domain.expires_at if domain and domain.expires_at and domain.expires_at > timezone.now() \
            else timezone.now()
        return {"expires_at": base + timedelta(days=365 * years)}

    def transfer_domain(self, domain_name: str, auth_code: str, years: int) -> dict:
        if not auth_code:
            raise RegistrarError("An authorization code is required to transfer this domain.")
        return {
            "provider_ref": f"manual-{uuid.uuid4().hex[:12]}",
            "expires_at": timezone.now() + timedelta(days=365 * years),
        }

    def get_domain(self, domain_name: str, provider_ref: str) -> dict:
        from apps.domains.models import Domain

        domain = Domain.objects.filter(provider_ref=provider_ref).first()
        if domain is None:
            raise RegistrarError("Unknown domain reference.")
        return {
            "status": domain.status, "expires_at": domain.expires_at,
            "nameservers": domain.nameservers, "is_locked": domain.is_locked,
        }

    def update_nameservers(self, domain_name: str, provider_ref: str, nameservers: list[str]) -> None:
        pass  # Nothing external to push - the caller persists this locally.

    def get_dns(self, domain_name: str, provider_ref: str) -> list[dict]:
        return []  # We are the source of truth for DNS zone records; nothing to fetch back.

    def update_dns(self, domain_name: str, provider_ref: str, records: list[dict]) -> None:
        pass  # No-op: this adapter doesn't host DNS anywhere external.

    def lock_domain(self, domain_name: str, provider_ref: str) -> None:
        pass

    def unlock_domain(self, domain_name: str, provider_ref: str) -> None:
        pass
