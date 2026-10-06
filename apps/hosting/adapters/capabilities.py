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
class TrashItem:
    item_id: str
    name: str
    original_path: str
    is_dir: bool
    deleted_at: str  # ISO 8601
    size_bytes: Optional[int] = None


@dataclass(frozen=True)
class DatabaseInfo:
    name: str
    size_mb: Optional[float] = None
    users: list = field(default_factory=list)


@dataclass(frozen=True)
class DatabaseGrant:
    user: str
    database: str
    privileges: list = field(default_factory=list)


@dataclass(frozen=True)
class DatabaseLimits:
    """What the plan allows and how the provider names things. ``None`` means no fixed limit."""

    name_prefix: str = ""  # added in front of what the customer types (shared hosting keeps accounts apart this way)
    max_databases: Optional[int] = None
    max_users: Optional[int] = None
    password_min_length: int = 12


@dataclass(frozen=True)
class TableInfo:
    name: str
    rows: int = 0
    size_kb: int = 0


@dataclass(frozen=True)
class TableData:
    """One page of a table, for read-only browsing: ``rows`` are lists in the order of ``columns``."""

    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    total: int = 0
    key_columns: list = field(default_factory=list)  # the primary key: rows can be edited one by one only if present


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    type: str = "TEXT"
    nullable: bool = True
    primary_key: bool = False
    default: Optional[str] = None


@dataclass(frozen=True)
class QueryResult:
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    affected: int = 0
    truncated: bool = False
    message: str = ""


@dataclass(frozen=True)
class DatabaseAdminAccess:
    """How a customer gets a database manager: ``external`` is a one-time address on the provider (phpMyAdmin) and
    ``embedded`` means WHPanel's own read-only browser serves it. Never carries a password or a long-lived token."""

    kind: str  # "external" or "embedded"
    url: str = ""


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
class EmailLimits:
    """The mail domain, and what the plan allows. ``None`` means no fixed limit."""

    domain: str = ""
    max_mailboxes: Optional[int] = None
    max_forwarders: Optional[int] = None
    default_quota_mb: int = 1024
    max_quota_mb: Optional[int] = None
    password_min_length: int = 12


@dataclass(frozen=True)
class AutoresponderInfo:
    address: str
    subject: str = ""
    body: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class SpamSettings:
    enabled: bool = True
    level: str = "medium"  # "low", "medium" or "high": how suspicious a message must be to count as spam
    delete_spam: bool = False  # False: file it in the Spam folder; True: delete it straight away


@dataclass(frozen=True)
class MessageInfo:
    message_id: str
    sender: str
    subject: str
    received_at: str  # ISO 8601
    unread: bool = False
    body: str = ""  # only filled in by ``read_message``
    to: str = ""
    folder: str = "inbox"
    snippet: str = ""  # the first words of the text, for the message list
    starred: bool = False
    html: str = ""  # the formatted version, when the message has one (only filled in by ``read_message``)


@dataclass(frozen=True)
class MailProfile:
    """A mailbox owner's own mail settings."""

    display_name: str = ""  # how the sender is shown to people they write to
    reply_to: str = ""  # replies go here instead, when set
    signature_html: str = ""  # already reduced to safe HTML by the caller
    signature_text: str = ""
    signature_new: bool = True  # add the signature to new messages
    signature_reply: bool = True  # and to replies


@dataclass(frozen=True)
class WebmailAccess:
    """How a customer reaches their mail in a browser: ``external`` is a one-time address on the provider (Roundcube),
    ``embedded`` means WHPanel's own simple inbox viewer. Never carries a password."""

    kind: str  # "external" or "embedded"
    url: str = ""


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
    def archive_paths(self, username: str, paths: list, archive_path: str) -> int:
        """Zip ``paths`` into a new archive at ``archive_path`` (never overwriting); returns the number of entries."""

    @abstractmethod
    def extract_archive(self, username: str, path: str, destination: str) -> int:
        """Unpack the .zip at ``path`` into ``destination`` (all or nothing, nothing overwritten); returns the count."""

    @abstractmethod
    def trash_path(self, username: str, path: str) -> str:
        """Move to the trash (recoverable); returns the trash id."""

    @abstractmethod
    def list_trash(self, username: str) -> list:
        """``TrashItem`` rows, newest first."""

    @abstractmethod
    def restore_trash(self, username: str, item_id: str) -> str:
        """Put a trashed item back; returns where it went."""

    @abstractmethod
    def purge_trash(self, username: str, item_id: Optional[str]) -> None:
        """Delete one trashed item for good, or the whole trash when ``item_id`` is None."""

    @abstractmethod
    def search_files(self, username: str, path: str, query: str) -> list:
        """``FileEntry`` rows whose NAME contains ``query``, anywhere under ``path`` (a capped, convenience search)."""


# The only privileges a customer may grant; a provider maps them to its own and refuses anything else.
DATABASE_PRIVILEGES = ("select", "insert", "update", "delete", "create", "alter", "drop", "index")


class DatabasesCapability(ABC):
    """
    ``create_database`` and ``create_database_user`` take what the customer typed; the provider may put its own account
    prefix in front (see ``database_limits().name_prefix``). Every other operation uses the full names that the list
    methods return. Plan limits are the provider's truth: it refuses to create past them.
    """

    @abstractmethod
    def database_limits(self, username: str) -> DatabaseLimits: ...

    @abstractmethod
    def database_admin_access(self, username: str, database: str) -> DatabaseAdminAccess:
        """phpMyAdmin or an equivalent: an external one-time address, or ``embedded`` for WHPanel's own browser."""

    @abstractmethod
    def list_tables(self, username: str, database: str) -> list:
        """``TableInfo`` rows."""

    @abstractmethod
    def browse_table(self, username: str, database: str, table: str, offset: int, limit: int) -> TableData:
        """A read-only page of rows (``limit`` is capped at 100 by the provider)."""

    @abstractmethod
    def describe_table(self, username: str, database: str, table: str) -> list:
        """``ColumnInfo`` rows."""

    @abstractmethod
    def run_query(self, username: str, database: str, sql: str) -> QueryResult:
        """Run ONE SQL statement the customer typed, with the provider's time, size and result limits."""

    @abstractmethod
    def insert_row(self, username: str, database: str, table: str, values: dict) -> None: ...

    @abstractmethod
    def update_row(self, username: str, database: str, table: str, key: dict, values: dict) -> None:
        """Change the row identified by its primary-key values ``key``."""

    @abstractmethod
    def delete_row(self, username: str, database: str, table: str, key: dict) -> None: ...

    @abstractmethod
    def list_database_grants(self, username: str) -> list:
        """``DatabaseGrant`` rows: which user may do what on which database."""
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


MAIL_ROUTING_MODES = ("local", "remote")  # local: this hosting server receives the mail; remote: another service does
SPAM_LEVELS = ("low", "medium", "high")
MAIL_FOLDERS = ("inbox", "drafts", "sent", "archive", "spam", "trash")
MAIL_VIRTUAL_FOLDERS = ("starred",)  # not a place messages live in: every starred message, wherever it is


class EmailCapability(ABC):
    """
    Addresses are full (``name@domain``); the provider only accepts the account's own mail domain. Plan limits and
    password rules belong to the provider. Passwords are passed straight through and never kept in the clear.
    """

    @abstractmethod
    def email_limits(self, username: str) -> EmailLimits: ...

    @abstractmethod
    def list_autoresponders(self, username: str) -> list:
        """``AutoresponderInfo`` rows."""

    @abstractmethod
    def set_autoresponder(self, username: str, address: str, subject: str, body: str, enabled: bool) -> None: ...

    @abstractmethod
    def delete_autoresponder(self, username: str, address: str) -> None: ...

    @abstractmethod
    def get_mail_routing(self, username: str) -> str:
        """One of ``MAIL_ROUTING_MODES``."""

    @abstractmethod
    def set_mail_routing(self, username: str, mode: str) -> None: ...

    @abstractmethod
    def get_spam_settings(self, username: str) -> SpamSettings: ...

    @abstractmethod
    def set_spam_settings(self, username: str, settings: SpamSettings) -> None: ...

    @abstractmethod
    def webmail_access(self, username: str, address: str) -> WebmailAccess: ...

    @abstractmethod
    def message_folders(self, username: str, address: str) -> dict:
        """{folder: (total, unread)} for every one of ``MAIL_FOLDERS`` and ``MAIL_VIRTUAL_FOLDERS``."""

    @abstractmethod
    def list_messages(self, username: str, address: str, folder: str = "inbox") -> list:
        """``MessageInfo`` rows (newest first) of one folder (or ``starred``), for the embedded mail viewer."""

    @abstractmethod
    def move_message(self, username: str, address: str, message_id: str, folder: str) -> None:
        """Move to ``inbox``, ``archive``, ``spam`` or ``trash``."""

    @abstractmethod
    def delete_message(self, username: str, address: str, message_id: str) -> None:
        """Move to the trash; a message already in the trash, or a draft, is deleted for good."""

    @abstractmethod
    def set_message_starred(self, username: str, address: str, message_id: str, starred: bool) -> None: ...

    @abstractmethod
    def save_draft(self, username: str, address: str, to: str, subject: str, body: str, html: str = "",
                   draft_id: str = "") -> str:
        """Keep an unsent message in Drafts (replacing ``draft_id`` when given); returns the draft's id."""

    @abstractmethod
    def get_mail_profile(self, username: str, address: str) -> MailProfile: ...

    @abstractmethod
    def set_mail_profile(self, username: str, address: str, profile: MailProfile) -> None: ...

    @abstractmethod
    def send_message(self, username: str, address: str, to: str, subject: str, body: str, html: str = "",
                     draft_id: str = "") -> None:
        """Send from this mailbox (``to`` may list several addresses separated by commas). ``body`` is the plain text,
        ``html`` the optional formatted version (already reduced to safe HTML by the caller)."""

    @abstractmethod
    def read_message(self, username: str, address: str, message_id: str) -> MessageInfo: ...

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
                                 "copy_path", "delete_path", "search_files", "archive_paths", "extract_archive",
                                 "trash_path", "list_trash", "restore_trash", "purge_trash"}),
    Capability.DATABASES: frozenset({"database_limits", "database_admin_access", "list_tables", "browse_table",
                                     "describe_table", "run_query", "insert_row", "update_row", "delete_row",
                                     "list_database_grants", "list_databases", "create_database",
                                     "delete_database", "list_database_users",
                                     "create_database_user", "set_database_user_password", "delete_database_user",
                                     "grant_database_access"}),
    Capability.EMAIL: frozenset({"email_limits", "list_autoresponders", "set_autoresponder", "delete_autoresponder",
                                 "get_mail_routing", "set_mail_routing", "get_spam_settings", "set_spam_settings",
                                 "webmail_access", "list_messages", "read_message", "message_folders",
                                 "move_message", "delete_message", "send_message", "set_message_starred",
                                 "save_draft", "get_mail_profile", "set_mail_profile",
                                 "list_mailboxes", "create_mailbox", "delete_mailbox", "set_mailbox_password",
                                 "set_mailbox_quota", "list_forwarders", "create_forwarder", "delete_forwarder"}),
    Capability.DOMAINS: frozenset({"list_domains", "add_domain", "remove_domain", "set_redirect"}),
    Capability.DNS: frozenset({"list_dns_records", "add_dns_record", "update_dns_record", "delete_dns_record"}),
    Capability.SSL: frozenset({"get_ssl_status", "request_ssl", "set_https_redirect"}),
    Capability.PHP: frozenset({"get_php_settings", "set_php_settings"}),
    Capability.CRON: frozenset({"list_cron_jobs", "create_cron_job", "update_cron_job", "delete_cron_job"}),
    Capability.BACKUPS: frozenset({"list_backups", "create_backup", "restore_backup"}),
}

# Operations that only read; everything else changes something and needs the stronger permission and an audit entry.
READ_OPERATIONS = frozenset({"get_status", "get_usage", "database_limits", "database_admin_access", "list_tables", "browse_table", "describe_table",
                             "list_database_grants", "list_files", "read_file", "search_files", "list_trash", "list_databases",
                             "list_database_users", "list_mailboxes", "list_forwarders", "email_limits", "list_autoresponders",
                             "get_mail_routing", "get_spam_settings", "webmail_access", "list_messages", "read_message", "message_folders",
                             "get_mail_profile", "list_domains",
                             "list_dns_records", "get_ssl_status", "get_php_settings", "list_cron_jobs",
                             "list_backups"})


def supported_capabilities(adapter) -> frozenset:
    """What this provider instance can do. Account lifecycle and usage are part of every ``HostingAdapter``."""
    found = {Capability.ACCOUNT, Capability.USAGE}
    for capability, contract in CONTRACTS.items():
        if isinstance(adapter, contract):
            found.add(capability)
    return frozenset(found)
