"""
Provider-neutral hosting capabilities (roadmap H0).

The customer UI and the hosting services never learn which kind of server an account lives on.
They ask for a *capability* ("files", "dns", ...) and an operation inside it; a provider (``HostingAdapter``
subclass: ``WhmApiAdapter`` is the cPanel provider, ``DemoAdapter`` the sample one) either
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
    LOGS = "logs"
    FTP = "ftp"
    ACCESS = "access"
    SECURITY = "security"
    INSIGHTS = "insights"
    CPANEL_ACCESS = "cpanel_access"
    BACKUPS = "backups"
    USAGE = "usage"
    APPS = "apps"


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
    redirect_permanent: bool = True  # 301 when True, 302 when False


@dataclass(frozen=True)
class DomainLimits:
    """What the plan allows for extra domains, and the nameservers a customer points a domain at. ``None``: no fixed limit."""

    max_addon_domains: Optional[int] = None
    max_subdomains: Optional[int] = None
    nameservers: list = field(default_factory=list)


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
    status: str = ""  # "none", "active", "expiring" (14 days or less left) or "expired"
    issued_at: Optional[str] = None  # ISO 8601
    days_left: Optional[int] = None  # negative once expired


@dataclass(frozen=True)
class PhpSettings:
    version: str
    memory_limit: str = ""
    upload_max_filesize: str = ""
    max_execution_time: Optional[int] = None
    display_errors: bool = False
    available_versions: list = field(default_factory=list)


@dataclass(frozen=True)
class PhpOptions:
    """What a customer may choose on this plan. The provider refuses anything outside these lists."""

    versions: list = field(default_factory=list)  # newest first
    end_of_life: list = field(default_factory=list)  # versions that no longer get security fixes
    recommended: str = ""
    memory_limits: list = field(default_factory=list)  # for example ["128M", "256M", "512M"]
    upload_limits: list = field(default_factory=list)
    execution_times: list = field(default_factory=list)  # seconds


@dataclass(frozen=True)
class PhpExtension:
    name: str
    enabled: bool = False
    required: bool = False  # cannot be switched off (the website or the panel needs it)


@dataclass(frozen=True)
class CronJobInfo:
    job_id: str
    schedule: str  # five-field cron expression
    command: str
    enabled: bool = True
    last_run_at: Optional[str] = None  # ISO 8601
    last_status: str = ""  # "ok", "failed" or "" when it has not run yet
    last_output: str = ""  # the end of what the job printed, kept short


@dataclass(frozen=True)
class CronOptions:
    """What a customer may schedule on this plan."""

    max_jobs: int = 0
    min_interval_minutes: int = 5
    presets: list = field(default_factory=list)  # (label, five-field schedule)


LOG_LINE_CHOICES = [50, 200, 500]  # how many lines a log view may show


@dataclass(frozen=True)
class LogView:
    """The end of one log, newest line last. ``total`` counts the matching lines before the tail was taken."""

    kind: str  # "error" or "access"
    lines: list = field(default_factory=list)
    total: int = 0
    size_bytes: int = 0


@dataclass(frozen=True)
class FtpOptions:
    host: str = ""
    port: int = 21
    secure_port: int = 22  # SFTP, when the provider offers it (0 when not)
    max_accounts: Optional[int] = 0  # None means no fixed limit
    root: str = ""  # the account's own folder name shown as the top of every FTP login


@dataclass(frozen=True)
class FtpAccount:
    login: str  # the full login, such as dev@example.com
    directory: str  # relative to the account's hosting root; "" is the whole account
    created_at: Optional[str] = None  # ISO 8601


@dataclass(frozen=True)
class IpRule:
    rule_id: str
    network: str  # an address or a CIDR range, tidied
    note: str = ""
    created_at: Optional[str] = None  # ISO 8601


@dataclass(frozen=True)
class ScanFinding:
    path: str
    rule: str  # a short stable id, such as "obfuscated-code"
    severity: str  # "high" or "medium"
    detail: str  # plain language: what looks wrong


@dataclass(frozen=True)
class ScanResult:
    """The last malware scan. ``scanned_at`` is None until one has run."""

    scanned_at: Optional[str] = None
    files_scanned: int = 0
    findings: list = field(default_factory=list)  # ScanFinding, most severe first


@dataclass(frozen=True)
class QuarantineItem:
    item_id: str
    path: str  # where the file was before it was isolated
    rule: str = ""
    quarantined_at: Optional[str] = None


@dataclass(frozen=True)
class ProtectedDir:
    path: str
    login: str
    created_at: Optional[str] = None


@dataclass(frozen=True)
class SshKey:
    fingerprint: str  # SHA256:...
    label: str
    key_type: str
    added_at: Optional[str] = None


@dataclass(frozen=True)
class SshStatus:
    enabled: bool = False
    host: str = ""
    port: int = 22
    keys: list = field(default_factory=list)  # SshKey
    max_keys: int = 0


@dataclass(frozen=True)
class IntegrityResult:
    """File-integrity monitoring: what changed since the baseline was taken. ``baseline_at`` is None without a baseline."""

    baseline_at: Optional[str] = None
    files_in_baseline: int = 0
    checked_at: Optional[str] = None
    changed: list = field(default_factory=list)
    added: list = field(default_factory=list)
    removed: list = field(default_factory=list)


@dataclass(frozen=True)
class UsagePoint:
    """One day of usage: disk in use at the end of the day, and the bandwidth used that day."""

    day: str  # ISO date
    disk_mb: int = 0
    bandwidth_mb: int = 0


@dataclass(frozen=True)
class PerformanceStat:
    key: str  # "cpu", "memory", "processes" or "io"
    label: str
    used: float
    limit: float
    unit: str  # "%", "MB", "" or "KB/s"


@dataclass(frozen=True)
class BackupInfo:
    backup_id: str
    kind: str  # "files", "database", "email" or "full"
    created_at: Optional[str] = None
    size_mb: Optional[float] = None
    status: str = "complete"
    source: str = "manual"  # "manual", "scheduled" or "before restore"
    verified: str = ""  # "" (not checked yet), "ok" or "failed"
    verified_at: Optional[str] = None  # ISO 8601
    offsite: str = ""  # "" (no off-server copy), "ok" or "missing" (the copy is gone or no longer matches)


@dataclass(frozen=True)
class BackupOptions:
    kinds: list = field(default_factory=list)  # (kind, label, what it holds)
    max_backups: int = 0  # the newest are kept; older ones are removed to make room
    schedules: list = field(default_factory=list)  # (value, label); "off" is always first
    schedule: str = "off"  # the account's current schedule
    last_scheduled_at: Optional[str] = None  # ISO 8601
    offsite: bool = False  # a second copy of every new backup is kept away from the server


# --- Capability contracts --------------------------------------------------------------------------------------

class FilesCapability(ABC):
    @abstractmethod
    def home_path(self, username: str) -> str:
        """The account's home folder as the web server knows it (for a rule that needs a full path, such as a password file). Paths everywhere else are relative to it."""

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
    def set_permissions(self, username: str, path: str, mode: str) -> None:
        """Set a file's or folder's permission (an octal text such as "644"). The provider refuses modes unsafe on a shared server."""

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


DOMAIN_KINDS = ("addon", "subdomain", "alias")  # what a customer can add; "primary" is the account's own domain
DNS_RECORD_TYPES = ("A", "AAAA", "CNAME", "MX", "TXT", "SRV", "CAA")
DNS_MIN_TTL = 300
DNS_MAX_TTL = 86400


class DomainsCapability(ABC):
    """
    ``name`` is always the full domain (``blog.example.com``). ``document_root`` is relative to the account's website folder.
    The provider enforces plan limits, valid names, and that a subdomain sits under one of the account's own domains.
    """

    @abstractmethod
    def domain_limits(self, username: str) -> DomainLimits: ...

    @abstractmethod
    def list_domains(self, username: str) -> list:
        """``DomainInfo`` rows: the primary domain and everything attached to the account."""

    @abstractmethod
    def add_domain(self, username: str, name: str, kind: str, document_root: str = "") -> None: ...

    @abstractmethod
    def remove_domain(self, username: str, name: str) -> None: ...

    @abstractmethod
    def set_redirect(self, username: str, name: str, redirect_to: str, permanent: bool = True) -> None:
        """Redirect ``name`` to the address ``redirect_to`` (301 when ``permanent``, else 302); an empty string removes it."""


class DnsCapability(ABC):
    """
    Records are relative to the zone: the name is ``@`` for the domain itself, or a label such as ``www`` or ``_dmarc``. The
    provider validates every record by its type (an A record needs an IPv4 address, a CNAME cannot sit at ``@``, ...), keeps
    the TTL in range and refuses duplicates. Changing DNS can take a site or its mail offline, so callers confirm first.
    """

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
    """
    One certificate per domain. The provider decides how it is issued (free, automatic) and refuses what cannot work: a domain whose
    DNS does not point at the server cannot be validated, and "always use HTTPS" needs a working certificate first.
    """

    @abstractmethod
    def list_ssl_status(self, username: str) -> list:
        """``SslStatus`` rows, one for every domain of the account."""

    @abstractmethod
    def get_ssl_status(self, username: str, domain: str) -> SslStatus: ...

    @abstractmethod
    def request_ssl(self, username: str, domain: str) -> None:
        """Issue a certificate, or renew the one in place (it may complete asynchronously)."""

    @abstractmethod
    def remove_ssl(self, username: str, domain: str) -> None:
        """Remove the certificate; the domain is served without HTTPS and "always use HTTPS" is switched off."""

    @abstractmethod
    def set_ssl_auto_renew(self, username: str, domain: str, enabled: bool) -> None: ...

    @abstractmethod
    def set_https_redirect(self, username: str, domain: str, enabled: bool) -> None: ...


class PhpCapability(ABC):
    """
    PHP settings are per domain. Only the choices of ``php_options`` can be applied (never a raw configuration file), and an
    extension the account depends on cannot be switched off.
    """

    @abstractmethod
    def php_options(self, username: str) -> PhpOptions: ...

    @abstractmethod
    def list_php_extensions(self, username: str, domain: str) -> list:
        """``PhpExtension`` rows."""

    @abstractmethod
    def set_php_extension(self, username: str, domain: str, name: str, enabled: bool) -> None: ...

    @abstractmethod
    def get_php_settings(self, username: str, domain: str) -> PhpSettings: ...

    @abstractmethod
    def set_php_settings(self, username: str, domain: str, settings: dict) -> None:
        """Apply only the allowlisted keys of ``PhpSettings`` (version and the customer-safe limits)."""


class CronCapability(ABC):
    """
    Scheduled jobs are never a free shell: the provider accepts only a PHP script inside the account, with a schedule that is not
    more frequent than the plan allows.
    """

    @abstractmethod
    def cron_options(self, username: str) -> CronOptions: ...

    @abstractmethod
    def run_cron_job(self, username: str, job_id: str) -> CronJobInfo:
        """Run the job once now and return it with its result."""

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


class AppsCapability(ABC):
    """
    Installing a ready-made application into the website. Only WordPress for now. The provider makes the database and its user, fetches the software, writes its
    configuration and creates the administrator: the customer names the site and the administrator and nothing else.
    """

    @abstractmethod
    def install_wordpress(self, username: str, domain: str, title: str, admin_user: str, admin_password: str, admin_email: str) -> dict:
        """Returns ``{"state": "ready" | "installing", ...}``. "installing" means the server is still working and WHPanel will be told when it ends."""


class LogsCapability(ABC):
    """Read-only views of the website's own logs. Never another account's, never a server log."""

    @abstractmethod
    def read_log(self, username: str, kind: str, lines: int, query: str) -> LogView: ...

    @abstractmethod
    def clear_log(self, username: str, kind: str) -> None: ...


class FtpCapability(ABC):
    """File-transfer logins limited to a folder of the account. Passwords are never readable once set."""

    @abstractmethod
    def ftp_options(self, username: str) -> FtpOptions: ...

    @abstractmethod
    def list_ftp_accounts(self, username: str) -> list:
        """``FtpAccount`` rows."""

    @abstractmethod
    def create_ftp_account(self, username: str, name: str, password: str, directory: str) -> str:
        """Returns the full login."""

    @abstractmethod
    def set_ftp_password(self, username: str, login: str, password: str) -> None: ...

    @abstractmethod
    def delete_ftp_account(self, username: str, login: str) -> None: ...


class AccessCapability(ABC):
    """Block visitors by IP address or range. The provider refuses ranges too broad to be a sensible block."""

    @abstractmethod
    def list_ip_rules(self, username: str) -> list:
        """``IpRule`` rows."""

    @abstractmethod
    def add_ip_rule(self, username: str, network: str, note: str) -> str:
        """Returns the new ``rule_id``."""

    @abstractmethod
    def delete_ip_rule(self, username: str, rule_id: str) -> None: ...


@dataclass(frozen=True)
class CpanelAccessSession:
    """A one-time sign-in link into the server's own control panel (cPanel). Never stored, never logged, never shown as text."""

    url: str
    expires_at: Optional[str] = None  # ISO 8601, when the provider says the link stops working


@dataclass(frozen=True)
class NodeService:
    key: str  # "web", "database", "mail", "dns", "agent" or "backups"
    label: str
    state: str  # "running", "degraded", "down" or "unavailable" (this kind of server cannot report it)
    detail: str = ""


@dataclass(frozen=True)
class NodeStatus:
    """
    How one machine is doing. A figure the server kind cannot report is ``None`` - never invented - and the dashboard says
    "not reported by this server type". A cPanel/WHM server cannot report CPU, memory, disk, network, an agent or backups.
    """

    version: str = ""
    cpu_percent: Optional[float] = None
    memory_used_mb: Optional[int] = None
    memory_total_mb: Optional[int] = None
    disk_used_gb: Optional[float] = None
    disk_total_gb: Optional[float] = None
    load: Optional[tuple] = None  # (1 minute, 5 minutes, 15 minutes)
    network_in_kbps: Optional[int] = None
    network_out_kbps: Optional[int] = None
    uptime_days: Optional[int] = None
    services: list = field(default_factory=list)  # NodeService


class NodeCapability(ABC):
    """The machine itself, not one hosting account on it. Read-only; staff-facing only."""

    @abstractmethod
    def node_status(self) -> NodeStatus: ...


def supports_node(adapter) -> bool:
    return isinstance(adapter, NodeCapability)


class CpanelAccessCapability(ABC):
    """
    TRANSITIONAL (H15): sign the customer into the server's own panel with a one-time link, for the things WHPanel does not do yet.
    It exists only while cPanel is in use and is removed with it. The provider must not reveal its own credentials.
    """

    @abstractmethod
    def open_cpanel_access(self, username: str) -> CpanelAccessSession: ...


class InsightsCapability(ABC):
    """History and live resource use of one website, read-only. Limits are enforced by the provider, not by these screens."""

    @abstractmethod
    def usage_history(self, username: str, days: int) -> list:
        """``UsagePoint`` rows, oldest first, one per day, ending today."""

    @abstractmethod
    def performance_stats(self, username: str) -> list:
        """``PerformanceStat`` rows for what the account is using right now against its plan's allowance."""


class SecurityCapability(ABC):
    """
    Findings and protections for one website. Nothing here deletes a customer's file: a suspicious file is moved to quarantine and
    can be put back, and the scan result is the provider's opinion, not proof.
    """

    @abstractmethod
    def get_scan_result(self, username: str) -> ScanResult: ...

    @abstractmethod
    def run_malware_scan(self, username: str) -> ScanResult: ...

    @abstractmethod
    def quarantine_file(self, username: str, path: str) -> str:
        """Isolate the file; returns the quarantine ``item_id``."""

    @abstractmethod
    def list_quarantine(self, username: str) -> list:
        """``QuarantineItem`` rows."""

    @abstractmethod
    def restore_quarantined(self, username: str, item_id: str) -> None: ...

    @abstractmethod
    def list_protected_dirs(self, username: str) -> list:
        """``ProtectedDir`` rows."""

    @abstractmethod
    def protect_directory(self, username: str, path: str, login: str, password: str) -> None:
        """Ask for a login before anyone can open the folder from the web."""

    @abstractmethod
    def unprotect_directory(self, username: str, path: str) -> None: ...

    @abstractmethod
    def ssh_status(self, username: str) -> SshStatus: ...

    @abstractmethod
    def set_ssh_enabled(self, username: str, enabled: bool) -> None: ...

    @abstractmethod
    def add_ssh_key(self, username: str, label: str, public_key: str) -> str:
        """Returns the key's fingerprint. Only public keys are accepted."""

    @abstractmethod
    def delete_ssh_key(self, username: str, fingerprint: str) -> None: ...

    @abstractmethod
    def integrity_status(self, username: str) -> IntegrityResult: ...

    @abstractmethod
    def create_integrity_baseline(self, username: str) -> IntegrityResult: ...

    @abstractmethod
    def check_integrity(self, username: str) -> IntegrityResult: ...


class BackupsCapability(ABC):
    """
    Recoverability, not just a backup job: a provider that declares this must also be able to check a backup's integrity and
    restore it. A restore is only offered for a backup that passes the check, and takes a safety copy of what it replaces.
    """

    @abstractmethod
    def backup_options(self, username: str) -> BackupOptions: ...

    @abstractmethod
    def list_backups(self, username: str) -> list:
        """``BackupInfo`` rows, newest first (a scheduled backup that is due is taken when this is read)."""

    @abstractmethod
    def create_backup(self, username: str, kind: str) -> str:
        """Take a backup of ``kind`` now; returns its ``backup_id``."""

    @abstractmethod
    def verify_backup(self, username: str, backup_id: str) -> BackupInfo:
        """Check the stored backup is complete and unaltered; returns it with ``verified`` set."""

    @abstractmethod
    def restore_backup(self, username: str, backup_id: str) -> None: ...

    @abstractmethod
    def delete_backup(self, username: str, backup_id: str) -> None: ...

    @abstractmethod
    def set_backup_schedule(self, username: str, schedule: str) -> None: ...

    @abstractmethod
    def set_backup_offsite(self, username: str, enabled: bool) -> None:
        """Keep (or stop keeping) a second copy of every new backup away from the hosting server."""


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
    Capability.LOGS: LogsCapability,
    Capability.FTP: FtpCapability,
    Capability.ACCESS: AccessCapability,
    Capability.SECURITY: SecurityCapability,
    Capability.INSIGHTS: InsightsCapability,
    Capability.CPANEL_ACCESS: CpanelAccessCapability,
    Capability.BACKUPS: BackupsCapability,
    Capability.APPS: AppsCapability,
}

# Capability -> the operations the service gateway may call through a provider. Anything not listed is refused, so
# new provider methods are never reachable from the UI/API until they are deliberately added here.
OPERATIONS = {
    Capability.ACCOUNT: frozenset({"get_status"}),
    Capability.USAGE: frozenset({"get_usage"}),
    Capability.FILES: frozenset({"home_path", "list_files", "read_file", "write_file", "make_directory", "rename_path",
                                 "copy_path", "set_permissions", "delete_path", "search_files", "archive_paths", "extract_archive",
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
    Capability.DOMAINS: frozenset({"domain_limits", "list_domains", "add_domain", "remove_domain", "set_redirect"}),
    Capability.DNS: frozenset({"list_dns_records", "add_dns_record", "update_dns_record", "delete_dns_record"}),
    Capability.SSL: frozenset({"list_ssl_status", "get_ssl_status", "request_ssl", "remove_ssl", "set_ssl_auto_renew",
                               "set_https_redirect"}),
    Capability.PHP: frozenset({"php_options", "list_php_extensions", "set_php_extension", "get_php_settings",
                               "set_php_settings"}),
    Capability.CRON: frozenset({"cron_options", "run_cron_job", "list_cron_jobs", "create_cron_job", "update_cron_job",
                                "delete_cron_job"}),
    Capability.LOGS: frozenset({"read_log", "clear_log"}),
    Capability.FTP: frozenset({"ftp_options", "list_ftp_accounts", "create_ftp_account", "set_ftp_password", "delete_ftp_account"}),
    Capability.ACCESS: frozenset({"list_ip_rules", "add_ip_rule", "delete_ip_rule"}),
    Capability.SECURITY: frozenset({"get_scan_result", "run_malware_scan", "quarantine_file", "list_quarantine", "restore_quarantined",
                                    "list_protected_dirs", "protect_directory", "unprotect_directory", "ssh_status",
                                    "set_ssh_enabled", "add_ssh_key", "delete_ssh_key", "integrity_status",
                                    "create_integrity_baseline", "check_integrity"}),
    Capability.INSIGHTS: frozenset({"usage_history", "performance_stats"}),
    Capability.APPS: frozenset({"install_wordpress"}),
    Capability.CPANEL_ACCESS: frozenset({"open_cpanel_access"}),
    Capability.BACKUPS: frozenset({"backup_options", "list_backups", "create_backup", "verify_backup", "restore_backup",
                                   "delete_backup", "set_backup_schedule", "set_backup_offsite"}),
}

# Operations that only read; everything else changes something and needs the stronger permission and an audit entry.
READ_OPERATIONS = frozenset({"home_path", "get_status", "get_usage", "database_limits", "database_admin_access", "list_tables", "browse_table", "describe_table",
                             "list_database_grants", "list_files", "read_file", "search_files", "list_trash", "list_databases",
                             "list_database_users", "list_mailboxes", "list_forwarders", "domain_limits", "email_limits", "list_autoresponders",
                             "get_mail_routing", "get_spam_settings", "webmail_access", "list_messages", "read_message", "message_folders",
                             "get_mail_profile", "list_domains",
                             "list_dns_records", "usage_history", "performance_stats", "get_scan_result", "list_quarantine", "list_protected_dirs", "ssh_status", "integrity_status", "backup_options", "read_log", "ftp_options", "list_ftp_accounts", "list_ip_rules", "cron_options", "list_ssl_status", "get_ssl_status", "php_options", "list_php_extensions",
                             "get_php_settings", "list_cron_jobs",
                             "list_backups"})


def supported_capabilities(adapter) -> frozenset:
    """What this provider instance can do. Account lifecycle and usage are part of every ``HostingAdapter``."""
    found = {Capability.ACCOUNT, Capability.USAGE}
    for capability, contract in CONTRACTS.items():
        if isinstance(adapter, contract):
            found.add(capability)
    allowed = getattr(adapter, "enabled_capabilities", None)  # a provider may switch tools on one at a time (the cPanel adapter does)
    if allowed is not None:
        found = {c for c in found if c in allowed or c in (Capability.ACCOUNT, Capability.USAGE)}
    return frozenset(found)
