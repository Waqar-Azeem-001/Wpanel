"""
Registrar adapter interface. All registrar-specific code must live inside a
subclass of ``RegistrarAdapter`` - never in views, templates or services (see
roadmap section 23, Provider Isolation).

Adapters work with plain values (strings, ints, lists, dicts), never ORM
instances, so they stay decoupled from our models and from each other. The
operation names match the roadmap's Phase 04 "Provider Operations" list
exactly.
"""
from abc import ABC, abstractmethod


class RegistrarError(Exception):
    """Raised by an adapter when the registrar rejects or fails an operation."""


class RegistrarAdapter(ABC):
    def __init__(self, credentials: dict, *, sandbox: bool = True):
        self.credentials = credentials
        self.sandbox = sandbox

    @abstractmethod
    def check_availability(self, domain_name: str) -> bool:
        """True if ``domain_name`` can be registered."""

    @abstractmethod
    def register_domain(self, domain_name: str, years: int, nameservers: list[str]) -> dict:
        """Register a domain. Returns {"provider_ref": str, "expires_at": datetime}."""

    @abstractmethod
    def renew_domain(self, domain_name: str, provider_ref: str, years: int) -> dict:
        """Extend a registration. Returns {"expires_at": datetime}."""

    @abstractmethod
    def transfer_domain(self, domain_name: str, auth_code: str, years: int) -> dict:
        """Initiate/complete an inbound transfer. Returns {"provider_ref": str, "expires_at": datetime}."""

    @abstractmethod
    def get_domain(self, domain_name: str, provider_ref: str) -> dict:
        """Current registrar-side truth. Returns
        {"status": str, "expires_at": datetime, "nameservers": list[str], "is_locked": bool}."""

    @abstractmethod
    def update_nameservers(self, domain_name: str, provider_ref: str, nameservers: list[str]) -> None:
        ...

    @abstractmethod
    def get_dns(self, domain_name: str, provider_ref: str) -> list[dict]:
        """Zone records as the registrar sees them, where the registrar hosts DNS."""

    @abstractmethod
    def update_dns(self, domain_name: str, provider_ref: str, records: list[dict]) -> None:
        """Push the full zone to the registrar, where the registrar hosts DNS."""

    @abstractmethod
    def lock_domain(self, domain_name: str, provider_ref: str) -> None:
        ...

    @abstractmethod
    def unlock_domain(self, domain_name: str, provider_ref: str) -> None:
        ...
