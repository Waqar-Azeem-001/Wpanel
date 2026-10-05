"""
Provider-neutral hosting capabilities (next-generation roadmap, H0).

The customer UI and the hosting services never learn whether an account lives on cPanel/WHM or on a native node.
They ask for a *capability* ("files", "dns", ...) and an operation inside it; a provider (``HostingAdapter``
subclass: today ``WhmApiAdapter`` is the cPanel provider, a native provider arrives with the N track) either
implements that capability's contract below or does not declare it. Nothing here names cPanel.

How a provider opts in: subclass the capability mixin(s) it implements, next to ``HostingAdapter``::

    class WhmApiAdapter(DatabasesCapability, HostingAdapter): ...

``supported_capabilities(adapter)`` then reports it. Account lifecycle and usage are part of ``HostingAdapter``
itself (every provider has them), so they are always supported.

All contracts work with plain values (strings, ints, dicts shaped like the records below), never ORM instances, and
take the account's ``username`` (the provider-side account name) as the first argument - the same convention as the
lifecycle methods. Paths, names and limits are NEVER trusted: the provider re-validates everything and the service
gateway (``apps.hosting.control``) only lets an allowlisted operation through.

Contract version 1. Operations are added to a capability by adding a method here and its name to ``OPERATIONS``;
nothing else may be called through the gateway (the "structured, allowlisted operations, never a shell" rule).
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

CONTRACT_VERSION = 1


class Capability(str, Enum):
    ACCOUNT = "account"
    FILES = "files"
    DATABASES = "databases"
    EMAIL = "email"
    DOMAINS = "domains"
    DNS = "dns"
    SSL = "ssl"
    PHP = "php"
    CRON = "cron"
    BACKUPS = "backups"
    USAGE = "usage"


class CapabilityNotSupported(Exception):
    """The account's provider does not implement this capability (yet). Not an error in the account itself."""


# --- Neutral records -------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class FileEntry:
    name: str
    path: str  # relative to the account's hosting root, always with forward slashes
    is_dir: bool
    size_bytes: Optional[int] = None
    modified_at: Optional[str] = None  # ISO 8601
    permissions: str = ""  # octal text such as "0644" where the provider reports it


@dataclass(frozen=True)
class DatabaseInfo:
    name: str
    size_mb: Optional[float] = None
    users: list = field(default_factory=list)


@dataclass(frozen=True)
class DatabaseUserInfo:
    name: str
    databases: list = field(default_factory=list)


@dataclass(frozen=True)
class MailboxInfo:
    address: str
    quota_mb: Optional[int] = None  # None means unlimited/unknown
    used_mb: Optional[float] = None


@dataclass(frozen=True)
class ForwarderInfo:
    source: str
    destination: str


@dataclass(frozen=True)
class DomainInfo:
    name: str
    kind: str  # "primary", "addon", "subdomain" or "alias"
    document_root: str = ""
    redirect_to: str = ""


@dataclass(frozen=True)
class DnsRecord:
    record_id: str  # the provider's own handle for editing/deleting this record
    type: str  # A, AAAA, CNAME, MX, TXT, SRV, CAA
    name: str
    value: str
    ttl: int = 3600
    priority: Optional[int] = None


@dataclass(frozen=True)
class SslStatus:
    domain: str
    active: bool
    issuer: str = ""
    expires_at: Optional[str] = None  # ISO 8601
    auto_renew: bool = False
    https_redirect: bool = False


@dataclass(frozen=True)
class PhpSettings:
    version: str
    memory_limit: str = ""
    upload_max_filesize: str = ""
    max_execution_time: Optional[int] = None
    display_errors: bool = False
    available_versions: list = field(default_factory=list)


@dataclass(frozen=True)
class CronJobInfo:
    job_id: str
    schedule: str  # five-field cron expression
    command: str
    enabled: bool = True


@dataclass(frozen=True)
class BackupInfo:
    backup_id: str
    kind: str  # "files", "database", "email" or "full"
    created_at: Optional[str] = None
    size_mb: Optional[float] = None
    status: str = "complete"


# --- Capability contracts --------------------------------------------------------------------------------------

class FilesCapability(ABC):
    @abstractmethod
    def list_files(self, username: str, path: str) -> list:
        """Entries (``FileEntry``) directly inside ``path`` (relative to the hosting root)."""

    @abstractmethod
    def read_file(self, username: str, path: str, *, max_bytes: int) -> bytes:
        """The file's content, refusing anything larger than ``max_bytes``."""

    @abstractmethod
    def write_file(self, username: str, path: str, content: bytes, *, overwrite: bool = False) -> None: ...

    @abstractmethod
    def make_directory(self, username: str, path: str) -> None: ...

    @abstractmethod
    def rename_path(self, username: str, path: str, new_path: str) -> None: ...

    @abstractmethod
    def copy_path(self, username: str, path: str, new_path: str) -> None: ...

    @abstractmethod
    def delete_path(self, username: str, path: str) -> None: ...

    @abstractmethod
    def search_files(self, username: str, path: str, query: str) -> list:
        """``FileEntry`` rows whose NAME contains ``query``, anywhere under ``path`` (a capped, convenience search)."""


class DatabasesCapability(ABC):
    @abstractmethod
    def list_databases(self, username: str) -> list:
        """``DatabaseInfo`` rows."""

    @abstractmethod
    def create_database(self, username: str, name: str) -> None: ...

    @abstractmethod
    def delete_database(self, username: str, name: str) -> None: ...

    @abstractmethod
    def list_database_users(self, username: str) -> list:
        """``DatabaseUserInfo`` rows."""

    @abstractmethod
    def create_database_user(self, username: str, name: str, password: str) -> None: ...

    @abstractmethod
    def set_database_user_password(self, username: str, name: str, password: str) -> None: ...

    @abstractmethod
    def delete_database_user(self, username: str, name: str) -> None: ...

    @abstractmethod
    def grant_database_access(self, username: str, database: str, user: str, privileges: list) -> None:
        """Give ``user`` the (neutral, allowlisted) ``privileges`` on ``database``; an empty list revokes access."""


class EmailCapability(ABC):
    @abstractmethod
    def list_mailboxes(self, username: str) -> list:
        """``MailboxInfo`` rows."""

    @abstractmethod
    def create_mailbox(self, username: str, address: str, password: str, *, quota_mb: Optional[int] = None) -> None: ...

    @abstractmethod
    def delete_mailbox(self, username: str, address: str) -> None: ...

    @abstractmethod
    def set_mailbox_password(self, username: str, address: str, password: str) -> None: ...

    @abstractmethod
    def set_mailbox_quota(self, username: str, address: str, quota_mb: Optional[int]) -> None: ...

    @abstractmethod
    def list_forwarders(self, username: str) -> list:
        """``ForwarderInfo`` rows."""

    @abstractmethod
    def create_forwarder(self, username: str, source: str, destination: str) -> None: ...

    @abstractmethod
    def delete_forwarder(self, username: str, source: str, destination: str) -> None: ...


class DomainsCapability(ABC):
    @abstractmethod
    def list_domains(self, username: str) -> list:
        """``DomainInfo`` rows: the primary domain and everything attached to the account."""

    @abstractmethod
    def add_domain(self, username: str, name: str, kind: str, document_root: str = "") -> None: ...

    @abstractmethod
    def remove_domain(self, username: str, name: str) -> None: ...

    @abstractmethod
    def set_redirect(self, username: str, name: str, redirect_to: str) -> None:
        """Redirect ``name`` to ``redirect_to``; an empty string removes the redirect."""


class DnsCapability(ABC):
    @abstractmethod
    def list_dns_records(self, username: str, domain: str) -> list:
        """``DnsRecord`` rows for ``domain``'s zone."""

    @abstractmethod
    def add_dns_record(self, username: str, domain: str, record: DnsRecord) -> str:
        """Add the record; returns its ``record_id``."""

    @abstractmethod
    def update_dns_record(self, username: str, domain: str, record: DnsRecord) -> None: ...

    @abstractmethod
    def delete_dns_record(self, username: str, domain: str, record_id: str) -> None: ...


class SslCapability(ABC):
    @abstractmethod
    def get_ssl_status(self, username: str, domain: str) -> SslStatus: ...

    @abstractmethod
    def request_ssl(self, username: str, domain: str) -> None:
        """Issue or renew the certificate where the provider supports it (it may complete asynchronously)."""

    @abstractmethod
    def set_https_redirect(self, username: str, domain: str, enabled: bool) -> None: ...


class PhpCapability(ABC):
    @abstractmethod
    def get_php_settings(self, username: str, domain: str) -> PhpSettings: ...

    @abstractmethod
    def set_php_settings(self, username: str, domain: str, settings: dict) -> None:
        """Apply only the allowlisted keys of ``PhpSettings`` (version and the customer-safe limits)."""


class CronCapability(ABC):
    @abstractmethod
    def list_cron_jobs(self, username: str) -> list:
        """``CronJobInfo`` rows."""

    @abstractmethod
    def create_cron_job(self, username: str, schedule: str, command: str) -> str:
        """Returns the new job's ``job_id``."""

    @abstractmethod
    def update_cron_job(self, username: str, job_id: str, *, schedule: Optional[str] = None,
                        command: Optional[str] = None, enabled: Optional[bool] = None) -> None: ...

    @abstractmethod
    def delete_cron_job(self, username: str, job_id: str) -> None: ...


class BackupsCapability(ABC):
    """Recoverability, not just a backup job: a provider that declares this must also be able to restore."""

    @abstractmethod
    def list_backups(self, username: str) -> list:
        """``BackupInfo`` rows, newest first."""

    @abstractmethod
    def create_backup(self, username: str, kind: str) -> str:
        """Start a backup of ``kind``; returns its ``backup_id``."""

    @abstractmethod
    def restore_backup(self, username: str, backup_id: str) -> None: ...


# Capability -> the mixin that defines its contract. ACCOUNT and USAGE live on HostingAdapter itself.
CONTRACTS = {
    Capability.FILES: FilesCapability,
    Capability.DATABASES: DatabasesCapability,
    Capability.EMAIL: EmailCapability,
    Capability.DOMAINS: DomainsCapability,
    Capability.DNS: DnsCapability,
    Capability.SSL: SslCapability,
    Capability.PHP: PhpCapability,
    Capability.CRON: CronCapability,
    Capability.BACKUPS: BackupsCapability,
}

# Capability -> the operations the service gateway may call through a provider. Anything not listed is refused, so
# new provider methods are never reachable from the UI/API until they are deliberately added here.
OPERATIONS = {
    Capability.ACCOUNT: frozenset({"get_status"}),
    Capability.USAGE: frozenset({"get_usage"}),
    Capability.FILES: frozenset({"list_files", "read_file", "write_file", "make_directory", "rename_path",
                                 "copy_path", "delete_path", "search_files"}),
    Capability.DATABASES: frozenset({"list_databases", "create_database", "delete_database", "list_database_users",
                                     "create_database_user", "set_database_user_password", "delete_database_user",
                                     "grant_database_access"}),
    Capability.EMAIL: frozenset({"list_mailboxes", "create_mailbox", "delete_mailbox", "set_mailbox_password",
                                 "set_mailbox_quota", "list_forwarders", "create_forwarder", "delete_forwarder"}),
    Capability.DOMAINS: frozenset({"list_domains", "add_domain", "remove_domain", "set_redirect"}),
    Capability.DNS: frozenset({"list_dns_records", "add_dns_record", "update_dns_record", "delete_dns_record"}),
    Capability.SSL: frozenset({"get_ssl_status", "request_ssl", "set_https_redirect"}),
    Capability.PHP: frozenset({"get_php_settings", "set_php_settings"}),
    Capability.CRON: frozenset({"list_cron_jobs", "create_cron_job", "update_cron_job", "delete_cron_job"}),
    Capability.BACKUPS: frozenset({"list_backups", "create_backup", "restore_backup"}),
}

# Operations that only read; everything else changes something and needs the stronger permission and an audit entry.
READ_OPERATIONS = frozenset({"get_status", "get_usage", "list_files", "read_file", "search_files", "list_databases",
                             "list_database_users", "list_mailboxes", "list_forwarders", "list_domains",
                             "list_dns_records", "get_ssl_status", "get_php_settings", "list_cron_jobs",
                             "list_backups"})


def supported_capabilities(adapter) -> frozenset:
    """What this provider instance can do. Account lifecycle and usage are part of every ``HostingAdapter``."""
    found = {Capability.ACCOUNT, Capability.USAGE}
    for capability, contract in CONTRACTS.items():
        if isinstance(adapter, contract):
            found.add(capability)
    return frozenset(found)
