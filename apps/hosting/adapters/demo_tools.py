"""
The demo provider's advanced tools: logs, FTP accounts and IP blocking.

* Logs are real files in the account's ``logs`` folder (so the file manager shows them too), filled with sample lines the first
  time they are read. The viewer only ever reads those two files, shows the end of them, and can filter by a word.
* FTP accounts: a login is ``name@domain``; the folder must exist inside the account; the password must be strong and is stored only
  as a hash. Five accounts per account.
* IP blocking: an address or a range, tidied; a range wider than a /16 (IPv4) or /48 (IPv6) is refused as too broad to be a sensible
  block; duplicates are refused; fifty rules per account.
No traffic is filtered and no FTP server runs here; a real provider (the cPanel adapter, a native node) implements the same contracts.
"""
import ipaddress
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from django.contrib.auth.hashers import make_password

from .base import HostingError
from .ftp_rules import FTP_NAME, check_ftp_password
from .capabilities import LOG_LINE_CHOICES, FtpAccount, FtpOptions, IpRule, LogView

LOG_KINDS = {"error": "logs/error.log", "access": "logs/access.log"}
MAX_LOG_READ = 2_000_000
MAX_FTP = 5
MAX_RULES = 50


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sample_log(kind, domain):
    lines = []
    for n in range(60):
        hour, minute = (n * 7) // 60 % 24, (n * 7) % 60
        stamp = f"06/Oct/2026:{hour:02d}:{minute:02d}:{(n * 13) % 60:02d} +0000"
        if kind == "access":
            path = ["/", "/shop/", "/blog/hello-world/", "/wp-login.php", "/cart/", "/favicon.ico", "/missing-page/"][n % 7]
            code = 404 if path == "/missing-page/" else (302 if path == "/wp-login.php" else 200)
            lines.append(f'203.0.113.{n % 200 + 20} - - [{stamp}] "GET {path} HTTP/1.1" {code} {1200 + n * 37} "-" "Mozilla/5.0"')
        else:
            kinds = ["[notice] cache warmed", "[warn] PHP Warning: Undefined variable $total in public_html/cart.php on line 42",
                     "[error] File does not exist: public_html/missing-page", "[notice] cron job finished"]
            lines.append(f"[{stamp}] {kinds[n % 4]} (sample entry for {domain})")
    return "\n".join(lines) + "\n"


class DemoLogs:
    def __init__(self, sandbox, domain):
        self.sandbox = sandbox
        self.domain = domain

    def _path(self, kind):
        if kind not in LOG_KINDS:
            raise HostingError("Choose the error log or the access log.")
        target = self.sandbox.resolve(LOG_KINDS[kind], must_exist=False)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_sample_log(kind, self.domain), encoding="utf-8")
        return target

    def read(self, kind, lines, query):
        path = self._path(kind)
        if lines not in LOG_LINE_CHOICES:
            raise HostingError("Choose how many lines to show.")
        text = path.read_bytes()[-MAX_LOG_READ:].decode("utf-8", errors="replace")
        rows = [r for r in text.splitlines() if r.strip()]
        query = (query or "").strip()[:100]
        if query:
            rows = [r for r in rows if query.lower() in r.lower()]
        return LogView(kind=kind, lines=rows[-lines:], total=len(rows), size_bytes=path.stat().st_size)

    def clear(self, kind):
        self._path(kind).write_text("", encoding="utf-8")


class _Store:
    def __init__(self, directory, name):
        self.file = Path(directory) / name

    def load(self):
        try:
            return json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def save(self, rows):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(rows), encoding="utf-8")


class DemoFtp:
    def __init__(self, directory, domain, folder_exists):
        self.store = _Store(directory, "ftp.json")
        self.domain = domain
        self.folder_exists = folder_exists  # callback: is this relative folder inside the account and present?

    def options(self):
        return FtpOptions(host=f"ftp.{self.domain}", port=21, secure_port=22, max_accounts=MAX_FTP, root="")

    def accounts(self):
        return [FtpAccount(login=r["login"], directory=r["directory"], created_at=r["created_at"]) for r in self.store.load()]

    def create(self, name, password, directory):
        name = (name or "").strip().lower()
        if not FTP_NAME.match(name):
            raise HostingError("The login name uses 2 to 32 lowercase letters, numbers, dots, dashes or underscores.")
        directory = (directory or "").strip().strip("/")
        if directory and not self.folder_exists(directory):
            raise HostingError("That folder does not exist in your hosting space. Create it in the file manager first.")
        password = check_ftp_password(password)
        rows = self.store.load()
        login = f"{name}@{self.domain}"
        if any(r["login"] == login for r in rows):
            raise HostingError("That FTP login already exists.")
        if len(rows) >= MAX_FTP:
            raise HostingError(f"Your plan allows {MAX_FTP} FTP accounts. Delete one to add another.")
        rows.append({"login": login, "directory": directory, "password_hash": make_password(password), "created_at": _now()})
        self.store.save(rows)
        return login

    def set_password(self, login, password):
        rows = self.store.load()
        row = next((r for r in rows if r["login"] == login), None)
        if row is None:
            raise HostingError("That FTP account no longer exists.")
        row["password_hash"] = make_password(check_ftp_password(password))
        self.store.save(rows)

    def delete(self, login):
        rows = self.store.load()
        if not any(r["login"] == login for r in rows):
            raise HostingError("That FTP account no longer exists.")
        self.store.save([r for r in rows if r["login"] != login])


class DemoAccess:
    def __init__(self, directory):
        self.store = _Store(directory, "ip_rules.json")

    def rules(self):
        return [IpRule(rule_id=r["id"], network=r["network"], note=r["note"], created_at=r["created_at"]) for r in self.store.load()]

    @staticmethod
    def tidy(text):
        try:
            net = ipaddress.ip_network((text or "").strip(), strict=False)
        except ValueError:
            raise HostingError("Enter an IP address such as 203.0.113.7 or a range such as 203.0.113.0/24.")
        floor = 16 if net.version == 4 else 48
        if net.prefixlen < floor:
            raise HostingError(f"That range is too wide to be a sensible block. Use a range of /{floor} or narrower.")
        if net.is_loopback or net.is_unspecified or net.is_multicast:
            raise HostingError("That address cannot be blocked.")
        return str(net.network_address) if net.num_addresses == 1 else str(net)

    def add(self, network, note):
        network = self.tidy(network)
        rows = self.store.load()
        if any(r["network"] == network for r in rows):
            raise HostingError("That address or range is already blocked.")
        if len(rows) >= MAX_RULES:
            raise HostingError(f"You can block up to {MAX_RULES} addresses or ranges. Remove one to add another.")
        rule = {"id": uuid.uuid4().hex[:10], "network": network, "note": (note or "").strip()[:100], "created_at": _now()}
        rows.append(rule)
        self.store.save(rows)
        return rule["id"]

    def delete(self, rule_id):
        rows = self.store.load()
        if not any(r["id"] == rule_id for r in rows):
            raise HostingError("That block no longer exists.")
        self.store.save([r for r in rows if r["id"] != rule_id])
