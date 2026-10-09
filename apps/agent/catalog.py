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
BACKUP_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}$")
PHP_VERSIONS = ("7.4", "8.0", "8.1", "8.2", "8.3", "8.4")


class InvalidCommand(ValueError):
    """The command or one of its parameters is not allowed. The message is safe to show."""


@dataclass(frozen=True)
class Field:
    kind: str  # domain | account | db_name | local_part | backup_id | enum | int | secret
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
        pattern = {"domain": DOMAIN, "account": ACCOUNT, "db_name": DB_NAME, "local_part": LOCAL_PART, "backup_id": BACKUP_ID}[self.kind]
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


def _f(kind, **kw):
    return Field(kind, **kw)


# Optional on CREATE_WEBSITE (the node applies its defaults when absent); required on SET_LIMITS.
LIMIT_FIELDS = {
    "disk_mb": Field("int", low=100, high=10_000_000, required=False),
    "memory_mb": Field("int", low=64, high=1_000_000, required=False),
    "processes": Field("int", low=5, high=10_000, required=False),
    "cpu_percent": Field("int", low=5, high=6400, required=False),
}


COMMANDS = {c.name: c for c in [
    Command("CREATE_WEBSITE", {"account": _f("account"), "domain": _f("domain"), "php_version": _f("enum", choices=PHP_VERSIONS), **LIMIT_FIELDS}, 180,
            description="Make the isolated hosting environment: user, folders, web and PHP-FPM configuration, resource limits."),
    Command("SET_LIMITS", {"account": _f("account"), **{k: Field(f.kind, low=f.low, high=f.high) for k, f in LIMIT_FIELDS.items()}}, 60,
            description="Change an account's disk quota and CPU, memory and process limits."),
    Command("SUSPEND_WEBSITE", {"account": _f("account")}, 60, description="Stop serving the account's sites (a suspended page is shown) and stop its PHP."),
    Command("UNSUSPEND_WEBSITE", {"account": _f("account")}, 60, description="Undo SUSPEND_WEBSITE."),
    Command("DELETE_WEBSITE", {"account": _f("account"), "domain": _f("domain"), "keep_archive": _f("enum", choices=("yes", "no"))}, 180,
            description="Remove the website; with keep_archive=yes a final backup is kept first."),
    Command("CREATE_DATABASE", {"account": _f("account"), "name": _f("db_name")}, 60, description="Create one database owned by the account."),
    Command("CHANGE_PHP_VERSION", {"account": _f("account"), "domain": _f("domain"), "php_version": _f("enum", choices=PHP_VERSIONS)}, 60,
            description="Switch the site's PHP-FPM pool to another version."),
    Command("ISSUE_SSL", {"account": _f("account"), "domain": _f("domain"), "method": _f("enum", choices=("http-01", "dns-01"))}, 300,
            description="Get and install a certificate."),
    Command("CREATE_MAILBOX", {"account": _f("account"), "domain": _f("domain"), "local_part": _f("local_part"), "quota_mb": _f("int", low=1, high=102400),
                               "password": _f("secret", sensitive=True)}, 60,
            description="Create a mailbox (needs a mail node; native mail is decided at N7)."),
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
