"""
Hosting/WHM adapter interface. All server-specific code must live inside a
subclass of ``HostingAdapter`` - never in views, templates or services (see
roadmap section 23, Provider Isolation). Operation names match the roadmap's
Phase 05 "WHM Operations" list.

Adapters work with plain values, never ORM instances, so they stay decoupled
from our models.
"""
from abc import ABC, abstractmethod


class HostingError(Exception):
    """Raised by an adapter when the server rejects or fails an operation."""


class HostingAdapter(ABC):
    def __init__(self, *, host, port, credentials: dict, use_ssl=True, verify_ssl=True, timeout=30):
        self.host = host
        self.port = port
        self.credentials = credentials
        self.use_ssl = use_ssl
        self.verify_ssl = verify_ssl
        self.timeout = timeout

    @abstractmethod
    def create_account(self, *, username: str, domain: str, package: str, contact_email: str,
                       password: str) -> dict:
        """Create a hosting account. Returns {"provider_ref": str}."""

    @abstractmethod
    def suspend_account(self, username: str, reason: str = "") -> None:
        ...

    @abstractmethod
    def unsuspend_account(self, username: str) -> None:
        ...

    @abstractmethod
    def terminate_account(self, username: str, *, keep_dns: bool = False) -> None:
        ...

    @abstractmethod
    def change_package(self, username: str, package: str) -> None:
        ...

    @abstractmethod
    def get_status(self, username: str) -> dict:
        """Registrar-side truth. Returns {"suspended": bool, "domain": str}."""

    @abstractmethod
    def get_usage(self, username: str) -> dict:
        """
        Usage where supported. Returns
        {"disk_used_mb": int|None, "disk_limit_mb": int|None,
         "bandwidth_used_mb": int|None, "bandwidth_limit_mb": int|None}.
        A value of None means "not reported by this server", not zero.
        """
