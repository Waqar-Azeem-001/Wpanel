"""
The only things WHPanel may ask a WH Agent to do (N1).

Every command has a fixed name and a fixed, typed list of parameters. There is no command that runs "a line of text", no free-form path and no
free-form argument: a value is accepted only if it matches the narrow pattern of its type, so a shell metacharacter, a ``..`` or a newline can
never reach the node. The agent checks the same rules again on its own side (``docs/n1-wh-agent.md`` lists them for the Go implementation).
"""
import re
from dataclasses import dataclass, field

PROTOCOL_VERSION = 1
MIN_AGENT_VERSION = (0, 1, 0)

DOMAIN = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
ACCOUNT = re.compile(r"^[a-z][a-z0-9]{2,15}$")
DB_NAME = re.compile(r"^[a-z][a-z0-9_]{2,47}$")
LOCAL_PART = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
DB_USER = re.compile(r"^[a-z][a-z0-9_]{2,31}$")
TABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
DB_PRIVILEGES = ("select", "insert", "update", "delete", "create", "alter", "drop", "index")
EMAIL = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,63}@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
MSG_ID = re.compile(r"^[A-Za-z0-9._,=-]{1,120}$")
MAIL_FOLDER_NAMES = ("inbox", "drafts", "sent", "archive", "spam", "trash", "starred")
HTTPS_URL = re.compile(r"^https" + r"://[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?(:[0-9]{1,5})?$")
BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$")
S3_PATH = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,60}$")
SNAPSHOT = re.compile(r"^[a-f0-9]{8,64}$")
# The ONLY services automatic self-healing may restart (N10). A short, written list: the web server, the database, the mail trio and the SSH server. Never a customer's own process, never a reboot.
HEAL_SERVICES = ("httpd", "mariadb", "postfix", "dovecot", "rspamd", "sshd")
SFTP_NAME = re.compile(r"^[a-z][a-z0-9]{1,14}$")
TRASH_ID = re.compile(r"^([a-f0-9]{12}|ALL)$")
MODE = re.compile(r"^[0-7]{3,4}$")
MAX_INLINE_BYTES = 3_000_000  # file content that travels inside a command; bigger files go by SFTP
BACKUP_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}$")
ARCHIVE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}\.tar\.gz$")
SHA256 = re.compile(r"^[A-Fa-f0-9]{64}$")
CRON_SCHEDULE = re.compile(r"^[0-9*/,-]+( [0-9*/,-]+){4}$")
CRON_COMMAND = re.compile(r"^php [A-Za-z0-9_][A-Za-z0-9_./-]*\.php( [A-Za-z0-9_.=:/-]{1,100}){0,10}$")
JOB_ID = re.compile(r"^[a-f0-9]{10}$")
WP_TITLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,&!?'()-]{0,99}$")
WP_USER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,29}$")
PHP_VERSIONS = ("7.4", "8.0", "8.1", "8.2", "8.3", "8.4")


# The pattern of every kind that is "a value that must look like this". The Go agent is generated from this table (manage.py export_agent_spec), so the two cannot drift apart.
PATTERNS = {"domain": DOMAIN, "account": ACCOUNT, "db_name": DB_NAME, "db_user": DB_USER, "table": TABLE, "sftp_name": SFTP_NAME, "email": EMAIL, "email_any": EMAIL, "msgid": MSG_ID, "https_url": HTTPS_URL, "bucket": BUCKET, "s3path": S3_PATH, "snapshot": SNAPSHOT, "trash_id": TRASH_ID, "mode": MODE, "local_part": LOCAL_PART, "backup_id": BACKUP_ID, "archive_name": ARCHIVE_NAME, "sha256": SHA256, "cron_schedule": CRON_SCHEDULE, "cron_command": CRON_COMMAND, "job_id": JOB_ID, "wp_title": WP_TITLE, "wp_user": WP_USER}
_B64 = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")


def _relpath(name, value, allow_root=True):
    """A path inside the account's own file area: relative, no '..', no control characters, backslash or colon. It names something inside a sandbox the
    node enforces again (including refusing links); it is never put into a shell command."""
    if not isinstance(value, str) or len(value) > 1024 or any(ord(c) < 32 or ord(c) == 127 for c in value) or "\\" in value or ":" in value or value.startswith("/"):
        raise InvalidCommand(f"{name} is not a valid path.")
    parts = [p for p in value.split("/") if p not in ("", ".")]
    if ".." in parts or any(len(p) > 255 for p in parts) or (not parts and not allow_root):
        raise InvalidCommand(f"{name} is not a valid path.")
    return "/".join(parts)


class InvalidCommand(ValueError):
    """The command or one of its parameters is not allowed. The message is safe to show."""


@dataclass(frozen=True)
class Field:
    kind: str  # email | email_any | msgid | text | relpath | path_list | b64 | query | mode | trash_id | sftp_name | domain | account | db_name | db_user | table | local_part | backup_id | enum | enum_list | int | secret
    choices: tuple = ()
    low: int = 0
    high: int = 0
    sensitive: bool = False
    required: bool = True

    def clean(self, name, value):
        if self.kind == "int":
            if isinstance(value, bool) or not isinstance(value, int) or not self.low <= value <= self.high:
                raise InvalidCommand(f"{name} must be a whole number from {self.low} to {self.high}.")
            return value
        if self.kind == "text":  # message text: any printable characters and line breaks, up to ``high`` characters; may be empty
            if not isinstance(value, str) or len(value) > self.high or any((ord(c) < 32 and c not in "\n\r\t") or ord(c) == 127 for c in value):
                raise InvalidCommand(f"{name} must be text of at most {self.high} characters.")
            return value
        if self.kind == "relpath":
            return _relpath(name, value)
        if self.kind == "path_list":
            if not isinstance(value, list) or not 1 <= len(value) <= 200:
                raise InvalidCommand(f"{name} must be a list of 1 to 200 paths.")
            return [_relpath(name, v, allow_root=False) for v in value]
        if self.kind == "b64":
            if not isinstance(value, str) or len(value) > MAX_INLINE_BYTES * 4 // 3 + 8 or not _B64.fullmatch(value):
                raise InvalidCommand(f"{name} must be base64 text of at most {MAX_INLINE_BYTES // 1_000_000} MB.")
            return value
        if self.kind == "query":
            if not isinstance(value, str) or not 1 <= len(value) <= 100 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise InvalidCommand(f"{name} must be 1 to 100 printable characters.")
            return value
        if self.kind == "enum_list":
            if (not isinstance(value, list) or len(value) > len(self.choices) or len(set(map(str, value))) != len(value)
                    or any(not isinstance(v, str) or v not in self.choices for v in value)):
                raise InvalidCommand(f"{name} must be a list of: {', '.join(self.choices)}.")
            return value
        if not isinstance(value, str):
            raise InvalidCommand(f"{name} must be text.")
        if self.kind == "enum":
            if value not in self.choices:
                raise InvalidCommand(f"{name} must be one of: {', '.join(self.choices)}.")
            return value
        if self.kind == "secret":
            if not 12 <= len(value) <= 128 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise InvalidCommand(f"{name} must be 12 to 128 printable characters.")
            return value
        pattern = PATTERNS[self.kind]
        if not pattern.fullmatch(value):
            raise InvalidCommand(f"{name} is not a valid {self.kind.replace('_', ' ')}.")
        return value


@dataclass(frozen=True)
class Command:
    name: str
    fields: dict = field(default_factory=dict)
    timeout: int = 120  # seconds the agent may spend; WHPanel gives up on a lease after this
    idempotent: bool = True  # running it twice with the same parameters leaves the same result, so a lost reply may be retried
    max_attempts: int = 3
    min_agent_version: tuple = (0, 1, 0)
    description: str = ""
    ephemeral: bool = False  # the answer holds customer data (table rows): it is wiped from WHPanel's database as soon as the caller has read it


def _f(kind, **kw):
    return Field(kind, **kw)


# Optional on CREATE_WEBSITE (the node applies its defaults when absent); required on SET_LIMITS.
LIMIT_FIELDS = {
    "disk_mb": Field("int", low=100, high=10_000_000, required=False),
    "memory_mb": Field("int", low=64, high=1_000_000, required=False),
    "processes": Field("int", low=5, high=10_000, required=False),
    "cpu_percent": Field("int", low=5, high=6400, required=False),
}
MAIL_LIMITS = {"max_mailboxes": Field("int", low=0, high=100_000, required=False), "max_forwarders": Field("int", low=0, high=100_000, required=False),
               "max_quota_mb": Field("int", low=1, high=1_000_000, required=False), "emails_per_hour": Field("int", low=1, high=100_000, required=False)}
DATABASES_FIELD = Field("int", low=0, high=100_000, required=False)  # how many databases the account may have
INODES_FIELD = Field("int", low=1000, high=50_000_000, required=False)  # how many files and folders the account may own
IO_FIELD = Field("int", low=1, high=10_000, required=False)  # disk I/O share against other accounts (systemd IOWeight)
SFTP_FIELD = Field("int", low=0, high=10_000, required=False)  # how many extra SFTP logins


COMMANDS = {c.name: c for c in [
    Command("CREATE_WEBSITE", {"account": _f("account"), "domain": _f("domain"), "php_version": _f("enum", choices=PHP_VERSIONS), **LIMIT_FIELDS, "databases": DATABASES_FIELD, "sftp_accounts": SFTP_FIELD, "inodes": INODES_FIELD, "io_weight": IO_FIELD}, 180,
            description="Make the isolated hosting environment: user, folders, web and PHP-FPM configuration, resource limits."),
    Command("SET_LIMITS", {"account": _f("account"), **{k: Field(f.kind, low=f.low, high=f.high) for k, f in LIMIT_FIELDS.items()}, "databases": DATABASES_FIELD, "sftp_accounts": SFTP_FIELD, "inodes": INODES_FIELD, "io_weight": IO_FIELD}, 60,
            description="Change an account's disk quota and CPU, memory and process limits."),
    Command("SUSPEND_WEBSITE", {"account": _f("account")}, 60, description="Stop serving the account's sites (a suspended page is shown) and stop its PHP."),
    Command("UNSUSPEND_WEBSITE", {"account": _f("account")}, 60, description="Undo SUSPEND_WEBSITE."),
    Command("DELETE_WEBSITE", {"account": _f("account"), "domain": _f("domain"), "keep_archive": _f("enum", choices=("yes", "no"))}, 180,
            description="Remove the website; with keep_archive=yes a final backup is kept first."),
    Command("CREATE_DATABASE", {"account": _f("account"), "name": _f("db_name")}, 60,
            description="Create one database. Its name must start with the account name and an underscore; the plan's database limit is enforced on the node."),
    Command("DELETE_DATABASE", {"account": _f("account"), "name": _f("db_name")}, 60, description="Drop one of the account's databases."),
    Command("CREATE_DB_USER", {"account": _f("account"), "name": _f("db_user"), "password": _f("secret", sensitive=True)}, 60, description="Create a database user."),
    Command("SET_DB_PASSWORD", {"account": _f("account"), "name": _f("db_user"), "password": _f("secret", sensitive=True)}, 60, description="Change a database user's password."),
    Command("DELETE_DB_USER", {"account": _f("account"), "name": _f("db_user")}, 60, description="Drop a database user."),
    Command("GRANT_DB_ACCESS", {"account": _f("account"), "database": _f("db_name"), "user": _f("db_user"), "privileges": _f("enum_list", choices=DB_PRIVILEGES)}, 60,
            description="Set exactly these privileges for a user on a database; an empty list removes access."),
    Command("FILE_LIST", {"account": _f("account"), "path": _f("relpath")}, 30, ephemeral=True, description="List a folder."),
    Command("FILE_READ", {"account": _f("account"), "path": _f("relpath"), "max_bytes": _f("int", low=1, high=MAX_INLINE_BYTES)}, 30, ephemeral=True,
            description="Read a file (refused if larger than max_bytes)."),
    Command("FILE_WRITE", {"account": _f("account"), "path": _f("relpath"), "content": Field("b64", sensitive=True), "overwrite": _f("enum", choices=("yes", "no"))}, 60,
            description="Write a file. The content is removed from WHPanel's record once the command finishes."),
    Command("FILE_MKDIR", {"account": _f("account"), "path": _f("relpath")}, 30, description="Make a folder."),
    Command("FILE_RENAME", {"account": _f("account"), "path": _f("relpath"), "new_path": _f("relpath")}, 30, idempotent=False, max_attempts=1, description="Rename or move."),
    Command("FILE_COPY", {"account": _f("account"), "path": _f("relpath"), "new_path": _f("relpath")}, 300, idempotent=False, max_attempts=1, description="Copy."),
    Command("FILE_CHMOD", {"account": _f("account"), "path": _f("relpath"), "mode": _f("mode")}, 30, description="Set a permission (unsafe modes are refused)."),
    Command("FILE_DELETE", {"account": _f("account"), "path": _f("relpath")}, 120, description="Delete for good."),
    Command("FILE_ARCHIVE", {"account": _f("account"), "paths": _f("path_list"), "archive_path": _f("relpath")}, 600, idempotent=False, max_attempts=1, description="Zip files."),
    Command("FILE_EXTRACT", {"account": _f("account"), "path": _f("relpath"), "destination": _f("relpath")}, 600, idempotent=False, max_attempts=1, description="Unzip."),
    Command("FILE_TRASH", {"account": _f("account"), "path": _f("relpath")}, 120, idempotent=False, max_attempts=1, description="Move to the trash."),
    Command("FILE_TRASH_LIST", {"account": _f("account")}, 30, ephemeral=True, description="List the trash."),
    Command("FILE_TRASH_RESTORE", {"account": _f("account"), "item_id": _f("trash_id")}, 120, idempotent=False, max_attempts=1, description="Put a trashed item back."),
    Command("FILE_TRASH_PURGE", {"account": _f("account"), "item_id": _f("trash_id")}, 120, description="Delete one trashed item for good, or ALL of them."),
    Command("FILE_SEARCH", {"account": _f("account"), "path": _f("relpath"), "query": _f("query")}, 60, ephemeral=True, description="Find files by name."),
    Command("SFTP_LIST", {"account": _f("account")}, 30, description="List the account's extra SFTP logins."),
    Command("SFTP_CREATE", {"account": _f("account"), "name": _f("sftp_name"), "password": _f("secret", sensitive=True), "directory": _f("relpath")}, 60,
            description="Make an SFTP login limited to one folder of the account."),
    Command("SFTP_SET_PASSWORD", {"account": _f("account"), "name": _f("sftp_name"), "password": _f("secret", sensitive=True)}, 60, description="Change an SFTP login's password."),
    Command("SFTP_DELETE", {"account": _f("account"), "name": _f("sftp_name")}, 60, description="Remove an SFTP login."),
    Command("MAIL_DOMAIN_ADD", {"account": _f("account"), "domain": _f("domain"), **MAIL_LIMITS}, 120,
            description="Set up mail for the account's domain on a mail node: DKIM key, limits. Safe to repeat: it updates the limits."),
    Command("MAIL_DOMAIN_REMOVE", {"account": _f("account"), "domain": _f("domain")}, 120, idempotent=True, description="Remove the domain's mail, mailboxes and DKIM key."),
    Command("MAIL_LIST", {"account": _f("account"), "domain": _f("domain")}, 30, ephemeral=True, description="Mailboxes, forwarders, auto-replies, spam settings, routing, DKIM."),
    Command("MAIL_BOX_DELETE", {"account": _f("account"), "address": _f("email")}, 120, description="Delete a mailbox and its mail."),
    Command("MAIL_BOX_PASSWORD", {"account": _f("account"), "address": _f("email"), "password": _f("secret", sensitive=True)}, 60, description="Change a mailbox password."),
    Command("MAIL_BOX_QUOTA", {"account": _f("account"), "address": _f("email"), "quota_mb": _f("int", low=0, high=1_000_000)}, 60, description="Set a mailbox quota (0 = the plan maximum)."),
    Command("MAIL_FORWARD_ADD", {"account": _f("account"), "source": _f("email"), "destination": _f("email_any")}, 60, description="Forward an address."),
    Command("MAIL_FORWARD_DELETE", {"account": _f("account"), "source": _f("email"), "destination": _f("email_any")}, 60, description="Stop a forward."),
    Command("MAIL_AUTOREPLY_SET", {"account": _f("account"), "address": _f("email"), "subject": Field("text", high=200), "body": Field("text", high=5000),
                                   "enabled": _f("enum", choices=("yes", "no"))}, 60, description="Set a mailbox's automatic reply."),
    Command("MAIL_AUTOREPLY_DELETE", {"account": _f("account"), "address": _f("email")}, 60, description="Remove the automatic reply."),
    Command("MAIL_SPAM_SET", {"account": _f("account"), "domain": _f("domain"), "enabled": _f("enum", choices=("yes", "no")),
                              "level": _f("enum", choices=("low", "medium", "high")), "delete_spam": _f("enum", choices=("yes", "no"))}, 60, description="Spam filter settings."),
    Command("MAIL_ROUTING_SET", {"account": _f("account"), "domain": _f("domain"), "mode": _f("enum", choices=("local", "remote"))}, 60, description="Receive the domain's mail here, or elsewhere."),
    Command("MAIL_FOLDERS", {"account": _f("account"), "address": _f("email")}, 30, ephemeral=True, description="Message counts per folder."),
    Command("MAIL_MESSAGES", {"account": _f("account"), "address": _f("email"), "folder": _f("enum", choices=MAIL_FOLDER_NAMES)}, 30, ephemeral=True, description="List a folder's messages."),
    Command("MAIL_READ", {"account": _f("account"), "address": _f("email"), "message_id": _f("msgid")}, 30, ephemeral=True, description="Read one message (marks it read)."),
    Command("MAIL_MOVE", {"account": _f("account"), "address": _f("email"), "message_id": _f("msgid"), "folder": _f("enum", choices=("inbox", "archive", "spam", "trash"))}, 30,
            idempotent=False, max_attempts=1, description="Move a message."),
    Command("MAIL_TRASH", {"account": _f("account"), "address": _f("email"), "message_id": _f("msgid")}, 30, idempotent=False, max_attempts=1,
            description="Delete a message: to the trash, or for good when it is already there or is a draft."),
    Command("MAIL_STAR", {"account": _f("account"), "address": _f("email"), "message_id": _f("msgid"), "starred": _f("enum", choices=("yes", "no"))}, 30, description="Star or unstar."),
    Command("MAIL_DRAFT", {"account": _f("account"), "address": _f("email"), "to": Field("text", high=1000), "subject": Field("text", high=200), "body": Field("text", high=100000),
                           "html": Field("text", high=200000), "draft_id": Field("text", high=120)}, 60, idempotent=False, max_attempts=1, description="Keep an unsent message."),
    Command("MAIL_PROFILE", {"account": _f("account"), "address": _f("email")}, 30, ephemeral=True, description="A mailbox's display name, reply-to and signature."),
    Command("MAIL_PROFILE_SET", {"account": _f("account"), "address": _f("email"), "display_name": Field("text", high=100), "reply_to": Field("text", high=200),
                                 "signature_html": Field("text", high=20000), "signature_text": Field("text", high=5000),
                                 "signature_new": _f("enum", choices=("yes", "no")), "signature_reply": _f("enum", choices=("yes", "no"))}, 60, description="Save them."),
    Command("MAIL_SEND", {"account": _f("account"), "address": _f("email"), "to": Field("text", high=1000), "subject": Field("text", high=200), "body": Field("text", high=100000),
                          "html": Field("text", high=200000), "draft_id": Field("text", high=120)}, 120, idempotent=False, max_attempts=1,
            description="Send a message from a mailbox. Counts against the hourly sending limit; a second run would send twice."),
    Command("BACKUP_CONFIGURE", {"endpoint": _f("https_url"), "bucket": _f("bucket"), "prefix": _f("s3path"), "access_key": _f("secret", sensitive=True),
                                 "secret_key": _f("secret", sensitive=True), "repo_password": _f("secret", sensitive=True)}, 120,
            description="Tell the node where its encrypted backups go (an S3-compatible bucket) and the keys. Node-wide, not per account."),
    Command("BACKUP_RUN", {"account": _f("account"), "kind": _f("enum", choices=("files", "database", "email", "full")), "source": _f("enum", choices=("manual", "scheduled"))}, 3600,
            idempotent=False, max_attempts=1, description="Take an encrypted backup of one account. Not repeated automatically: a second run would be a second backup."),
    Command("BACKUP_VERIFY", {"account": _f("account"), "snapshot_id": _f("snapshot")}, 1800, description="Check a backup is complete and unaltered."),
    Command("BACKUP_RESTORE", {"account": _f("account"), "snapshot_id": _f("snapshot"), "kind": _f("enum", choices=("files", "database", "email", "full"))}, 3600, idempotent=False,
            max_attempts=1, description="Put a backup back. A safety backup of what it replaces is taken first."),
    Command("BACKUP_FORGET", {"account": _f("account"), "snapshot_id": _f("snapshot")}, 600, description="Delete one backup."),
    Command("BACKUP_TEST_RESTORE", {"account": _f("account"), "snapshot_id": _f("snapshot")}, 3600,
            description="Restore a backup into a scratch folder, compare every file with the list made at backup time, and throw the copy away. Touches nothing live."),
    Command("BACKUP_NODE_CONFIG", {}, 600, idempotent=False, max_attempts=1, description="Back up this node's own configuration (what is needed to rebuild it), never its backup keys."),
    Command("USAGE_REPORT", {"account": _f("account"), "days": _f("int", low=1, high=90)}, 60, description="What the account uses now, its recent daily history, and its live CPU, memory, process and I/O figures."),
    Command("ISOLATION_CHECK", {}, 120, description="Check this node against the isolation baseline and every account's installed limits against what the plan says."),
    Command("SERVICE_RESTART", {"service": _f("enum", choices=HEAL_SERVICES)}, 120, idempotent=False, max_attempts=1,
            description="Restart one of the node's own services (an allow-listed few). The web server's configuration is tested first; a failing test stops the restart."),
    Command("REMOVE_SSL", {"account": _f("account"), "domain": _f("domain")}, 60, description="Remove the certificate; the site is served without HTTPS."),
    Command("SSL_STATUS", {"account": _f("account")}, 30, description="Read the account's certificate (issuer, dates, renewal and redirect settings)."),
    Command("SET_SSL_OPTIONS", {"account": _f("account"), "domain": _f("domain"), "auto_renew": _f("enum", choices=("yes", "no")),
                                "https_redirect": _f("enum", choices=("yes", "no"))}, 60, description="Set automatic renewal and the always-use-HTTPS redirect."),
    Command("DB_INVENTORY", {"account": _f("account")}, 30, ephemeral=True, description="List the account's databases (with sizes), users and grants."),
    Command("DB_TABLES", {"account": _f("account"), "database": _f("db_name")}, 30, ephemeral=True, description="List the tables of a database."),
    Command("DB_DESCRIBE", {"account": _f("account"), "database": _f("db_name"), "table": _f("table")}, 30, ephemeral=True, description="List a table's columns."),
    Command("DB_BROWSE", {"account": _f("account"), "database": _f("db_name"), "table": _f("table"), "offset": _f("int", low=0, high=10_000_000),
                          "limit": _f("int", low=1, high=100)}, 30, ephemeral=True, description="Read one page of rows from a table (read-only)."),
    Command("BACKUP_DATABASE", {"account": _f("account"), "name": _f("db_name")}, 1800, idempotent=False, max_attempts=1, description="Dump one database to a backup file."),
    Command("RESTORE_DATABASE", {"account": _f("account"), "name": _f("db_name"), "backup_id": _f("backup_id")}, 1800, idempotent=False, max_attempts=1,
            description="Replace a database with a backup. A safety backup of the current contents is taken first."),
    Command("CHANGE_PHP_VERSION", {"account": _f("account"), "domain": _f("domain"), "php_version": _f("enum", choices=PHP_VERSIONS)}, 60,
            description="Switch the site's PHP-FPM pool to another version."),
    Command("ISSUE_SSL", {"account": _f("account"), "domain": _f("domain"), "method": _f("enum", choices=("http-01", "dns-01"))}, 300,
            description="Get and install a certificate."),
    Command("CREATE_MAILBOX", {"account": _f("account"), "domain": _f("domain"), "local_part": _f("local_part"), "quota_mb": _f("int", low=1, high=102400),
                               "password": _f("secret", sensitive=True)}, 60,
            description="Create a mailbox (needs a mail node; native mail is decided at N7)."),
    Command("CRON_LIST", {"account": _f("account")}, 30, description="List the account's scheduled jobs with the plan's rules."),
    Command("CRON_CREATE", {"account": _f("account"), "schedule": _f("cron_schedule"), "command": _f("cron_command")}, 60,
            description="Add a scheduled job: only php and a script inside the website folder, never more often than every five minutes."),
    Command("CRON_UPDATE", {"account": _f("account"), "job_id": _f("job_id"), "schedule": Field("cron_schedule", required=False), "command": Field("cron_command", required=False),
                            "enabled": Field("enum", choices=("yes", "no"), required=False)}, 60, description="Change, switch on or switch off one scheduled job."),
    Command("CRON_DELETE", {"account": _f("account"), "job_id": _f("job_id")}, 60, description="Delete one scheduled job."),
    Command("CRON_RUN", {"account": _f("account"), "job_id": _f("job_id")}, 3700, idempotent=False, max_attempts=1,
            description="Run one scheduled job now, in the account's own limits, and report how it ended."),
    Command("INSTALL_WORDPRESS", {"account": _f("account"), "domain": _f("domain"), "site_title": _f("wp_title"), "admin_user": _f("wp_user"),
                                  "admin_email": _f("email_any"), "admin_password": Field("secret", sensitive=True)}, 900, idempotent=False, max_attempts=1,
            description="Install WordPress into an empty website: its own database and user, checked downloads, wp-config, and the administrator account."),
    Command("MIGRATE_ANALYZE", {"account": _f("account"), "archive": _f("archive_name"), "expected_sha256": Field("sha256", required=False)}, 1800, ephemeral=True,
            description="Read a cPanel account backup that has arrived on this node and report what it holds and what cannot move by itself. Changes nothing."),
    Command("MIGRATE_IMPORT", {"account": _f("account"), "archive": _f("archive_name"), "part": _f("enum", choices=("files", "databases", "mail", "cron")),
                               "replace": Field("enum", choices=("no", "yes"), required=False)}, 7200, max_attempts=2,
            description="Copy one part of a cPanel backup (website files, databases, or mailboxes) into the account's place on this node. Never replaces a database."),
    Command("MIGRATE_VALIDATE", {"account": _f("account"), "archive": _f("archive_name")}, 1800, ephemeral=True,
            description="Compare what is on this node with what the backup held: files present, databases and tables present."),
    Command("MIGRATE_DISCARD", {"archive": _f("archive_name")}, 60, description="Delete a staged cPanel backup (it holds password hashes)."),
    Command("CREATE_BACKUP", {"account": _f("account"), "scope": _f("enum", choices=("full", "files", "databases"))}, 3600, idempotent=False, max_attempts=1,
            description="Make a backup now. Not idempotent: a second run would make a second backup."),
    Command("RESTORE_BACKUP", {"account": _f("account"), "backup_id": _f("backup_id"), "scope": _f("enum", choices=("full", "files", "databases"))}, 3600,
            idempotent=False, max_attempts=1, description="Restore a backup over the account's current data."),
]}


def validate(command, params):
    """Return the cleaned parameters, or raise InvalidCommand. Unknown commands, unknown or missing parameters are all refused."""
    spec = COMMANDS.get(command) if isinstance(command, str) else None
    if spec is None:
        raise InvalidCommand("That command is not on the agent's allow-list.")
    if not isinstance(params, dict):
        raise InvalidCommand("Parameters must be an object.")
    extra = sorted(set(params) - set(spec.fields))
    if extra:
        raise InvalidCommand(f"Unknown parameter(s): {', '.join(str(e)[:40] for e in extra)}.")
    cleaned = {}
    for name, rule in spec.fields.items():
        if name not in params:
            if rule.required:
                raise InvalidCommand(f"{name} is required.")
            continue
        cleaned[name] = rule.clean(name, params[name])
    return cleaned


def scrub(command, params):
    """The parameters with secret values hidden, for storage after the job is finished, for the audit trail and for logs."""
    spec = COMMANDS.get(command)
    return {k: ("***" if spec and k in spec.fields and spec.fields[k].sensitive else v) for k, v in (params or {}).items()}


def parse_version(text):
    try:
        parts = tuple(int(p) for p in str(text).split("."))
    except ValueError:
        return None
    return parts if len(parts) == 3 else None
